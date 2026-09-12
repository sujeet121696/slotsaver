"""The evening run: confirm every appointment, backfill every cancellation."""

from .caller import Caller, ConfirmResult, OfferResult
from .models import Appointment, ClinicState, SlotStatus


def run_evening(state: ClinicState, caller: Caller) -> dict:
    """Runs the full confirm -> backfill loop and returns the morning report."""
    recovered = 0
    for appt in list(state.appointments):
        if appt.status is not SlotStatus.SCHEDULED:
            continue
        outcome = _confirm_with_retry(state, caller, appt)
        if outcome is ConfirmResult.CANCELLED:
            recovered += _backfill(state, caller, appt)

    report = _report(state, recovered)
    state.log(
        f"Morning report: {report['confirmed']} confirmed · "
        f"{report['cancelled']} cancelled · {report['backfilled']} backfilled · "
        f"₹{report['recovered']} recovered"
    )
    return report


def _confirm_with_retry(state: ClinicState, caller: Caller, appt: Appointment) -> ConfirmResult:
    outcome = caller.confirm_call(appt, state.clinic_name)
    state.log(f"Confirm call → {outcome.summary}")
    if outcome.result is ConfirmResult.NO_ANSWER:
        outcome = caller.confirm_call(appt, state.clinic_name)
        state.log(f"Retry call → {outcome.summary}")

    result = outcome.result
    if result is ConfirmResult.CONFIRMED:
        appt.status = SlotStatus.CONFIRMED
    elif result is ConfirmResult.CANCELLED:
        appt.status = SlotStatus.CANCELLED
    elif result is ConfirmResult.RESCHEDULE:
        # Demo scope: rescheduling frees the slot like a cancellation, but the
        # patient keeps a future booking, so it isn't lost revenue.
        appt.status = SlotStatus.RESCHEDULED
        state.log(f"{appt.patient} moved to a future slot; {appt.time} freed")
    else:
        appt.status = SlotStatus.NEEDS_ATTENTION
        state.log(f"{appt.patient} unreachable — flagged for the clinic")
    state.notify()
    return result if isinstance(result, ConfirmResult) else ConfirmResult.NO_ANSWER


def _backfill(state: ClinicState, caller: Caller, appt: Appointment) -> int:
    """Calls the waitlist in order until someone takes the slot.

    Returns the fee recovered (0 if the slot stayed empty).
    """
    for entry in list(state.waitlist):
        outcome = caller.offer_call(entry, appt, state.clinic_name)
        state.log(f"Backfill call → {outcome.summary}")
        if outcome.result is OfferResult.ACCEPTED:
            appt.status = SlotStatus.BACKFILLED
            appt.patient = entry.name
            appt.phone = entry.phone
            state.waitlist.remove(entry)
            state.log(f"Slot {appt.time} refilled — ₹{appt.fee} recovered")
            return appt.fee
    state.log(f"Slot {appt.time} could not be refilled tonight")
    return 0


def _report(state: ClinicState, recovered: int) -> dict:
    counts = {status: 0 for status in SlotStatus}
    for appt in state.appointments:
        counts[appt.status] += 1
    return {
        "confirmed": counts[SlotStatus.CONFIRMED],
        "cancelled": counts[SlotStatus.CANCELLED] + counts[SlotStatus.BACKFILLED],
        "backfilled": counts[SlotStatus.BACKFILLED],
        "rescheduled": counts[SlotStatus.RESCHEDULED],
        "needs_attention": counts[SlotStatus.NEEDS_ATTENTION],
        "recovered": recovered,
    }
