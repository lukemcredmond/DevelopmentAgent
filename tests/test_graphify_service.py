"""Graphify service helpers."""

from backend.services.graphify_service import graphify_status, run_graph_query


def test_graph_query_without_cli_returns_helpful_message():
    result = run_graph_query("authentication module")
    assert isinstance(result, str)
    assert len(result) > 10


def test_graphify_status_shape():
    status = graphify_status()
    assert "available" in status
    assert "reportExists" in status
