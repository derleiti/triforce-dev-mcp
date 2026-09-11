from pathlib import Path

from app.services import google_genai


def test_canonical_gemini_key_wins(monkeypatch):
    monkeypatch.delenv("GOOGLE_GEMINI_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_AI_STUDIO_KEY", "canonical")
    monkeypatch.setenv("GEMINI_API_KEY", "legacy")
    monkeypatch.setenv("GOOGLE_API_KEY", "unrelated")
    assert google_genai.resolve_api_key() == "canonical"
    assert google_genai.configured_key_names() == ("GOOGLE_AI_STUDIO_KEY", "GEMINI_API_KEY")


def test_text_contents_uses_native_system_instruction():
    system, contents = google_genai.text_contents([
        {"role": "system", "content": "Be precise"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ])
    assert system == "Be precise"
    assert [item.role for item in contents] == ["user", "model"]
    assert contents[0].parts[0].text == "hello"


def test_production_code_no_longer_imports_legacy_google_sdk():
    root = Path(__file__).parents[1]
    offenders = []
    for path in (root / "app").rglob("*.py"):
        if "google.generativeai" in path.read_text(errors="ignore"):
            offenders.append(str(path.relative_to(root)))
    assert offenders == []


def test_requirements_use_supported_google_genai_sdk():
    root = Path(__file__).parents[1]
    requirements = (root / "requirements.txt").read_text()
    lock = (root / "requirements-lock-2.85-beta2.txt").read_text()
    assert "google-genai==2.18.1" in requirements
    assert "google-generativeai" not in requirements
    assert "google-genai==2.18.1" in lock
    assert "google-generativeai" not in lock


def test_ai_studio_key_is_canonical_for_gemini_and_explicit_resolver(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "legacy")
    monkeypatch.setenv("GOOGLE_AI_STUDIO_KEY", "studio")
    assert google_genai.resolve_api_key() == "studio"
    assert google_genai.resolve_ai_studio_key() == "studio"


def test_generic_gemini_paths_do_not_use_ai_studio_media_key():
    root = Path(__file__).parents[1]
    provider = (root / "app/services/provider_chat.py").read_text()
    router = (root / "app/services/chat_router.py").read_text()
    assert 'resolve_api_key()' in provider
    assert '("GEMINI_API_KEY", "GOOGLE_GEMINI_KEY")' in router
    assert '("GEMINI_API_KEY", "GOOGLE_AI_STUDIO_KEY")' not in router


def test_triforce_keeps_manual_tool_execution_ownership():
    root = Path(__file__).parents[1]
    text = (root / "app/services/gemini_access.py").read_text()
    assert "AutomaticFunctionCallingConfig(disable=True)" in text
