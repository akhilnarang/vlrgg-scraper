"""Localized display strings, resolved per request from ``Accept-Language`` (English when absent/unknown).

Locales are flat ``namespace.value -> label`` JSON files in ``app/locales``, keyed by the stable wire value
of the existing field. Schemas expose ``*_label`` computed fields that call ``label()`` at serialization
time, so cached English JSON is localized on read and existing fields never change.
"""

import json
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from pathlib import Path
from typing import NamedTuple

from fastapi import Request, Response

DEFAULT = "en"
LOCALES: dict[str, dict[str, str]] = {
    path.stem: json.loads(path.read_text(encoding="utf-8"))
    for path in (Path(__file__).parent / "locales").glob("*.json")
}
# "pt-br" -> "pt-BR" plus base-language fallbacks ("pt" -> "pt-BR"); first locale alphabetically wins a base.
_BY_TAG: dict[str, str] = {tag.lower(): tag for tag in LOCALES}
for _tag in sorted(LOCALES):
    _BY_TAG.setdefault(_tag.split("-")[0].lower(), _tag)

request_lang: ContextVar[str] = ContextVar("request_lang", default=DEFAULT)


class _AcceptedTag(NamedTuple):
    """One ``Accept-Language`` entry; sorts by quality (desc) then header order."""

    quality: float
    index: int
    tag: str

    @classmethod
    def parse(cls, index: int, part: str) -> _AcceptedTag | None:
        tag, _, params = part.strip().partition(";")
        if not (tag := tag.strip()):
            return None
        quality = 1.0
        for param in params.split(";"):
            key, _, value = param.strip().partition("=")
            if key.strip().lower() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
                if not 0.0 <= quality <= 1.0:  # RFC 9110 qvalue range; also rejects NaN
                    quality = 0.0
        return cls(quality, index, tag.lower())

    def sort_key(self) -> tuple[float, int]:
        return (-self.quality, self.index)


def resolve(accept_language: str | None) -> str:
    """Best supported locale for an ``Accept-Language`` header: exact tag, then base language, else English."""
    accepted = [
        parsed
        for index, part in enumerate((accept_language or "").split(","))
        if (parsed := _AcceptedTag.parse(index, part))
    ]
    for entry in sorted(accepted, key=_AcceptedTag.sort_key):
        if entry.quality <= 0:  # RFC 9110: q=0 means "not acceptable"
            continue
        if hit := _BY_TAG.get(entry.tag) or _BY_TAG.get(entry.tag.split("-")[0]):
            return hit
    return DEFAULT


def label(namespace: str, value: object) -> str:
    """Localized label for ``namespace.value`` in the request language.

    ``value`` is the stable wire value of the existing field (enum member or legacy string). Falls back
    to the English label, then to the value itself, so an unmapped value is still displayable.
    """
    raw = str(getattr(value, "value", value))
    key = f"{namespace}.{raw}"
    lang = request_lang.get()
    return LOCALES.get(lang, {}).get(key) or LOCALES[DEFAULT].get(key) or raw


async def localize_response(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """HTTP middleware: pick the response language from Accept-Language for the ``*_label`` fields."""
    lang = resolve(request.headers.get("accept-language"))
    token = request_lang.set(lang)
    try:
        response = await call_next(request)
    finally:
        request_lang.reset(token)
    response.headers["Content-Language"] = lang
    # Append on the response path. The outer GZip adds Accept-Encoding after this.
    response.headers.add_vary_header("Accept-Language")
    return response
