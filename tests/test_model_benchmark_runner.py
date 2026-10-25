"""Model benchmark scoring and job lifecycle."""

import json
from unittest.mock import patch

from backend import state
from backend.bootstrap import initialize
from backend.services.model_benchmark_runner import (
    _load_persisted_with_fallback,
    _storage_key,
    can_resume_benchmark,
    cancel_benchmark,
    compute_code_scores,
    compute_overall_score,
    compute_tool_scores,
    export_benchmark_snapshot,
    filter_chat_models,
    get_benchmark_snapshot,
    import_benchmark_snapshot,
    is_benchmark_running,
    merge_resume_benchmark,
    pick_best_model,
    reset_benchmark_job_state,
    score_tier,
    start_model_benchmark,
)


def _done_row(model: str, overall: int) -> dict:
    return {
        "model": model,
        "status": "done",
        "loadOk": True,
        "overallScore": overall,
        "toolScore": 80,
        "codeScore": 70,
    }


def test_scoring_helpers():
    assert score_tier(90) == "good"
    assert score_tier(60) == "warn"
    assert score_tier(30) == "bad"
    assert score_tier(None) == "unknown"
    assert compute_tool_scores({"pass": 8, "fail": 2}) == (80, 0.8)
    code_score, pr, native = compute_code_scores(
        {"passRate": 0.5, "toolRecoveryRate": 0.2}
    )
    assert code_score == round(100 * (0.7 * 0.5 + 0.3 * 0.8))
    assert pr == 0.5
    assert native == 0.8
    assert compute_overall_score(80, 60) == round(0.35 * 80 + 0.65 * 60)


def test_pick_best_model_tiebreak():
    rows = [
        {"model": "a", "loadOk": True, "overallScore": 70, "codeScore": 50, "wallClockSec": 100},
        {"model": "b", "loadOk": True, "overallScore": 70, "codeScore": 60, "wallClockSec": 200},
    ]
    assert pick_best_model(rows) == "b"


def test_filter_chat_models():
    assert "nomic-embed-text" not in filter_chat_models(["llama3:8b", "nomic-embed-text"])


def test_start_benchmark_blocked_when_sprint_active():
    initialize()
    reset_benchmark_job_state()
    state.ACTIVE_SPRINT_AGENT = "Developer"
    out = start_model_benchmark(["m1"])
    assert out.get("status") == "blocked"
    state.ACTIVE_SPRINT_AGENT = ""


def test_benchmark_job_completes_with_mocks():
    initialize()
    reset_benchmark_job_state()
    state.ACTIVE_SPRINT_AGENT = ""
    state.OLLAMA_URL = "http://localhost:11434"

    class FakeHealth:
        ok = True
        url = "http://localhost:11434"
        models = ["model-a"]
        provider = "ollama"
        error = ""

    class FakeProvider:
        health_timeout_sec = 30
        timeout_sec = 600

        def health(self):
            return FakeHealth()

    probe_ok = {"ok": True, "provider": "ollama", "url": "http://x", "model": "model-a", "models": []}

    with patch(
        "backend.services.model_test_runner.build_test_provider",
        return_value=FakeProvider(),
    ), patch(
        "backend.services.model_test_runner.probe_model",
        return_value=probe_ok,
    ), patch(
        "backend.services.tool_llm_probe.run_llm_probe_all",
        return_value=[{"success": True}, {"success": True, "skipReason": None}],
    ), patch(
        "backend.services.eval_harness.run_suite",
        return_value=([], {"passRate": 1.0, "toolRecoveryRate": 0.0, "passed": 8, "tasks": 8}),
    ), patch(
        "backend.services.eval_harness.load_tasks",
        return_value=[object()],
    ), patch(
        "backend.agents.registry.agent_dev"
    ) as agent_dev:
        agent_dev.set_primary_model = lambda m: None
        agent_dev.model = "old"
        start_model_benchmark(["model-a"])
        import time

        for _ in range(50):
            from backend.services.model_benchmark_runner import get_benchmark_snapshot

            snap = get_benchmark_snapshot()
            if snap.get("status") in ("done", "cancelled"):
                break
            time.sleep(0.05)
        snap = get_benchmark_snapshot()
        assert snap.get("status") == "done"
        rows = snap.get("results") or []
        assert rows and rows[0].get("overallScore") is not None
        assert snap.get("bestModel") == "model-a"


def test_cancel_benchmark_sets_flag_when_running():
    initialize()
    reset_benchmark_job_state()
    state.ACTIVE_SPRINT_AGENT = ""

    class FakeHealth:
        ok = True
        url = "http://localhost:11434"
        models = ["m1"]
        provider = "ollama"
        error = ""

    class FakeProvider:
        def health(self):
            return FakeHealth()

    with patch("backend.services.model_test_runner.build_test_provider", return_value=FakeProvider()):
        start_model_benchmark(["m1"])
    cancel_benchmark()
    from backend.services.model_benchmark_runner import get_benchmark_snapshot

    snap = get_benchmark_snapshot()
    assert snap.get("status") == "running"
    assert snap.get("cancelRequested") is True
    reset_benchmark_job_state()


def test_can_resume_benchmark_partial():
    initialize()
    reset_benchmark_job_state()
    url = "http://localhost:11434"
    state.OLLAMA_URL = url
    snap = {
        "status": "cancelled",
        "results": [
            _done_row("a", 79),
            _done_row("b", 65),
            {"model": "c", "status": "pending"},
        ],
    }
    state.storage.set_setting(_storage_key(url), json.dumps(snap))
    meta = can_resume_benchmark(url)
    assert meta["canResume"] is True
    assert meta["doneCount"] == 2
    assert meta["remainingCount"] == 1


def test_can_resume_benchmark_false_when_all_done():
    initialize()
    url = "http://localhost:11434"
    snap = {
        "status": "done",
        "results": [_done_row("a", 90), _done_row("b", 80)],
    }
    state.storage.set_setting(_storage_key(url), json.dumps(snap))
    meta = can_resume_benchmark(url)
    assert meta["canResume"] is False
    assert meta["remainingCount"] == 0


def test_merge_resume_keeps_done_and_queues_failed():
    persisted = {
        "results": [
            _done_row("done-a", 79),
            {"model": "failed-b", "status": "failed", "loadOk": False},
            {"model": "pend-c", "status": "pending"},
        ],
    }
    installed = ["done-a", "failed-b", "pend-c", "new-d"]
    merged, to_run = merge_resume_benchmark(persisted, installed)
    assert len(merged) == 4
    assert merged[0]["overallScore"] == 79
    assert to_run == ["failed-b", "pend-c", "new-d"]


def test_resume_benchmark_runs_subset_only():
    initialize()
    reset_benchmark_job_state()
    state.ACTIVE_SPRINT_AGENT = ""
    url = "http://localhost:11434"
    state.OLLAMA_URL = url
    persisted = {
        "status": "cancelled",
        "runId": "oldrun",
        "startedAt": "2020-01-01 00:00:00",
        "results": [
            _done_row("model-a", 79),
            _done_row("model-b", 65),
            {"model": "model-c", "status": "pending"},
            {"model": "model-d", "status": "pending"},
            {"model": "model-e", "status": "pending"},
        ],
    }
    state.storage.set_setting(_storage_key(url), json.dumps(persisted))

    class FakeHealth:
        ok = True
        url = "http://localhost:11434"
        models = ["model-a", "model-b", "model-c", "model-d", "model-e"]
        provider = "ollama"
        error = ""

    class FakeProvider:
        def health(self):
            return FakeHealth()

    run_models: list[str] = []

    def fake_thread(target=None, args=(), **kwargs):
        models_arg = args[0] if args else kwargs.get("args", ())[0]
        run_models.extend(list(models_arg))
        class _T:
            def start(self):
                return None

        return _T()

    with patch(
        "backend.services.model_test_runner.build_test_provider",
        return_value=FakeProvider(),
    ), patch(
        "backend.services.model_benchmark_runner.threading.Thread",
        side_effect=fake_thread,
    ):
        out = start_model_benchmark(resume=True)
    assert out.get("status") == "running"
    assert run_models == ["model-c", "model-d", "model-e"]
    assert out.get("completed") == 2
    assert len(out.get("results") or []) == 5
    assert out["results"][0]["overallScore"] == 79
    assert out["results"][1]["overallScore"] == 65
    reset_benchmark_job_state()


def test_benchmark_url_fallback_localhost_vs_127():
    initialize()
    reset_benchmark_job_state()
    snap = {"status": "done", "results": [_done_row("only-127", 88)]}
    state.storage.set_setting(
        "model_benchmark_last:http://127.0.0.1:11434",
        json.dumps(snap),
    )
    data = _load_persisted_with_fallback("http://localhost:11434")
    assert data is not None
    assert data["results"][0]["model"] == "only-127"
    assert state.storage.get_setting(_storage_key("http://localhost:11434"))
    reset_benchmark_job_state()


def test_can_resume_when_persisted_status_running_partial():
    initialize()
    url = "http://localhost:11434"
    snap = {
        "status": "running",
        "results": [
            _done_row("a", 80),
            {"model": "b", "status": "pending"},
        ],
    }
    state.storage.set_setting(_storage_key(url), json.dumps(snap))
    meta = can_resume_benchmark(url)
    assert meta["canResume"] is True
    assert meta["doneCount"] == 1


def test_import_and_export_roundtrip():
    initialize()
    reset_benchmark_job_state()
    assert not is_benchmark_running()
    url = "http://localhost:11434"
    state.OLLAMA_URL = url
    payload = {
        "results": [_done_row("imported", 77), {"model": "pending-m", "status": "pending"}],
        "status": "cancelled",
    }
    out = import_benchmark_snapshot(payload, url=url)
    assert out.get("bestModel") == "imported"
    assert out.get("canResume") is True
    exported = export_benchmark_snapshot(url)
    assert exported.get("results")
    assert exported["results"][0]["overallScore"] == 77
    snap = get_benchmark_snapshot()
    assert snap.get("fromPersisted")
    reset_benchmark_job_state()


def test_import_blocked_while_running():
    initialize()
    reset_benchmark_job_state()
    state.ACTIVE_SPRINT_AGENT = ""

    class FakeHealth:
        ok = True
        url = "http://localhost:11434"
        models = ["m1"]
        provider = "ollama"
        error = ""

    class FakeProvider:
        def health(self):
            return FakeHealth()

    with patch("backend.services.model_test_runner.build_test_provider", return_value=FakeProvider()):
        start_model_benchmark(["m1"])
    assert is_benchmark_running()
    blocked = import_benchmark_snapshot({"results": [_done_row("x", 1)]})
    assert blocked.get("status") == "blocked"
    reset_benchmark_job_state()
