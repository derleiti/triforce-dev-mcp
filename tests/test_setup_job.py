from __future__ import annotations

from app.setup_job import job_is_running


def test_setup_job_running_states_are_explicit():
    assert job_is_running({"state": "queued"})
    assert job_is_running({"state": "running"})
    assert not job_is_running({"state": "completed"})
    assert not job_is_running({"state": "failed"})
    assert not job_is_running(None)
