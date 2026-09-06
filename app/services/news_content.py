"""Convert an article body to ordered content without flattening its structure."""

import itertools
import re
from collections.abc import Iterator
from typing import NamedTuple

from bs4 import Comment, NavigableString, Tag

from app.schemas.news import NewsBlock, NewsBlockType, NewsTextRun
from app.services.news_video import news_video_player
from app.utils import resolve_http_url

_SKIP_TAGS = {"script", "style", "noscript", "template"}
_CONTAINERS = {"div", "section", "article", "main", "figure", "header", "footer"}
_HEADINGS = {f"h{level}" for level in range(1, 7)}
_NESTED_BLOCKS: dict[str, NewsBlockType] = {
    "blockquote": "blockquote",
    "ul": "list",
    "ol": "list",
    "li": "list_item",
}
_CLOSING_QUOTE = re.compile(r' *(["”])')
_POSSESSIVE_SPACE = re.compile(r" +(?=['’]s\b)")


class LegacyArticleFields(NamedTuple):
    """Placeholder-based article fields retained for backward compatibility."""

    content: str
    links: list[dict[str, str]]
    images: list[str]
    videos: list[str]


def _clean_runs(runs: list[NewsTextRun]) -> list[NewsTextRun]:
    cleaned: list[NewsTextRun] = []
    for run in runs:
        text = run.text
        if not cleaned:
            text = text.lstrip(" \n")
        elif cleaned[-1].text.endswith((" ", "\n")):
            text = text.lstrip(" ")
        if text.startswith("\n") and cleaned:
            cleaned[-1].text = cleaned[-1].text.rstrip(" ")
        if not text:
            continue
        if cleaned and (cleaned[-1].url, cleaned[-1].bold, cleaned[-1].italic) == (run.url, run.bold, run.italic):
            cleaned[-1].text += text
        else:
            cleaned.append(run.model_copy(update={"text": text}))
    while cleaned:
        cleaned[-1].text = cleaned[-1].text.rstrip(" \n")
        if cleaned[-1].text:
            break
        cleaned.pop()
    return _normalize_run_spacing(cleaned)


def _normalize_run_spacing(runs: list[NewsTextRun]) -> list[NewsTextRun]:
    """Normalize inline spacing without rescanning prefixes for each link.

    :param runs: Cleaned runs from one article block.
    :return: Runs with normalized spacing and retained link/style metadata.
    """
    if not runs:
        return runs
    text = "".join(run.text for run in runs)
    remove: set[int] = set()
    for match in re.finditer(r" +(?=[.,;:!?])", text):
        remove.update(range(match.start(), match.end()))
    opening_quotes = {match.end(): match for match in re.finditer(r'(["“]) *', text)}

    offset = 0
    index = 0
    while index < len(runs):
        run = runs[index]
        start = offset
        offset += len(run.text)
        index += 1
        if not run.url:
            continue
        while index < len(runs) and runs[index].url == run.url:
            offset += len(runs[index].text)
            index += 1
        linked_text = text[start:offset]
        core_start = start + len(linked_text) - len(linked_text.lstrip(" "))
        core_end = offset - len(linked_text) + len(linked_text.rstrip(" "))
        opening = opening_quotes.get(core_start)
        closing = _CLOSING_QUOTE.match(text, core_end)
        if opening and closing and (opening[1], closing[1]) in {('"', '"'), ("“", "”")}:
            remove.update(range(opening.start() + 1, core_start))
            remove.update(range(core_end, closing.end() - 1))
        if possessive := _POSSESSIVE_SPACE.match(text, core_end):
            remove.update(range(core_end, possessive.end()))

    if not remove:
        return runs

    offset = 0
    result = []
    for run in runs:
        normalized = "".join(char for index, char in enumerate(run.text, offset) if index not in remove)
        offset += len(run.text)
        if normalized:
            result.append(run.model_copy(update={"text": normalized}))
    return result


def parse_article_blocks(
    body: Tag,
    kind: NewsBlockType = "paragraph",
    level: int | None = None,
    bold: bool = False,
    italic: bool = False,
    url: str | None = None,
) -> list[NewsBlock]:
    """Parse supported article tags into ordered content blocks.

    Media blocks keep their source in ``url`` and any wrapping anchor in the
    distinct ``link_url`` field. Optional arguments carry parsing state through
    recursive calls.

    :param body: Container whose children are read in document order.
    :param kind: Block type for text directly inside this container.
    :param level: Heading level for text blocks, if applicable.
    :param bold: Inherited bold formatting.
    :param italic: Inherited italic formatting.
    :param url: Inherited anchor destination.
    :return: Blocks assembled from text groups and nested content.
    """
    blocks: list[NewsBlock] = []
    items = (item for child in body.children for item in _read_node(child, bold, italic, url))
    grouped_items = itertools.groupby(items, key=lambda item: isinstance(item, NewsTextRun))
    for is_text, group in grouped_items:
        if not is_text:
            blocks.extend(item for item in group if isinstance(item, NewsBlock))
            continue
        runs = _clean_runs([item for item in group if isinstance(item, NewsTextRun)])
        if runs:
            # VLR writes media captions as italic text immediately after the media.
            is_caption = (
                blocks and blocks[-1].type in {"image", "video"} and all(run.italic for run in runs if run.text.strip())
            )
            blocks.append(NewsBlock(type="caption" if is_caption else kind, runs=runs, level=level))
    return blocks


def _read_node(node: object, bold: bool, italic: bool, url: str | None) -> Iterator[NewsTextRun | NewsBlock | None]:
    """Read a node as text segments, completed blocks, or block boundaries.

    :param node: The BeautifulSoup node to read.
    :param bold: Inherited bold formatting.
    :param italic: Inherited italic formatting.
    :param url: Inherited anchor destination, distinct from a media source.
    :yield: Text runs, blocks, or ``None`` to separate text around an empty block.
    """
    if isinstance(node, Comment):
        return
    if isinstance(node, NavigableString):
        yield NewsTextRun(text=re.sub(r"\s+", " ", str(node)), url=url, bold=bold, italic=italic)
        return
    if not isinstance(node, Tag):
        return
    if node.name in _SKIP_TAGS or "wf-hover-card" in (node.get("class") or []):
        return
    bold = bold or node.name in {"b", "strong"}
    italic = italic or node.name in {"i", "em"}
    if node.name == "br":
        yield NewsTextRun(text="\n", url=url, bold=bold, italic=italic)
        return
    if node.name in {"img", "iframe", "video"}:
        source = node.get("src")
        if not source and node.name == "video":
            source_tag = node.find("source", src=True)
            source = source_tag.get("src") if source_tag else None
        if media_url := resolve_http_url(source):
            yield NewsBlock(
                type="image" if node.name == "img" else "video",
                url=media_url,
                link_url=url,
                player=news_video_player(media_url) if node.name != "img" else None,
                alt=str(node.get("alt", "")) if node.name == "img" else "",
            )
        return
    if node.name in _NESTED_BLOCKS:
        yield None
        if children := parse_article_blocks(node, bold=bold, italic=italic, url=url):
            try:
                start = int(str(node.get("start", "1")))
            except ValueError:
                start = 1
            yield NewsBlock(type=_NESTED_BLOCKS[node.name], children=children, ordered=node.name == "ol", start=start)
        return
    if node.name in _CONTAINERS or node.name in _HEADINGS or node.name in {"p", "figcaption"}:
        yield None
        kind: NewsBlockType = (
            "heading" if node.name in _HEADINGS else "caption" if node.name == "figcaption" else "paragraph"
        )
        level = int(node.name[1]) if node.name in _HEADINGS else None
        yield from parse_article_blocks(node, kind, level, bold, italic, url)
        return
    if node.name == "a":
        url = resolve_http_url(node.get("href"))
    for child in node.children:
        yield from _read_node(child, bold, italic, url)


def project_legacy_fields(blocks: list[NewsBlock]) -> LegacyArticleFields:
    """Project ordered blocks onto the original placeholder-based fields.

    Linked text becomes ``{{link_N}}`` and media becomes ``{image_N}`` or
    ``{video_N}``, with corresponding values stored in separate lists.

    :param blocks: Ordered article blocks to flatten into legacy fields.
    :return: Legacy content, links, images, and videos with matching indexes.
    """
    links: list[dict[str, str]] = []
    images: list[str] = []
    videos: list[str] = []

    def inline(runs: list[NewsTextRun]) -> str:
        """Render text runs and collect linked text in the captured links list.

        :param runs: Inline text runs to render.
        :return: Text containing ``{{link_N}}`` placeholders for linked runs.
        """
        parts: list[str] = []
        for url, group in itertools.groupby(runs, key=lambda run: run.url):
            text = "".join(run.text for run in group)
            if url:
                leading = text[: len(text) - len(text.lstrip())]
                trailing = text[len(text.rstrip()) :]
                if text.strip():
                    parts.append(f"{leading}{{{{link_{len(links)}}}}}{trailing}")
                    links.append({"text": text.strip(), "url": url})
                else:
                    parts.append(text)
            else:
                parts.append(text)
        return "".join(parts)

    def render(block: NewsBlock) -> str:
        if block.type in {"image", "video"} and block.url:
            media = images if block.type == "image" else videos
            placeholder = f"{{{block.type}_{len(media)}}}"
            media.append(block.url)
            return placeholder
        if block.type == "list":
            items = []
            number = block.start
            for child in block.children:
                text = render(child)
                if child.type == "list_item":
                    prefix = f"{number}. " if block.ordered else "- "
                    items.append(prefix + text.replace("\n", "\n  "))
                    number += 1
                else:
                    items.append(text)
            return "\n".join(items)
        parts = [inline(block.runs)] if block.runs else []
        parts.extend(render(child) for child in block.children)
        return "\n\n".join(part for part in parts if part)

    return LegacyArticleFields(
        content="\n\n".join(render(block) for block in blocks).strip(),
        links=links,
        images=images,
        videos=videos,
    )
