"""Shared pytest hooks."""

import pytest

from backend.services.workflow_settings import reset_workflow_settings


@pytest.fixture(autouse=True)
def _reset_workflow_settings_after_test():
    yield
    try:
        reset_workflow_settings()
    except Exception:
        pass
