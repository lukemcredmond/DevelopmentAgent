"""Patch recovery: excerpt nudge and fingerprint helpers."""

from backend.services.patch_recovery import (
    build_patch_fail_excerpt_nudge,
    failed_patch_fingerprint,
)
from backend.services.sprint_speed_gates import patch_fingerprint


def test_failed_patch_fingerprint_stable():
    fp1 = failed_patch_fingerprint("lib/main.dart", old_text="foo", new_text="bar")
    fp2 = failed_patch_fingerprint("lib/main.dart", old_text="foo", new_text="bar")
    assert fp1 == fp2
    assert fp1 == patch_fingerprint("lib/main.dart", old_text="foo", summary="bar")


def test_build_patch_fail_excerpt_nudge_includes_path():
    body = "line1\nline2\nanchor line\nline4\n"
    msg = build_patch_fail_excerpt_nudge("lib/main.dart", body, "anchor line")
    assert "lib/main.dart" in msg
    assert "anchor line" in msg or "3|" in msg
