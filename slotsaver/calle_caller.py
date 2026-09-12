"""Real phone calls through CALL-E's one-shot Calls API (calle-ai SDK).

Implements the same Caller protocol as MockCaller, so the engine and the
Strands agent are oblivious to which one is wired in. Each call sends a
natural-language task (Asha's script) plus a result_schema, so CALL-E itself
returns the structured outcome — no transcript parsing here.

Safety: refuses to dial any number not in ALLOWED_DEMO_PHONES (.env,
comma-separated). The demo dataset's numbers are fictional; only phones we
own may ever ring.
"""

import os
import uuid

from calle import CalleClient

from .caller import CallOutcome, ConfirmResult, OfferResult
from .models import Appointment, WaitlistEntry

_CONFIRM_SCHEMA = {
    "type": "object",
    "required": ["outcome"],
    "properties": {
        "outcome": {
            "type": "string",
            "enum": ["confirmed", "cancelled", "reschedule", "no_answer"],
        },
        "notes": {"type": "string"},
    },
    "additionalProperties": False,
}

_OFFER_SCHEMA = {
    "type": "object",
    "required": ["outcome"],
    "properties": {
        "outcome": {"type": "string", "enum": ["accepted", "declined", "no_answer"]},
        "notes": {"type": "string"},
    },
    "additionalProperties": False,
}


def allowed_phones() -> set[str]:
    raw = os.environ.get("ALLOWED_DEMO_PHONES", "")
    return {p.strip() for p in raw.split(",") if p.strip()}


class CalleCaller:
    """Places real calls; persona and clinic name flow into every task."""

    def __init__(
        self,
        api_key: str,
        persona: str = "Asha",
        region: str = "IN",
        locale: str = "en-US",
        timeout_seconds: float = 300.0,
    ) -> None:
        self.client = CalleClient(api_key=api_key)
        self.persona = persona
        self.region = region
        self.locale = locale
        self.timeout_seconds = timeout_seconds
        self.calls_placed = 0
        self._attempts: dict[str, int] = {}
        # Idempotency keys dedupe server-side FOREVER, so they must be unique
        # per run — otherwise a rerun gets yesterday's cached call back
        # instead of dialing.
        self.run_id = uuid.uuid4().hex[:8]

    def _guard(self, phone: str) -> None:
        if phone not in allowed_phones():
            raise RuntimeError(
                f"refusing to dial {phone}: not in ALLOWED_DEMO_PHONES. "
                "Real calls may only reach phones we own."
            )

    def _run(self, key_base: str, phone: str, task: str, schema: dict) -> dict:
        self._guard(phone)
        n = self._attempts[key_base] = self._attempts.get(key_base, 0) + 1
        self.calls_placed += 1
        call = self.client.calls.create_and_wait(
            task=task,
            recipients=[
                {"phones": [phone], "region": self.region, "locale": self.locale}
            ],
            result_schema=schema,
            idempotency_key=f"slotsaver-{self.run_id}-{key_base}-attempt{n}",
            interval_seconds=2.0,
            timeout_seconds=self.timeout_seconds,
        )
        result = call.get("structured_result") or {}
        if call.get("status") != "completed" or "outcome" not in result:
            attempts = (call.get("recipients") or [{}])[0].get("attempts") or []
            sip = attempts[-1].get("failure_code") if attempts else None
            notes = f"call status: {call.get('status')}"
            if call.get("failure_message"):
                notes += f" · {call['failure_message']}"
            if sip:
                notes += f" · SIP {sip}"
            return {"outcome": "no_answer", "notes": notes}
        return result

    def confirm_call(self, appt: Appointment, clinic_name: str) -> CallOutcome:
        task = (
            f"You are {self.persona}, the friendly phone assistant of {clinic_name}. "
            f"Call {appt.patient} and confirm their appointment TOMORROW at "
            f"{appt.time} with {appt.doctor}. Introduce yourself as the clinic's "
            f"assistant up front. You are talking to a person — never press any "
            f"phone keys and never wait on hold. The moment you have the answer, "
            f"thank them, say goodbye, and end the call; keep it under 45 seconds. "
            f"Outcomes: they will come (confirmed); they cancel "
            f"(cancelled) — thank them and say the slot will be freed; they want a "
            f"different time (reschedule); nobody answered or it wasn't them "
            f"(no_answer)."
        )
        result = self._run(f"{appt.id}-confirm", appt.phone, task, _CONFIRM_SCHEMA)
        outcome = ConfirmResult(result["outcome"])
        summaries = {
            ConfirmResult.CONFIRMED: f"{appt.patient} confirmed {appt.time}",
            ConfirmResult.CANCELLED: f"{appt.patient} cancelled {appt.time}",
            ConfirmResult.RESCHEDULE: f"{appt.patient} asked to reschedule {appt.time}",
            ConfirmResult.NO_ANSWER: f"{appt.patient} did not answer",
        }
        summary = summaries[outcome]
        if outcome is ConfirmResult.NO_ANSWER and result.get("notes"):
            summary += f" ({result['notes']})"
        return CallOutcome(outcome, summary)

    def offer_call(
        self, entry: WaitlistEntry, appt: Appointment, clinic_name: str
    ) -> CallOutcome:
        task = (
            f"You are {self.persona}, the friendly phone assistant of {clinic_name}. "
            f"Call {entry.name}, who asked us for an earlier appointment. A slot "
            f"just opened TOMORROW at {appt.time} with {appt.doctor}. Offer it to "
            f"them. Introduce yourself as the clinic's assistant up front. You are "
            f"talking to a person — never press any phone keys and never wait on "
            f"hold. The moment you have the answer, thank them, say goodbye, and "
            f"end the call; keep it under 45 seconds. "
            f"Outcomes: they take the slot (accepted) — "
            f"tell them they are booked and the clinic will see them tomorrow; they "
            f"don't want it (declined) — they stay on the list; nobody answered "
            f"(no_answer)."
        )
        result = self._run(
            f"{appt.id}-offer-{entry.id}", entry.phone, task, _OFFER_SCHEMA
        )
        outcome = OfferResult(result["outcome"])
        summaries = {
            OfferResult.ACCEPTED: f"{entry.name} accepted the {appt.time} slot",
            OfferResult.DECLINED: f"{entry.name} declined the {appt.time} slot",
            OfferResult.NO_ANSWER: f"{entry.name} did not answer",
        }
        summary = summaries[outcome]
        if outcome is OfferResult.NO_ANSWER and result.get("notes"):
            summary += f" ({result['notes']})"
        return CallOutcome(outcome, summary)
