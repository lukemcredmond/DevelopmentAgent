"""Dev step must not route to Needs PO on casual clarification wording in prose."""

from backend.services.needs_user_guard import dev_clarification_from_result


def test_dev_clarification_ignores_casual_wording_in_summary():
    result = (
        "Updated lib/main.dart. The acceptance criteria still need clarification "
        "from the PO on edge cases, but I implemented the happy path."
    )
    assert dev_clarification_from_result(result) is False


def test_dev_clarification_accepts_explicit_line_prefix():
    assert dev_clarification_from_result("Needs clarification: auth provider for SSO\n") is True
    assert dev_clarification_from_result("Blocked on requirements: OAuth scope\n") is True
    assert dev_clarification_from_result("Notes: unclear requirement wording in AC item 3\n") is False


def test_dev_clarification_accepts_move_to_needs_po_phrase():
    assert dev_clarification_from_result("I will move to needs po for the open question.") is True
