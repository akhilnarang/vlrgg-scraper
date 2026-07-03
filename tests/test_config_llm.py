from app.core.config import settings


def test_llm_settings_have_expected_defaults():
    assert settings.LLM_MODEL == "gpt-5.4"
    assert settings.LLM_REASONING_EFFORT == "low"
    assert settings.LLM_DEBUG is False
    assert settings.LLM_MAX_STEPS == 10
    assert settings.LLM_RATE_LIMIT_ENABLED is False
    assert settings.LLM_RATE_LIMIT == 5
    assert settings.LLM_RATE_LIMIT_WINDOW == 60
    # base url + key default to None unless provided by env/.env
    assert hasattr(settings, "LLM_BASE_URL")
