from datetime import datetime, timedelta, timezone

from app.routes import mcp


def test_session_cleanup_accepts_mixed_timezone_history():
    previous = dict(mcp._mcp_sessions)
    try:
        mcp._mcp_sessions.clear()
        old_naive = datetime.now() - timedelta(hours=2)
        fresh_aware = datetime.now(timezone.utc)
        mcp._mcp_sessions["old-naive"] = {"created": old_naive, "last_seen": old_naive}
        mcp._mcp_sessions["fresh-aware"] = {"created": fresh_aware, "last_seen": fresh_aware}

        mcp._cleanup_old_sessions()

        assert "old-naive" not in mcp._mcp_sessions
        assert "fresh-aware" in mcp._mcp_sessions
    finally:
        mcp._mcp_sessions.clear()
        mcp._mcp_sessions.update(previous)


def test_new_session_timestamps_are_timezone_aware():
    session_id = "timezone-aware-regression"
    previous = mcp._mcp_sessions.pop(session_id, None)
    try:
        session = mcp._get_session(session_id)
        assert session["created"].utcoffset() is not None
        assert session["last_seen"].utcoffset() is not None
    finally:
        mcp._mcp_sessions.pop(session_id, None)
        if previous is not None:
            mcp._mcp_sessions[session_id] = previous
