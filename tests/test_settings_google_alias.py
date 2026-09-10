from app.config import Settings


def test_google_ai_studio_key_does_not_collide_with_gemini_alias():
    settings = Settings.model_validate(
        {"GOOGLE_AI_STUDIO_KEY": "working-key"},
        by_alias=True,
        by_name=True,
    )
    assert settings.google_ai_studio_key == "working-key"
    assert settings.gemini_api_key == "working-key"


def test_google_ai_studio_key_wins_over_legacy_gemini_key():
    settings = Settings.model_validate(
        {
            "GOOGLE_AI_STUDIO_KEY": "canonical",
            "GEMINI_API_KEY": "legacy",
        },
        by_alias=True,
        by_name=True,
    )
    assert settings.google_ai_studio_key == "canonical"
    assert settings.gemini_api_key == "canonical"
