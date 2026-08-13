"""Regression tests for the 2026-08-13 crash that stranded a ticket in EXECUTING.

A 401 from execution/result escaped process_ticket, killed the process, and left the ticket in the
one status nothing recovers -- jobs/expiry.py sweeps PENDING_APPROVAL and APPROVED only.
"""

from __future__ import annotations

import pytest

from app import main as executor_main
from app.gate_client import GateError


@pytest.fixture(autouse=True)
def no_backoff_sleep(monkeypatch):
    monkeypatch.setattr(executor_main.time, "sleep", lambda _s: None)


class FakeGate:
    def __init__(self, failures: list[Exception | None]):
        self._failures = failures
        self.calls: list[tuple] = []

    def report_result(self, ticket_id, outcome, message, request_ids):
        self.calls.append((ticket_id, outcome, message, request_ids))
        exc = self._failures.pop(0) if self._failures else None
        if exc:
            raise exc
        return {"status": "CLOSED"}


def replayed() -> GateError:
    """The exact error that caused the outage."""
    return GateError(401, "AUTH", "{'code': 'AGENT_AUTH_FAILED', 'message': 'replayed identity request'}")


def test_transient_failure_is_retried_until_it_lands():
    gate = FakeGate([replayed(), replayed(), None])
    executor_main._report(gate, "T1", "success", "executed as approved", ["req-1"])
    assert len(gate.calls) == 3
    assert gate.calls[-1] == ("T1", "success", "executed as approved", ["req-1"])


def test_report_never_raises_even_when_every_attempt_fails(caplog):
    """MUST NOT RAISE: a raise here propagates out of main() and kills the poll loop."""
    gate = FakeGate([replayed()] * executor_main._REPORT_ATTEMPTS)
    executor_main._report(gate, "T2", "success", "executed as approved", [])
    assert len(gate.calls) == executor_main._REPORT_ATTEMPTS
    assert "NEEDS MANUAL CLEANUP" in caplog.text


def test_invalid_state_is_not_retried():
    """Someone else already moved the ticket. Retrying cannot win and just delays the loop."""
    gate = FakeGate([GateError(409, "INVALID_STATE", "ticket is CLOSED, not EXECUTING")])
    executor_main._report(gate, "T3", "success", "executed as approved", [])
    assert len(gate.calls) == 1


def test_unexpected_exception_is_swallowed_too():
    """Not just GateError -- a socket error here would strand the ticket just as permanently."""
    gate = FakeGate([ConnectionError("connection reset")] * executor_main._REPORT_ATTEMPTS)
    executor_main._report(gate, "T4", "failure", "boom", [])
    assert len(gate.calls) == executor_main._REPORT_ATTEMPTS
