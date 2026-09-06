import re
from pathlib import Path
from unittest.mock import patch

import pytest

from app import constants
from app.exceptions import ScrapingError
from app.services import news

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
async def test_news_list_returns_full_history_in_order(http_get):
    pages = {
        constants.NEWS_URL: (FIXTURE_DIR / "news_page1.html").read_bytes(),
        news.news_url(2): (FIXTURE_DIR / "news_page2.html").read_bytes(),
    }
    with patch(
        "httpx.AsyncClient.get",
        side_effect=http_get(pages, fallback=(FIXTURE_DIR / "news_empty.html").read_bytes()),
    ):
        result = await news.news_list(pages=0)

    assert len(result) == 60
    assert len({item.url for item in result}) == 60
    assert result[0].title
    assert result[30].title


@pytest.mark.asyncio
async def test_news_list_does_not_return_partial_results(http_get):
    pages = {constants.NEWS_URL: (FIXTURE_DIR / "news_page1.html").read_bytes()}
    failures = {news.news_url(2): 500}

    with patch("httpx.AsyncClient.get", side_effect=http_get(pages, failures=failures)), pytest.raises(ScrapingError):
        await news.news_list(pages=2)


@pytest.mark.asyncio
@pytest.mark.parametrize("article_id", ["562934", "750541", "748106", "750321"])
async def test_news_article_preserves_links_and_quoted_names(http_response, article_id):
    response = http_response(
        f"https://www.vlr.gg/{article_id}",
        (FIXTURE_DIR / f"news_{article_id}.html").read_bytes(),
    )

    with patch("httpx.AsyncClient.get", return_value=response):
        result = await news.news_by_id(article_id)

    text = re.sub(r"\{\{link_(\d+)\}\}", lambda m: result.links[int(m[1])]["text"], result.content)
    if article_id == "562934":
        assert result.title == "EDward Gaming bids farewell to head coach Muggle"
        assert 'Tang "{{link_2}}" Shijun' in result.content
        assert len(result.links) == 21
        assert len(result.images) == 1
        assert result.author == "raezeri"
        assert text.index("{image_0}") < text.index("One of the first from China") < text.index("2025 was")
        assert result.blocks[-1].type == "list"
        assert len(result.blocks[-1].children) == 9
    elif article_id == "750541":
        assert text.index("2028 season.") < text.index("{image_0}") < text.index("koshmaras getting ready")
        assert [b.type for b in result.blocks[:4]] == ["paragraph", "paragraph", "image", "caption"]
        assert any(r.italic for r in result.blocks[3].runs)
        assert result.blocks[-1].type == "list"
        assert len(result.blocks[-1].children) == 9
    elif article_id == "748106":
        assert text.index("{video_0}") < text.index("N4RRATE, it's been a year")
        assert [b.type for b in result.blocks[:4]] == ["paragraph", "video", "paragraph", "blockquote"]
        player = result.blocks[1].player
        assert player is not None
        assert player.model_dump(mode="json") == {
            "provider": "youtube",
            "media_id": "vbBd_Hu6o2M",
            "player_url": "/media/youtube/vbBd_Hu6o2M",
            "external_url": "https://www.youtube.com/watch?v=vbBd_Hu6o2M",
        }
        assert all(r.italic for r in result.blocks[0].runs)
        assert all(r.bold for r in result.blocks[2].runs)
        assert len([b for b in result.blocks if b.type == "blockquote"]) == 6
        assert text.count("It's definitely been like a bit of a roller coaster") == 1
    else:
        headings = [b for b in result.blocks if b.type == "heading"]
        assert ["".join(r.text for r in h.runs) for h in headings] == [
            "Nongshim continues to roll, downs Global Esports 2-1",
            "T1 derails the VARREL roll, seals Champions Shanghai spot with 2-0 win",
            "Up next",
        ]
        assert all(h.level == 1 for h in headings)
        assert text.index("{video_0}") < text.index("Dambi with a Dambi-esque")
        assert text.index("{video_1}") < text.index("Meteor finished the series")
        assert text.count("Up next") == 1
        videos = [b for b in result.blocks if b.type == "video"]
        assert all(b.player is not None and b.player.provider == "twitch" for b in videos)
        player = videos[0].player
        assert player is not None
        assert player.media_id == "ExquisiteRealSandpiperBabyRage-31jlkIQddWpcEqu0"
        assert player.external_url == "https://clips.twitch.tv/ExquisiteRealSandpiperBabyRage-31jlkIQddWpcEqu0"
        video_indexes = [index for index, block in enumerate(result.blocks) if block.type == "video"]
        assert [result.blocks[index + 1].type for index in video_indexes] == ["caption", "caption"]
        assert all(all(run.italic for run in result.blocks[index + 1].runs) for index in video_indexes)

    assert result.blocks
    assert "{{image_" not in result.content
    assert "{{video_" not in result.content
    assert len(re.findall(r"\{image_\d+\}", result.content)) == len(result.images)
    assert len(re.findall(r"\{video_\d+\}", result.content)) == len(result.videos)


@pytest.mark.asyncio
async def test_news_article_fallback_preserves_nested_content_once(http_response):
    response = http_response(
        "https://www.vlr.gg/1",
        b"""
        <article>
          <header>
            <h1>Fallback article</h1>
            <div class="meta"><span class="author">by Author</span><time>2026-01-01</time></div>
          </header>
          <h2>Body heading</h2>
          <p>Hello <strong>!</strong>
            \xe2\x80\x9c <a href="/player/1"><strong>bold<em>both</em></strong></a> \xe2\x80\x9d joined;
            <a href="/player/2">Player</a> 's match.<br>"<br><a href="/player/3">next</a>"
            <a href="javascript:alert(1)">unsafe</a></p>
          <p>First paragraph</p><p>! Second paragraph</p>
          <ol start="3"><li>Outer<ul><li>Inner</li></ul>Tail</li><li>Second</li></ol>
          <figure><a href="/photo"><img src="//owcdn.net/photo.jpg" alt="Winner"></a>
            <figcaption>Photo <em>credit</em></figcaption></figure>
          <a href="/clip"><video><source src="/clip.mp4"></video></a>
          <img src="javascript:alert(2)"><p>After video</p>
          <script>hidden script</script><!-- hidden comment -->
        </article>""",
    )
    with patch("httpx.AsyncClient.get", return_value=response):
        result = await news.news_by_id("1")

    assert result.title == "Fallback article"
    assert result.author == "Author"
    assert result.date is not None
    assert [b.type for b in result.blocks] == [
        "heading",
        "paragraph",
        "paragraph",
        "paragraph",
        "list",
        "image",
        "caption",
        "video",
        "paragraph",
    ]
    heading, paragraph, first, second, ordered, image, caption, video, after = result.blocks
    assert "".join(r.text for r in heading.runs) == "Body heading"
    assert heading.level == 2
    assert "".join(r.text for r in paragraph.runs) == 'Hello! “boldboth” joined; Player\'s match.\n"\nnext" unsafe'
    linked = [r for r in paragraph.runs if r.url]
    assert [(r.text, r.bold, r.italic, r.url) for r in linked] == [
        ("bold", True, False, "https://www.vlr.gg/player/1"),
        ("both", True, True, "https://www.vlr.gg/player/1"),
        ("Player", False, False, "https://www.vlr.gg/player/2"),
        ("next", False, False, "https://www.vlr.gg/player/3"),
    ]
    assert "".join(r.text for r in first.runs) == "First paragraph"
    assert "".join(r.text for r in second.runs) == "! Second paragraph"
    assert ordered.ordered and ordered.start == 3
    assert [b.type for b in ordered.children[0].children] == ["paragraph", "list", "paragraph"]
    assert len(ordered.children) == 2
    assert (image.url, image.link_url, image.alt) == (
        "https://owcdn.net/photo.jpg",
        "https://www.vlr.gg/photo",
        "Winner",
    )
    assert caption.runs[-1].italic
    assert (video.url, video.link_url) == ("https://www.vlr.gg/clip.mp4", "https://www.vlr.gg/clip")
    assert video.player is None
    assert "".join(r.text for r in after.runs) == "After video"
    assert result.content == (
        "Body heading\n\n"
        'Hello! “{{link_0}}” joined; {{link_1}}\'s match.\n"\n{{link_2}}" unsafe\n\n'
        "First paragraph\n\n! Second paragraph\n\n"
        "3. Outer\n  \n  - Inner\n  \n  Tail\n4. Second\n\n"
        "{image_0}\n\nPhoto credit\n\n{video_0}\n\nAfter video"
    )
    assert result.links == [
        {"text": "boldboth", "url": "https://www.vlr.gg/player/1"},
        {"text": "Player", "url": "https://www.vlr.gg/player/2"},
        {"text": "next", "url": "https://www.vlr.gg/player/3"},
    ]
    assert result.images == ["https://owcdn.net/photo.jpg"]
    assert result.videos == ["https://www.vlr.gg/clip.mp4"]
    assert "javascript:" not in result.model_dump_json()
