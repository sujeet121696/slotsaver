"""Caller abstraction: the engine talks to phones only through this interface.

MockCaller scripts outcomes for local testing; CalleCaller (later) places real
calls through CALL-E's plan_call -> run_call -> get_call_run loop. The engine
never knows which one it's using.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .models import Appointment, WaitlistEntry


class ConfirmResult(str, Enum):
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    RESCHEDULE = "reschedule"
    NO_ANSWER = "no_answer"


class OfferResult(str, Enum):
    ACCEPTED = "accepted"
    DECLINED = "declined"
    NO_ANSWER = "no_answer"


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
        }
        return CallOutcome(result, summaries[result])
