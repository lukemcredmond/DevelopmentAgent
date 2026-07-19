"""Console log timestamp format matches other panels."""

from backend.bootstrap import initialize
from backend.services.logs import add_system_log
from backend import state


def test_system_log_timestamp_includes_date():
    initialize()
    before = len(state.SYSTEM_LOGS)
    add_system_log("System", "info", "order-check")
    entry = state.SYSTEM_LOGS[-1]
    assert entry["text"] == "order-check"
    # YYYY-MM-DD HH:MM:SS
    assert len(entry["timestamp"]) >= 19
    assert entry["timestamp"][4] == "-"
    assert entry["timestamp"][10] == " "
    assert len(state.SYSTEM_LOGS) == before + 1
