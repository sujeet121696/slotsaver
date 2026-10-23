"""The evening run's tools: the ONLY code that changes ClinicState.

Both brains drive these same tools. engine.py calls them in a fixed order;
agent.py exposes them to the LLM, which only chooses the order. The guards
live here rather than in the agent's prompt so a model that ignores its
instructions still can't overwrite a confirmed patient, skip the waitlist
order or call someone a third time.
"""

from .caller import Caller, CallOutcome, ConfirmResult, OfferResult, StopRun
from .models import Appointment, ClinicState, SlotStatus, WaitlistEntry

MAX_CALLS_PER_PERSON = 2


class GuardError(Exception):
    """A tool call that would break a rule of the run. Nothing was dialed."""


class EveningTools:
    def __init__(self, state: ClinicState, caller: Caller) -> None:
        self.state = state
        self.caller = caller
        self.recovered = 0
        self._calls: dict[str, int] = {}  # appointment/waitlist id -> calls tonight
        self._retry_pending: set[str] = set()  # appointments owed their one retry
        self._offered: dict[str, set[str]] = {}  # appointment id -> waitlist ids offered
        self._backfill_paused: set[str] = set()  # slots waiting on a human
        # The first StopRun from the caller. Kept here, not only raised,
        # because Strands turns a tool's exception into text for the model,
        # which could then try the next call anyway.
        self.stopped: StopRun | None = None

    def _dial(self, place_call, *args) -> CallOutcome:
        try:
            return place_call(*args)
        except StopRun as err:
            self.stopped = err
            self.state.log(f"Run stopped: {err}")
            raise

    def appointment(self, appointment_id: str) -> Appointment:
        for appt in self.state.appointments:
            if appt.id == appointment_id:
                return appt
        raise GuardError(f"no appointment with id {appointment_id}")

    def confirm(self, appointment_id: str) -> CallOutcome:
        """Confirm call, or the one retry after a no-answer."""
        if self.stopped is not None:
            raise self.stopped
        appt = self.appointment(appointment_id)
        is_retry = appt.id in self._retry_pending
        if not is_retry and appt.status is not SlotStatus.SCHEDULED:
            raise GuardError(
                f"{appt.id} is already {appt.status.value}; only a no-answer gets a retry"
            )
        self._count_call(appt.id)

        outcome = self._dial(self.caller.confirm_call, appt, self.state.clinic_name)
        self._retry_pending.discard(appt.id)
        self.state.log(f"{'Retry call' if is_retry else 'Confirm call'} → {outcome.summary}")

        result = outcome.result
        if result is ConfirmResult.CONFIRMED:
            appt.status = SlotStatus.CONFIRMED
        elif result is ConfirmResult.CANCELLED:
            appt.status = SlotStatus.CANCELLED
        elif result is ConfirmResult.RESCHEDULE:
            # The patient keeps a future booking (the clinic calls them back
            # to fix it), so this slot isn't offered to the waitlist.
            appt.status = SlotStatus.RESCHEDULED
            self.state.log(f"{appt.patient} moved to a future slot; {appt.time} freed")
        elif result is ConfirmResult.NO_ANSWER:
            appt.status = SlotStatus.NEEDS_ATTENTION
            if is_retry:
                self.state.log(f"{appt.patient} unreachable — flagged for the clinic")
            else:
                self._retry_pending.add(appt.id)
        else:
            # Wrong person or an unreadable result: we don't know what
            # happened, so nothing is retried, freed or offered.
            appt.status = SlotStatus.NEEDS_ATTENTION
            self.state.log(
                f"{appt.patient}: couldn't confirm who answered — flagged for a human to check"
            )
        self.state.notify()
        return outcome

    def next_candidate(self, appointment_id: str) -> WaitlistEntry | None:
        """The only waitlist entry an offer for this slot may go to next."""
        offered = self._offered.get(appointment_id, set())
        for entry in self.state.waitlist:
            if entry.id in offered:
                continue
            if self._calls.get(entry.id, 0) >= MAX_CALLS_PER_PERSON:
                continue
            return entry
        return None

    def offer(self, waitlist_id: str, appointment_id: str) -> CallOutcome:
        """Offer a cancelled slot to the next waitlist entry, in order."""
        if self.stopped is not None:
            raise self.stopped
        appt = self.appointment(appointment_id)
        if appt.status is not SlotStatus.CANCELLED:
            raise GuardError(
                f"{appt.id} is {appt.status.value}; only a cancelled slot can be offered"
            )
        if appt.id in self._backfill_paused:
            raise GuardError(f"backfill for {appt.id} is paused for a human to check")
        candidate = self.next_candidate(appt.id)
        if candidate is None:
            raise GuardError(f"nobody left on the waitlist to offer {appt.id} to")
        if candidate.id != waitlist_id:
            raise GuardError(
                f"waitlist order: offer {appt.id} to {candidate.id} ({candidate.name}) next"
            )
        self._count_call(candidate.id)
        self._offered.setdefault(appt.id, set()).add(candidate.id)

        outcome = self._dial(
            self.caller.offer_call, candidate, appt, self.state.clinic_name
        )
        self.state.log(f"Backfill call → {outcome.summary}")
        if outcome.result is OfferResult.ACCEPTED:
            appt.status = SlotStatus.BACKFILLED
            appt.patient = candidate.name
            appt.phone = candidate.phone
            self.state.waitlist.remove(candidate)
            self.recovered += appt.fee
            self.state.log(f"Slot {appt.time} refilled — ₹{appt.fee} recovered")
        elif outcome.result is OfferResult.WRONG_PERSON_OR_UNCLEAR:
            self._backfill_paused.add(appt.id)
            self.state.log(
                f"Slot {appt.time}: couldn't confirm who answered — "
                "backfill paused for a human to check"
            )
        return outcome

    def backfill_paused(self, appointment_id: str) -> bool:
        return appointment_id in self._backfill_paused

    def morning_report(self) -> dict:
        counts = {status: 0 for status in SlotStatus}
        for appt in self.state.appointments:
            counts[appt.status] += 1
        report = {
            "confirmed": counts[SlotStatus.CONFIRMED],
            "cancelled": counts[SlotStatus.CANCELLED] + counts[SlotStatus.BACKFILLED],
            "backfilled": counts[SlotStatus.BACKFILLED],
            "rescheduled": counts[SlotStatus.RESCHEDULED],
            "needs_attention": counts[SlotStatus.NEEDS_ATTENTION],
            "recovered": self.recovered,
        }
        self.state.log(
            f"Morning report: {report['confirmed']} confirmed · "
            f"{report['cancelled']} cancelled · {report['backfilled']} backfilled · "
            f"₹{report['recovered']} recovered"
        )
        return report

    def _count_call(self, person_id: str) -> None:
        calls = self._calls.get(person_id, 0)
        if calls >= MAX_CALLS_PER_PERSON:
            raise GuardError(f"{person_id} has already had {calls} calls tonight")
        self._calls[person_id] = calls + 1
