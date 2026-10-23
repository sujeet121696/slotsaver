"""Caller abstraction: the engine talks to phones only through this interface.

MockCaller scripts outcomes for local testing. The real providers live in
slotsaver/providers/ (CALL-E, and Vobiz via Bolna). The engine never knows
which one it's using.

WRONG_PERSON_OR_UNCLEAR covers both "someone else picked up" and "the call
gave us no usable result". Either way we don't know what happened, so it is
never treated as a no-answer (no retry) or a cancellation (no slot freed).
"""

import os
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .models import Appointment, WaitlistEntry


class StopRun(RuntimeError):
    """A provider problem every later call would hit too (a rejected API key,
    an empty balance). Unlike a failed call, it stops the whole run."""


def allowed_phones() -> set[str]:
    """ALLOWED_DEMO_PHONES from .env. Every real provider checks this before
    dialing; it lives here (not in a provider module) so no provider's SDK
    has to be installed to read it."""
    raw = os.environ.get("ALLOWED_DEMO_PHONES", "")
    return {p.strip() for p in raw.split(",") if p.strip()}


def require_allowed(phone: str) -> None:
    """Raises unless the phone is allowlisted. Every real provider calls
    this before it contacts its API."""
    if phone not in allowed_phones():
        raise RuntimeError(
            f"refusing to dial {phone}: not in ALLOWED_DEMO_PHONES. "
            "Real calls may only reach phones we own."
        )


class ConfirmResult(str, Enum):
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    RESCHEDULE = "reschedule"
    NO_ANSWER = "no_answer"
    WRONG_PERSON_OR_UNCLEAR = "wrong_person_or_unclear"


class OfferResult(str, Enum):
    ACCEPTED = "accepted"
    DECLINED = "declined"
    NO_ANSWER = "no_answer"
    WRONG_PERSON_OR_UNCLEAR = "wrong_person_or_unclear"


@dataclass
class CallOutcome:
    result: ConfirmResult | OfferResult
    summary: str  # one-line human-readable outcome for the ops log


class Caller(Protocol):
    def confirm_call(self, appt: Appointment, clinic_name: str) -> CallOutcome: ...

    def offer_call(
        self, entry: WaitlistEntry, appt: Appointment, clinic_name: str
    ) -> CallOutcome: ...


class MockCaller:
    """Scripted outcomes keyed by phone number.

    confirm_script / offer_script map a phone number to a list of results,
    consumed one per call — so "no answer, then confirmed on retry" is
    ["no_answer", "confirmed"]. Unscripted numbers default to confirmed/accepted.
    """

    def __init__(
        self,
        confirm_script: dict[str, list[ConfirmResult]] | None = None,
        offer_script: dict[str, list[OfferResult]] | None = None,
    ) -> None:
        self.confirm_script = confirm_script or {}
        self.offer_script = offer_script or {}
        self.calls_placed = 0

    def _next(self, script: dict[str, list], phone: str, default):
        queue = script.get(phone)
        if queue:
            return queue.pop(0)
        return default

    def confirm_call(self, appt: Appointment, clinic_name: str) -> CallOutcome:
        self.calls_placed += 1
        result = self._next(self.confirm_script, appt.phone, ConfirmResult.CONFIRMED)
        summaries = {
            ConfirmResult.CONFIRMED: f'{appt.patient} confirmed {appt.time}',
            ConfirmResult.CANCELLED: f'{appt.patient} cancelled {appt.time}',
            ConfirmResult.RESCHEDULE: f'{appt.patient} asked to reschedule {appt.time}',
            ConfirmResult.NO_ANSWER: f'{appt.patient} did not answer',
            ConfirmResult.WRONG_PERSON_OR_UNCLEAR: f'unclear who answered for {appt.patient}',
        }
        return CallOutcome(result, summaries[result])

    def offer_call(
        self, entry: WaitlistEntry, appt: Appointment, clinic_name: str
    ) -> CallOutcome:
        self.calls_placed += 1
        result = self._next(self.offer_script, entry.phone, OfferResult.ACCEPTED)
        summaries = {
            OfferResult.ACCEPTED: f'{entry.name} accepted the {appt.time} slot',
            OfferResult.DECLINED: f'{entry.name} declined the {appt.time} slot',
            OfferResult.NO_ANSWER: f'{entry.name} did not answer',
            OfferResult.WRONG_PERSON_OR_UNCLEAR: f'unclear who answered for {entry.name}',
        }
        return CallOutcome(result, summaries[result])
