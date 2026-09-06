from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from app.core.config import settings


def test_public_player_endpoint_contract_and_invalid_media(monkeypatch):
    monkeypatch.setattr(settings, "API_KEYS", {"test": "test-key"})
    from app.main import app

    client = TestClient(app, base_url="https://api.example.test:8443")
    for provider, media_id in [("youtube", "vbBd_Hu6o2M"), ("twitch", "ExampleClip-123")]:
        response = client.get(f"/media/{provider}/{media_id}")
        assert response.status_code == 200
        iframe = BeautifulSoup(response.text, "html.parser").find("iframe")
        assert iframe is not None
        src = iframe["src"]
        assert isinstance(src, str)
        source = urlsplit(src)
        query = parse_qs(source.query)
        if provider == "youtube":
            assert source.hostname == "www.youtube.com"
            assert source.path == "/embed/vbBd_Hu6o2M"
            assert query == {"playsinline": ["1"], "autoplay": ["0"]}
        else:
            assert source.hostname == "clips.twitch.tv"
            assert query == {"clip": ["ExampleClip-123"], "parent": ["api.example.test"], "autoplay": ["false"]}
        minimum = "min-width:200px;min-height:200px" if provider == "youtube" else "min-width:400px;min-height:300px"
        assert minimum in response.text
        assert iframe.has_attr("allowfullscreen")
        assert iframe["referrerpolicy"] == "strict-origin-when-cross-origin"
        assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
        assert "default-src 'none'" in response.headers["content-security-policy"]
    response = client.get("/media/twitch/%22%3E%3Cscript%3E")
    assert response.status_code == 404
    assert "<script>" not in response.text
