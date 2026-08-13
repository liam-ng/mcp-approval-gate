"""Poll for approved tickets, execute each exactly once, report the outcome.

Single replica only. Two replicas would both see the same APPROVED ticket in
one poll; the gate's status machine makes the *second* execution/start fail
with INVALID_STATE (APPROVED -> EXECUTING is legal only from APPROVED), so a
duplicate AWS call is prevented by the gate rather than by this loop -- but
the losing replica would log a confusing error every cycle. Scale out only
behind a distributed lock.
"""

from __future__ import annotations

import logging
import random
import signal
import sys
import time
from types import FrameType
from typing import Any

from .aws_exec import ExecutionFailed, execute
from .gate_client import GateClient, GateError
from .settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("executor")

# Retries for execution/result ONLY. Between execution/start and a landed result the ticket sits in
# EXECUTING, and nothing recovers that state: jobs/expiry.py sweeps PENDING_APPROVAL and APPROVED only.
# So this call is worth retrying where the others are not.
_REPORT_ATTEMPTS = 4
_REPORT_BACKOFF_SECONDS = 2

_stop = False


def _handle_signal(signum: int, _frame: FrameType | None) -> None:
    global _stop
    log.info("received signal %s, finishing current ticket then exiting", signum)
    _stop = True


def _report(
    gate: GateClient, ticket_id: str, outcome: str, message: str, request_ids: list[str]
) -> None:
    """Land the outcome, retrying. MUST NOT RAISE.

    An exception here escaped process_ticket and killed the whole process on 2026-08-13, leaving the
    ticket EXECUTING forever -- exactly the state the caller comments warn about. The 401 that did it
    was transient (a same-second signature collision, fixed in gate_client.identity_header), so a
    retry would have recovered it outright.
    """
    for attempt in range(1, _REPORT_ATTEMPTS + 1):
        try:
            gate.report_result(ticket_id, outcome, message, request_ids)
            return
        except GateError as exc:
            # The gate already moved it (a concurrent replica, or a human). Retrying cannot win.
            if exc.code == "INVALID_STATE":
                log.error("%s: execution/result rejected: %s -- not retrying", ticket_id, exc)
                return
            log.warning(
                "%s: execution/result attempt %d/%d failed: %s",
                ticket_id, attempt, _REPORT_ATTEMPTS, exc,
            )
        except Exception:  # noqa: BLE001 - a raise here strands the ticket permanently
            log.exception(
                "%s: execution/result attempt %d/%d failed", ticket_id, attempt, _REPORT_ATTEMPTS
            )
        if attempt < _REPORT_ATTEMPTS and not _stop:
            time.sleep(_REPORT_BACKOFF_SECONDS * attempt)

    # Loud on purpose: this is the one failure a human has to clean up by hand.
    log.error(
        "%s: STUCK IN EXECUTING, NEEDS MANUAL CLEANUP -- outcome %r never recorded after %d attempts",
        ticket_id, outcome, _REPORT_ATTEMPTS,
    )


def process_ticket(gate: GateClient, ticket: dict[str, Any]) -> None:
    ticket_id = ticket["ticketId"]
    parameters_hash = ticket["actionDetails"]["parametersHash"]

    try:
        started = gate.start_execution(ticket_id, parameters_hash)
    except GateError as exc:
        if exc.code == "HASH_MISMATCH":
            # The approved ticket is not the one this hash came from. Never
            # execute: a human must supersede. Nothing to report -- the ticket
            # is still APPROVED and stays that way until it expires.
            log.error("%s: parametersHash mismatch, refusing to execute", ticket_id)
        elif exc.code == "INVALID_STATE":
            # Almost always a restart mid-flight: the ticket is already
            # EXECUTING from a previous process. Re-executing is exactly the
            # thing the gate exists to prevent, so leave it for a human.
            log.warning("%s: %s -- leaving alone", ticket_id, exc)
        else:
            log.error("%s: execution/start failed: %s", ticket_id, exc)
        return

    # Execute from the gate's response, never from the polled copy: this is
    # the payload the gate just re-affirmed against the approved hash.
    action_details = started["actionDetails"]
    log.info(
        "%s: executing %s.%s in %s%s",
        ticket_id,
        action_details["service"],
        action_details["operation"],
        action_details["region"],
        " (DRY_RUN)" if settings.dry_run else "",
    )

    try:
        request_ids = execute(action_details)
    except ExecutionFailed as exc:
        log.error("%s: execution failed: %s", ticket_id, exc)
        # Report before raising anything else: a ticket stuck in EXECUTING is
        # invisible to the expiry sweep and needs manual cleanup.
        _report(gate, ticket_id, "failure", str(exc), [])
        return
    except Exception as exc:  # noqa: BLE001 - must not leave the ticket EXECUTING
        log.exception("%s: unexpected error during execution", ticket_id)
        _report(gate, ticket_id, "failure", f"unexpected error: {exc}", [])
        return

    _report(gate, ticket_id, "success", "executed as approved", request_ids)
    log.info("%s: done, awsRequestIds=%s", ticket_id, request_ids)


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    log.info(
        "executor starting: gate=%s server_id=%s dry_run=%s",
        settings.gate_base_url, settings.gate_server_id, settings.dry_run,
    )
    if settings.dry_run:
        log.warning("DRY_RUN is on -- no AWS call will be made. Set DRY_RUN=false to arm.")

    gate = GateClient()
    try:
        while not _stop:
            try:
                approved = gate.list_approved()
            except GateError as exc:
                # Auth and connectivity problems are transient often enough
                # that dying on them just hands the restart to the kubelet.
                log.error("poll failed: %s", exc)
                approved = []
            except Exception:  # noqa: BLE001
                log.exception("poll failed")
                approved = []

            for ticket in approved:
                if _stop:
                    break
                # Last line of defence. One bad ticket must not take the loop down with it -- the
                # kubelet would restart us into the same ticket and the same crash, forever.
                try:
                    process_ticket(gate, ticket)
                except Exception:  # noqa: BLE001
                    log.exception("%s: unhandled error, skipping", ticket.get("ticketId"))

            delay = settings.poll_interval_seconds + random.uniform(
                0, settings.poll_jitter_seconds
            )
            # Sleep in slices so SIGTERM doesn't wait out a full interval.
            waited = 0.0
            while waited < delay and not _stop:
                time.sleep(min(1.0, delay - waited))
                waited += 1.0
    finally:
        gate.close()

    log.info("executor stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
