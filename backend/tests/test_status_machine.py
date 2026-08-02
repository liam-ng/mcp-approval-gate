import pytest

from app.core.models import ALL_STATUSES, TERMINAL_STATUSES
from app.core.status_machine import (
    TransitionError,
    assert_transition,
    can_transition,
    status_after_approval,
)

ACTOR_KINDS = ("agent", "human", "system")

# The complete set of legal (from, to, actor) triples. Anything not listed
# here must be rejected — the exhaustive test below checks every combination.
LEGAL = {
    ("PENDING_APPROVAL", "PENDING_APPROVAL", "human"),
    ("PENDING_APPROVAL", "APPROVED", "human"),
    ("PENDING_APPROVAL", "REJECTED", "human"),
    ("PENDING_APPROVAL", "DEPRECATED", "human"),
    ("PENDING_APPROVAL", "EXPIRED", "system"),
    ("APPROVED", "DEPRECATED", "human"),
    ("APPROVED", "EXPIRED", "system"),
    ("APPROVED", "EXECUTING", "agent"),
    ("EXECUTING", "COMPLETED", "agent"),
    ("EXECUTING", "FAILED", "agent"),
}


def test_exhaustive_transition_matrix():
    for frm in ALL_STATUSES:
        for to in ALL_STATUSES:
            for actor in ACTOR_KINDS:
                expected = (frm, to, actor) in LEGAL
                assert can_transition(frm, to, actor) is expected, (frm, to, actor)


def test_terminal_statuses_have_no_outgoing_transitions():
    for frm in TERMINAL_STATUSES:
        for to in ALL_STATUSES:
            for actor in ACTOR_KINDS:
                assert not can_transition(frm, to, actor)  # type: ignore[arg-type]


def test_assert_transition_raises_with_context():
    with pytest.raises(TransitionError) as exc:
        assert_transition("COMPLETED", "EXECUTING", "agent")
    assert exc.value.from_status == "COMPLETED"
    assert exc.value.to_status == "EXECUTING"


@pytest.mark.parametrize(
    ("count", "required", "expected"),
    [
        (0, 1, "PENDING_APPROVAL"),
        (1, 1, "APPROVED"),
        (2, 1, "APPROVED"),
        (0, 2, "PENDING_APPROVAL"),
        (1, 2, "PENDING_APPROVAL"),
        (2, 2, "APPROVED"),
        (3, 2, "APPROVED"),
    ],
)
def test_approval_threshold(count, required, expected):
    assert status_after_approval(count, required) == expected
