from backend.services.tool_cache import (
    clear_tool_cache,
    get_cached_result,
    resolve_duplicate_replay,
    store_cached_result,
)
from backend.agents.registry import _format_grep_results


def test_empty_grep_pattern_errors():
    out = _format_grep_results("")
    assert out.startswith("Error:")
    assert "non-empty pattern" in out


def test_empty_grep_not_cached_or_replayed():
    clear_tool_cache()
    bad = "No matches for pattern ''.\n[cached — workspace unchanged since last call]"
    store_cached_result("grep", {"pattern": ""}, bad, True)
    assert get_cached_result("grep", {"pattern": ""}) is None
    assert resolve_duplicate_replay("grep", {"pattern": ""}, None) is None


def test_valid_grep_still_cached():
    clear_tool_cache()
    body = "Grep 'foo' (2 match(es)):\n- a.dart:1: foo\n- b.dart:2: foo"
    store_cached_result("grep", {"pattern": "foo"}, body, True)
    hit = get_cached_result("grep", {"pattern": "foo"})
    assert hit is not None
    assert "a.dart" in hit[0]


def test_junk_no_matches_empty_pattern_evicted_on_read():
    clear_tool_cache()
    from backend.services import tool_cache as tc

    key = tc._cache_key("grep", {"pattern": ""})
    tc._STEP_CACHE[key] = {
        "output": "No matches for pattern ''.",
        "success": True,
    }
    assert get_cached_result("grep", {"pattern": ""}) is None
    assert key not in tc._STEP_CACHE
