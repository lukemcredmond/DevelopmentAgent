"""Parse suggested child cards from plan epic details."""

from backend.services.feature_service import build_epics_json_from_plan_outline


def test_inline_quoted_suggested_child_cards():
    outline = """## Proposed epics
1. **Core app scaffold** — foundation

## Epic details

### Epic: Core app scaffold

- **Scope**
  - Flutter setup
- **Suggested child cards:** "Set up Drift schema"; "Build settings provider"; "Offline-first boot"
- **AC hints**
  - App boots offline
"""
    raw = build_epics_json_from_plan_outline(outline)
    assert raw
    import json

    epics = json.loads(raw)["epics"]
    assert len(epics) == 1
    titles = [c["title"] for c in epics[0]["children"]]
    assert "Set up Drift schema" in titles
    assert "Build settings provider" in titles
    assert titles != [
        "Implement Core app scaffold — core flow",
        "Implement Core app scaffold — edge cases and polish",
    ]


def test_outline_fingerprint_invalidates_stale_pack():
    from backend.bootstrap import initialize
    from backend.services.feature_pack_service import (
        get_pending_feature_pack,
        outline_fingerprint,
        set_pending_feature_pack,
    )

    initialize()
    set_pending_feature_pack(
        {
            "epicsJson": '{"epics":[]}',
            "outlineFingerprint": outline_fingerprint("plan A"),
            "status": "preview",
        }
    )
    pack = get_pending_feature_pack()
    assert pack.get("outlineFingerprint") == outline_fingerprint("plan A")
    assert outline_fingerprint("plan B") != pack.get("outlineFingerprint")
