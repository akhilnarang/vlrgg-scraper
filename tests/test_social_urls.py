"""Tests for social-link classification.

Twitter rebranded to x.com. Matching the literal string "twitter.com" silently
misses every link on the new domain, which is what left `twitter` null on every
team and player while the X link was misfiled as the team's website.
"""

import pytest

from app.utils import is_social_url, is_twitter_url, twitter_profile_url


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/Sentinels",
        "https://www.x.com/Sentinels",
        "http://twitter.com/Sentinels",
        "https://www.twitter.com/Sentinels",
        "https://mobile.twitter.com/Sentinels",
    ],
)
def test_twitter_urls_recognized(url):
    assert is_twitter_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://sentinels.gg/",
        "https://pprx.team/",
        "https://fb.com/pprxteam",
        "https://www.twitch.tv/sick_cs",
        # Domains that merely contain the substring must not match.
        "https://nitter.net/x.com.fake",
        "https://notx.com/foo",
        "https://twitter.com.evil.example/foo",
    ],
)
def test_non_twitter_urls_rejected(url):
    assert not is_twitter_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/a",
        "https://fb.com/a",
        "https://facebook.com/a",
        "https://instagram.com/a",
        "https://tiktok.com/a",
    ],
)
def test_social_urls_recognized(url):
    assert is_social_url(url)


@pytest.mark.parametrize("url", ["https://sentinels.gg/", "https://pprx.team/", "http://www.edgteam.cn/"])
def test_own_sites_are_not_social(url):
    assert not is_social_url(url)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # VLR shows the handle as link text; a bare handle is not a URL, and
        # expanding it naively produced `https://@SicK_cs`.
        ("@SicK_cs", "https://x.com/SicK_cs"),
        ("SicK_cs", "https://x.com/SicK_cs"),
        ("  @zmjjkk  ", "https://x.com/zmjjkk"),
        ("https://x.com/unfakeo", "https://x.com/unfakeo"),
        ("https://twitter.com/TenZ", "https://twitter.com/TenZ"),
    ],
)
def test_twitter_profile_url_normalizes(value, expected):
    assert twitter_profile_url(value) == expected


@pytest.mark.parametrize("value", ["", "   ", "@", "https://twitch.tv/someone"])
def test_twitter_profile_url_rejects_junk(value):
    assert twitter_profile_url(value) is None
