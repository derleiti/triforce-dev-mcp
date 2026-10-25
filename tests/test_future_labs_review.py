from types import SimpleNamespace
import pytest
from app.services.future_labs_review import PROJECT, review
from app.mcp.handlers_memory_history import HISTORY_HANDLERS, HISTORY_TOOLS


class Provider:
    settings = SimpleNamespace(memory_max_results=4)
    def __init__(self, omit=()):
        self.omit = set(omit)
        self.calls = []
    async def get_observations(self, ids, project):
        self.calls.append((list(ids), project))
        return [
            {"id": i, "project": project,
             "title": "shared_ai_message: Re: Future Lab round 1/3",
             "narrative": f"Historical message {i}"}
            for i in ids if i not in self.omit
        ]


@pytest.mark.asyncio
async def test_complete_review_is_read_only_and_scoped():
    provider = Provider()
    result = await review(provider)
    assert result["status"] == "complete"
    assert result["source_count"] == 19
    assert result["missing_ids"] == []
    assert all(project == PROJECT and len(ids) <= 4 for ids, project in provider.calls)
    assert all(topic["runtime_verification"] == "not_assessed" for topic in result["topics"])
    assert all(topic["historical_state"] == "proposal_not_approved" for topic in result["topics"])
    assert len(result["topics"]) == 6


@pytest.mark.asyncio
async def test_missing_history_cannot_be_presented_as_verified():
    result = await review(Provider(omit={31}))
    assert result["status"] == "degraded"
    assert result["missing_ids"] == [31]
    assert result["source_count"] == 18


def test_future_labs_review_registered_as_operator_readonly():
    tool = next(t for t in HISTORY_TOOLS if t["name"] == "future_labs_review")
    assert tool["annotations"]["readOnlyHint"] is True
    assert tool["inputSchema"]["additionalProperties"] is False
    assert "future_labs_review" in HISTORY_HANDLERS


@pytest.mark.asyncio
async def test_static_evidence_never_promotes_unverified_features(tmp_path):
    from app.services.future_labs_review import static_evidence
    p = tmp_path / ".github/workflows"
    p.mkdir(parents=True)
    (p / "platform-artifacts.yml").write_text("name: stub\n")
    report = await review(Provider(), repo_root=tmp_path)
    topics = {r["id"]:r for r in report["topics"]}
    assert topics["reproducible_releases"]["static_evidence"] == [".github/workflows/platform-artifacts.yml"]
    assert all(not x["acceptance_verified"] for x in topics.values())
    assert all(x["runtime_verification"] == "not_assessed" for x in topics.values())
    assert static_evidence(tmp_path)["hardware_matrix"]["static_evidence_status"] == "no_mapped_evidence"
