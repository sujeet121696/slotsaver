"""The evening run: confirm every appointment, backfill every cancellation."""

from .caller import Caller, ConfirmResult, OfferResult
from .models import Appointment, ClinicState, SlotStatus
from .tools import EveningTools


def run_evening(state: ClinicState, caller: Caller) -> dict:
    """Runs the full confirm -> backfill loop and returns the morning report."""
    tools = EveningTools(state, caller)
    for appt in list(state.appointments):
        if appt.status is not SlotStatus.SCHEDULED:
            continue
        outcome = tools.confirm(appt.id)
        if outcome.result is ConfirmResult.NO_ANSWER:
            outcome = tools.confirm(appt.id)
        if outcome.result is ConfirmResult.CANCELLED:
            _backfill(tools, appt)
    return tools.morning_report()


def _backfill(tools: EveningTools, appt: Appointment) -> None:
    """Calls the waitlist in order until someone takes the slot."""
    while (entry := tools.next_candidate(appt.id)) is not None:
        outcome = tools.offer(entry.id, appt.id)
        if outcome.result is OfferResult.ACCEPTED:
            return
        if tools.backfill_paused(appt.id):
            return
    tools.state.log(f"Slot {appt.time} could not be refilled tonight")
