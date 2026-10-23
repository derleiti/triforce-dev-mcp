"""Background-only account-auth retry regression tests."""
import asyncio
import subprocess
from pathlib import Path

import pytest

from app.services.tristar import idle_worker

def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    (root / "tracked.txt").write_text("base\n")
    subprocess.run(["git", "-C", str(root), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "init"], check=True)
    return root

def test_idle_auth_cooldown_skips_expired_background_profile(monkeypatch):
    models = {
        "codex-mcp": "account:chatgpt/gpt-5.6-terra",
        "claude-mcp": "account:claude/sonnet",
        "gemini-mcp": "account:gemini/gemini-3.8-flash-high",
        "opencode-mcp": "mistral/mistral-medium-latest",
    }
    now = [5000.0]
    monkeypatch.setattr(idle_worker, "_AUTH_DEFERRED", {})
    monkeypatch.setattr(idle_worker, "load_profile", lambda name: {"model": models[name]})
    monkeypatch.setattr(idle_worker.time, "monotonic", lambda: now[0])
    monkeypatch.setenv("TRISTAR_IDLE_AUTH_RETRY_SECONDS", "3600")
    assert idle_worker._automatic_profile_candidates("opencode-mcp")[0] == "opencode-mcp"
    idle_worker._remember_idle_auth_issue("opencode-mcp", models["opencode-mcp"],
                                         "Token expired. Please re-login")
    assert "opencode-mcp" not in idle_worker._automatic_profile_candidates("opencode-mcp")
    assert idle_worker._automatic_profile_candidates("opencode-mcp")[0] == "codex-mcp"
    now[0] += 3601
    assert idle_worker._automatic_profile_candidates("opencode-mcp")[0] == "opencode-mcp"


def test_idle_auth_cache_clears_when_profile_model_changes(monkeypatch):
    monkeypatch.setattr(idle_worker, "_AUTH_DEFERRED", {})
    monkeypatch.setattr(idle_worker, "load_profile", lambda _: {"model": "new-model"})
    idle_worker._remember_idle_auth_issue("opencode-mcp", "old-model", "token expired")
    assert "opencode-mcp" in idle_worker._automatic_profile_candidates("opencode-mcp")
    assert "opencode-mcp" not in idle_worker._AUTH_DEFERRED


def test_idle_cooldown_applies_only_to_account_auth_not_rate_limits(monkeypatch):
    monkeypatch.setattr(idle_worker, "_AUTH_DEFERRED", {})
    assert idle_worker._is_provider_fallback_error("provider rate limit")
    idle_worker._remember_idle_auth_issue("gemini-mcp", "model", "rate limit")
    assert not idle_worker._AUTH_DEFERRED
    idle_worker._remember_idle_auth_issue("gemini-mcp", "model", "Account is not linked")
    assert "gemini-mcp" in idle_worker._AUTH_DEFERRED


def test_idle_all_unavailable_still_reports_real_attempt(monkeypatch):
    monkeypatch.setattr(idle_worker, "_AUTH_DEFERRED", {})
    monkeypatch.setattr(idle_worker, "load_profile", lambda _: {"model": "model"})
    for profile in idle_worker.DEFAULT_PROFILES:
        idle_worker._remember_idle_auth_issue(profile, "model", "token expired")
    # Do not silently discard operator work when every model is inaccessible.
    assert idle_worker._automatic_profile_candidates("codex-mcp") == ["codex-mcp"]


@pytest.mark.asyncio
async def test_explicit_idle_profile_ignores_background_auth_cooldown(monkeypatch, tmp_path):
    from contextlib import nullcontext
    from app.services.aicoder_runner import AICoderRunResult
    work = _repo(tmp_path)
    monkeypatch.setattr(idle_worker, "_AUTH_DEFERRED", {
        "opencode-mcp": ("mistral/mistral-medium-latest", float("inf")),
    })
    monkeypatch.setattr(idle_worker, "_state_guard", nullcontext)
    monkeypatch.setattr(idle_worker, "_load_state", lambda: {"operator_queue": []})
    monkeypatch.setattr(idle_worker, "_save_state", lambda state: None)
    monkeypatch.setattr(idle_worker, "_next_assignment",
                        lambda state: ("test-gap", "Explicit operator request", ""))
    monkeypatch.setattr(idle_worker, "workspace_fingerprint", lambda _: "sha:clean")
    monkeypatch.setattr(idle_worker, "create_snapshot", lambda _: tmp_path / "snapshot")
    monkeypatch.setattr(idle_worker, "build_idle_prompt", lambda **kw: "test prompt")
    monkeypatch.setattr(idle_worker, "load_profile",
                        lambda _: {"model": "mistral/mistral-medium-latest"})
    monkeypatch.setattr(idle_worker, "prepare_instance_home", lambda _: tmp_path / "home")
    monkeypatch.setattr(idle_worker, "apply_profile_state", lambda *a: None)
    calls = []
    class Runner:
        async def run(self, **kwargs):
            calls.append(kwargs.get("model"))
            return AICoderRunResult(profile_id="idle-opencode-mcp",
                                    status="success", model=kwargs.get("model", ""),
                                    response="STATUS: NO_FINDING")
    monkeypatch.setattr(idle_worker, "AICoderRunner", Runner)
    (tmp_path / "snapshot").mkdir()
    result = await idle_worker.run_idle_once(workspace=work, profile_id="opencode-mcp")
    assert result["status"] == "success"
    assert result["attempted_profiles"] == ["opencode-mcp"]
    assert len(calls) == 1
    assert "opencode-mcp" not in idle_worker._AUTH_DEFERRED
