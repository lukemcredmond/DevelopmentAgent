import datetime

from backend import state
from backend.config import MAX_LOG_ENTRIES
from backend.services.events import publish_event


def add_system_log(source: str, log_type: str, text: str) -> None:
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    entry = {
        "timestamp": timestamp,
        "source": source,
        "type": log_type,
        "text": text,
    }
    state.SYSTEM_LOGS.append(entry)
    if len(state.SYSTEM_LOGS) > MAX_LOG_ENTRIES:
        del state.SYSTEM_LOGS[: len(state.SYSTEM_LOGS) - MAX_LOG_ENTRIES]
    state.storage.save_project_logs(state.CURRENT_PROJECT_ID, state.SYSTEM_LOGS)
    publish_event("log", entry)


def clear_system_logs() -> None:
    state.SYSTEM_LOGS.clear()
    state.storage.save_project_logs(state.CURRENT_PROJECT_ID, state.SYSTEM_LOGS)
