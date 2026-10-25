"""Dev model benchmark guardrails."""

from unittest.mock import patch

from backend import state
from backend.bootstrap import initialize
from backend.services.dev_model_benchmark_guard import assess_dev_model, lookup_benchmark_row


def _snap(rows, best: str):
    return {
        "status": "done",
        "fromPersisted": True,
        "bestModel": best,
        "results": rows,
    }


def test_assess_mimo_warns_vs_rnj_best():
    initialize()
    rows = [
        {
            "model": "essentialai/rnj-1:latest",
            "status": "done",
            "loadOk": True,
            "toolScore": 93,
            "codeScore": 86,
            "overallScore": 88,
            "nativeToolCallRate": 0.9,
        },
        {
            "model": "maternion/mimo-v2.6:9b",
            "status": "done",
            "loadOk": True,
            "toolScore": 92,
            "codeScore": 63,
            "overallScore": 73,
            "nativeToolCallRate": 0.5,
        },
    ]
    with patch(
        "backend.services.model_benchmark_runner.get_benchmark_snapshot",
        return_value=_snap(rows, "essentialai/rnj-1:latest"),
    ):
        a = assess_dev_model("maternion/mimo-v2.6:9b")
    assert a.level == "warn"
    assert any("gap" in r.lower() or "best" in r.lower() for r in a.reasons)
    assert a.best_model == "essentialai/rnj-1:latest"


def test_assess_zero_tools_warns():
    initialize()
    rows = [
        {
            "model": "gemma-3-q4km:12b",
            "status": "done",
            "loadOk": True,
            "toolScore": 0,
            "codeScore": 65,
            "overallScore": 42,
            "nativeToolCallRate": 0.0,
        },
    ]
    with patch(
        "backend.services.model_benchmark_runner.get_benchmark_snapshot",
        return_value=_snap(rows, "gemma-3-q4km:12b"),
    ):
        a = assess_dev_model("gemma-3-q4km:12b")
    assert a.level == "warn"
    assert any("Tools score is 0" in r for r in a.reasons)


def test_lookup_benchmark_row_done_only():
    rows = [
        {"model": "pending-model", "status": "pending"},
        {"model": "ok-model", "status": "done", "overallScore": 70},
    ]
    with patch(
        "backend.services.dev_model_benchmark_guard._benchmark_rows",
        return_value=rows,
    ):
        assert lookup_benchmark_row("ok-model") is not None
        assert lookup_benchmark_row("pending-model") is None


def test_assess_no_row_ok():
    initialize()
    state.PRIMARY_MODELS = {"dev": "unknown:tag"}
    with patch(
        "backend.services.model_benchmark_runner.get_benchmark_snapshot",
        return_value=_snap([], ""),
    ):
        a = assess_dev_model("unknown:tag")
    assert a.level == "ok"
