"""Convert an article body to ordered content without flattening its structure."""

import re
from urllib.parse import urljoin

from bs4 import Comment, NavigableString, Tag

from app.constants import PREFIX
from app.schemas.news import NewsBlock, NewsBlockType, NewsTextRun

_SKIP_TAGS = {"script", "style", "noscript", "template"}
_CONTAINERS = {"div", "section", "article", "main", "figure", "header", "footer"}
_HEADINGS = {f"h{level}" for level in range(1, 7)}


def _url(value: object) -> str | None:
    return urljoin(PREFIX + "/", str(value)) if value else None


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
    text = "".join(run.text for run in runs)
    remove: set[int] = set()
    for match in re.finditer(r" +(?=[.,;:!?])", text):
        remove.update(range(match.start(), match.end()))

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
        opening = re.search(r'(["“]) *$', text[:core_start])
        closing = re.match(r' *(["”])', text[core_end:])
        if opening and closing and (opening[1], closing[1]) in {('"', '"'), ("“", "”")}:
            remove.update(range(opening.start() + 1, core_start))
            remove.update(range(core_end, core_end + closing.end() - 1))
        if possessive := re.match(r" +(?=['’]s\b)", text[core_end:]):
            remove.update(range(core_end, core_end + possessive.end()))

    offset = 0
    result = []
    for run in runs:
        normalized = "".join(char for index, char in enumerate(run.text, offset) if index not in remove)
        offset += len(run.text)
        if normalized:
            result.append(run.model_copy(update={"text": normalized}))
    return result


def parse_article_blocks(body: Tag) -> list[NewsBlock]:
    """Walk each DOM node once, retaining inline styles and block boundaries."""

    def flow(
        parent: Tag,
        kind: NewsBlockType = "paragraph",
        level: int | None = None,
        bold: bool = False,
        italic: bool = False,
        url: str | None = None,
    ) -> list[NewsBlock]:
        blocks: list[NewsBlock] = []
        pending: list[NewsTextRun] = []

        def flush() -> None:
            nonlocal pending
            runs = _clean_runs(pending)
            pending = []
            if runs:
                # VLR writes photo captions as italic text after an image in a paragraph.
                is_caption = (
                    parent.name == "p"
                    and blocks
                    and blocks[-1].type == "image"
                    and all(run.italic for run in runs if run.text.strip())
                )
                blocks.append(NewsBlock(type="caption" if is_caption else kind, runs=runs, level=level))

        def visit(node: object, bold: bool, italic: bool, url: str | None) -> None:
            if isinstance(node, Comment):
                return
            if isinstance(node, NavigableString):
                pending.append(NewsTextRun(text=re.sub(r"\s+", " ", str(node)), url=url, bold=bold, italic=italic))
                return
            if not isinstance(node, Tag):
                return
            if node.name in _SKIP_TAGS or "wf-hover-card" in (node.get("class") or []):
                return
            bold = bold or node.name in {"b", "strong"}
            italic = italic or node.name in {"i", "em"}
            if node.name == "br":
                pending.append(NewsTextRun(text="\n", url=url, bold=bold, italic=italic))
                return
            if node.name in {"img", "iframe", "video"}:
                source = node.get("src")
                if not source and node.name == "video":
                    source_tag = node.find("source", src=True)
                    source = source_tag.get("src") if source_tag else None
                if source:
                    flush()
                    blocks.append(
                        NewsBlock(
                            type="image" if node.name == "img" else "video",
                            url=_url(source),
                            alt=str(node.get("alt", "")) if node.name == "img" else "",
                        )
                    )
                return
            if node.name in {"blockquote", "ul", "ol", "li"}:
                flush()
                children = flow(node, bold=bold, italic=italic, url=url)
                if children:
                    node_kind: NewsBlockType = (
                        "list" if node.name in {"ul", "ol"} else "list_item" if node.name == "li" else "blockquote"
                    )
                    try:
                        start = int(str(node.get("start", "1")))
                    except ValueError:
                        start = 1
                    blocks.append(NewsBlock(type=node_kind, children=children, ordered=node.name == "ol", start=start))
                return
            if node.name in _CONTAINERS or node.name in _HEADINGS or node.name in {"p", "figcaption"}:
                flush()
                node_kind = (
                    "heading" if node.name in _HEADINGS else "caption" if node.name == "figcaption" else "paragraph"
                )
                node_level = int(node.name[1]) if node.name in _HEADINGS else None
                blocks.extend(flow(node, node_kind, node_level, bold, italic, url))
                return
            if node.name == "a":
                url = _url(node.get("href")) or url
            for child in node.children:
                visit(child, bold, italic, url)

        for child in parent.children:
            visit(child, bold, italic, url)
        flush()
        return blocks

    return flow(body)


def legacy_article_content(blocks: list[NewsBlock]) -> tuple[str, list[dict[str, str]], list[str], list[str]]:
    """Project the ordered blocks onto the original placeholder-based fields."""
    links: list[dict[str, str]] = []
    images: list[str] = []
    videos: list[str] = []

    def inline(runs: list[NewsTextRun]) -> str:
        parts: list[str] = []
        index = 0
        while index < len(runs):
            run = runs[index]
            text = run.text
            index += 1
            if run.url:
                while index < len(runs) and runs[index].url == run.url:
                    text += runs[index].text
                    index += 1
                leading = text[: len(text) - len(text.lstrip())]
                trailing = text[len(text.rstrip()) :]
                if text.strip():
                    parts.append(f"{leading}{{{{link_{len(links)}}}}}{trailing}")
                    links.append({"text": text.strip(), "url": run.url})
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

    return "\n\n".join(render(block) for block in blocks).strip(), links, images, videos
