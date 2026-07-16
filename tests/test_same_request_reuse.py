"""Same-request reuse, related prompt injection, and oversized card rejection."""

from backend import state
from backend.agents.task_context import build_task_prompt, find_task_by_id, init_new_task, record_task_decision
from backend.bootstrap import initialize
from backend.services.board_service import append_backlog_tasks, is_oversized_implementation
from backend.services.feature_similarity import REUSE_THRESHOLD, find_same_request_match, score_task_similarity


def _clear_board():
    state.SHARED_BOARD.clear()
    for lane in (
        "Features",
        "Backlog",
        "Pending Approval",
        "Refinement",
        "In Progress",
        "Needs User",
        "Needs PO",
        "Code Review",
        "QA",
        "Done",
    ):
        state.SHARED_BOARD[lane] = []


def test_high_similarity_reuses_existing_card_no_duplicate():
    initialize()
    _clear_board()
    existing = init_new_task(
        {
            "id": "T-EXIST",
            "title": "Add user login form",
            "description": "Implement login form with email and password validation",
            "status": "In Progress",
            "workType": "implementation",
            "requiresDev": True,
        }
    )
    state.SHARED_BOARD["In Progress"] = [existing]
    requester = init_new_task(
        {
            "id": "T-REQ",
            "title": "Requester card",
            "description": "Working on auth flow",
            "status": "In Progress",
            "workType": "implementation",
        }
    )
    state.SHARED_BOARD["In Progress"].append(requester)
    state.ACTIVE_SPRINT_TASK_ID = "T-REQ"

    msg = append_backlog_tasks(
        [
            {
                "title": "Add user login form",
                "description": "Implement login form with email and password validation",
                "acceptanceCriteria": ["Form validates email"],
                "workType": "implementation",
                "requiresDev": True,
            }
        ]
    )

    backlog = state.SHARED_BOARD.get("Backlog", [])
    assert not any(
        "login form" in str(t.get("title", "")).lower() and t.get("id") != "T-EXIST" for t in backlog
    )
    assert "Reused existing T-EXIST" in msg
    assert "Do not recreate" in msg
    req = find_task_by_id("T-REQ")
    assert req is not None
    assert "T-EXIST" in (req.get("relatedTaskIds") or [])
    assert "T-EXIST" in (req.get("blockedBy") or [])


def test_low_similarity_still_creates():
    initialize()
    _clear_board()
    existing = init_new_task(
        {
            "id": "T-PAY",
            "title": "Payment checkout flow",
            "description": "Wire stripe payment intents for cart checkout",
            "status": "Backlog",
            "workType": "implementation",
        }
    )
    state.SHARED_BOARD["Backlog"] = [existing]

    msg = append_backlog_tasks(
        [
            {
                "title": "Dark mode toggle",
                "description": "Add a settings switch for dark theme preference",
                "acceptanceCriteria": ["Toggle persists"],
                "workType": "implementation",
                "requiresDev": True,
            }
        ]
    )

    assert "Added 1 task" in msg
    assert "Reused" not in msg
    assert len(state.SHARED_BOARD["Backlog"]) == 2


def test_done_match_attaches_outcome_to_requester():
    initialize()
    _clear_board()
    done = init_new_task(
        {
            "id": "T-DONE",
            "title": "Cache user profile API",
            "description": "Add redis cache layer for profile endpoint responses",
            "status": "Done",
            "workType": "implementation",
            "files": [{"path": "lib/cache/profile.dart", "action": "created"}],
        }
    )
    record_task_decision("T-DONE", "Developer", "implement", "Added redis cache for profile")
    state.SHARED_BOARD["Done"] = [done]
    requester = init_new_task(
        {
            "id": "T-PARENT",
            "title": "Profile performance",
            "description": "Speed up profile reads",
            "status": "In Progress",
        }
    )
    state.SHARED_BOARD["In Progress"] = [requester]
    state.ACTIVE_SPRINT_TASK_ID = "T-PARENT"

    msg = append_backlog_tasks(
        [
            {
                "title": "Cache user profile API",
                "description": "Add redis cache layer for profile endpoint responses",
                "acceptanceCriteria": ["Cache hit path"],
                "workType": "implementation",
                "requiresDev": True,
            }
        ]
    )

    assert "Reused existing T-DONE" in msg
    req = find_task_by_id("T-PARENT")
    assert req is not None
    outcomes = req.get("dependencyOutcomes") or []
    assert any(o.get("taskId") == "T-DONE" for o in outcomes)
    outcome = next(o for o in outcomes if o.get("taskId") == "T-DONE")
    assert "redis" in outcome.get("summary", "").lower() or "cache" in outcome.get("summary", "").lower()


def test_oversized_implementation_rejected():
    initialize()
    _clear_board()
    fat = {
        "title": "Rebuild entire auth stack",
        "description": (
            "Implement OAuth, add JWT refresh, create session store, build login UI, "
            "refactor middleware, migrate users, integrate SSO, wire logout, fix token expiry."
        ),
        "acceptanceCriteria": [
            "OAuth works",
            "JWT refresh",
            "Session store",
            "Login UI",
            "Middleware",
            "SSO",
        ],
        "workType": "implementation",
        "requiresDev": True,
    }
    reason = is_oversized_implementation(fat)
    assert reason is not None

    msg = append_backlog_tasks([fat])
    assert msg.startswith("Error:")
    assert "smallest" in msg.lower() or "split" in msg.lower()
    assert state.SHARED_BOARD.get("Backlog") == []


def test_planning_card_not_rejected_for_size():
    initialize()
    _clear_board()
    planning = {
        "title": "Plan auth decomposition",
        "description": "Break auth into smallest backlog cards for OAuth JWT sessions UI SSO",
        "acceptanceCriteria": ["a", "b", "c", "d", "e", "f"],
        "workType": "planning",
        "requiresDev": False,
        "requiresQa": False,
    }
    assert is_oversized_implementation(planning) is None
    msg = append_backlog_tasks([planning])
    assert "Added 1 task" in msg


def test_build_task_prompt_includes_related_done_outcomes():
    initialize()
    _clear_board()
    done = init_new_task(
        {
            "id": "T-REL",
            "title": "Search index rebuild",
            "description": "Rebuild elasticsearch index for products",
            "status": "Done",
            "files": [{"path": "services/search.py", "action": "updated"}],
        }
    )
    record_task_decision("T-REL", "Developer", "implement", "Rebuilt search index")
    current = init_new_task(
        {
            "id": "T-CUR",
            "title": "Product search polish",
            "description": "Polish product search UX",
            "status": "In Progress",
            "relatedTaskIds": ["T-REL"],
        }
    )
    state.SHARED_BOARD["Done"] = [done]
    state.SHARED_BOARD["In Progress"] = [current]

    prompt = build_task_prompt(current, "Brief")
    assert "RELATED WORK" in prompt
    assert "T-REL" in prompt
    assert "reuse" in prompt.lower()
    assert "Search index" in prompt or "search" in prompt.lower()


def test_find_same_request_match_threshold():
    initialize()
    _clear_board()
    a = {
        "title": "Export CSV reports",
        "description": "Generate CSV export for billing reports",
    }
    b = {
        "id": "T-CSV",
        "title": "Export CSV reports",
        "description": "Generate CSV export for billing reports",
        "workType": "implementation",
    }
    state.SHARED_BOARD["Backlog"] = [init_new_task(dict(b))]
    score, _ = score_task_similarity(a, state.SHARED_BOARD["Backlog"][0])
    assert score >= REUSE_THRESHOLD
    match = find_same_request_match(a)
    assert match is not None
    assert match[0]["id"] == "T-CSV"
