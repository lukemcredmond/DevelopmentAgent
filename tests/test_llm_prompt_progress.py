"""LLM prompt fingerprint and unchanged-prompt progress injection."""

from backend.services.llm_context import (
    fingerprint_llm_messages,
    maybe_inject_unchanged_prompt_progress,
)


def test_fingerprint_ignores_progress_marker():
    base = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task prompt"},
    ]
    fp1 = fingerprint_llm_messages(base)
    with_progress = base + [
        {
            "role": "system",
            "content": "=== STEP PROGRESS (prompt unchanged) ===\nextra",
        }
    ]
    fp2 = fingerprint_llm_messages(with_progress)
    assert fp1 == fp2


def test_unchanged_fingerprint_injects_system_message():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "same task"},
        {"role": "tool", "content": "blocked fingerprint", "name": "read_file"},
    ]
    fp0 = fingerprint_llm_messages(messages)
    fp1, injected1 = maybe_inject_unchanged_prompt_progress(
        messages, iteration=1, last_fingerprint=""
    )
    assert injected1 is False
    assert fp1 == fp0
    assert len(messages) == 3

    fp2, injected2 = maybe_inject_unchanged_prompt_progress(
        messages, iteration=2, last_fingerprint=fp0
    )
    assert injected2 is True
    assert len(messages) == 4
    assert any(
        "STEP PROGRESS (prompt unchanged)" in str(m.get("content"))
        for m in messages
        if m.get("role") == "system"
    )
