from app.utils import is_social_url, is_twitter_url, twitter_profile_url


def test_social_links_are_classified_by_host():
    assert is_twitter_url("https://x.com/Sentinels")
    assert is_twitter_url("https://twitter.com/Sentinels")
    assert is_social_url("https://instagram.com/Sentinels")
    assert not is_twitter_url("https://twitter.com.evil.example/Sentinels")
    assert not is_social_url("https://sentinels.gg/")


def test_twitter_profiles_normalize_handles_and_reject_other_sites():
    assert twitter_profile_url("  @SicK_cs  ") == "https://x.com/SicK_cs"
    assert twitter_profile_url("https://x.com/unfakeo") == "https://x.com/unfakeo"
    assert twitter_profile_url("https://twitch.tv/someone") is None
