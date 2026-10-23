"""Real phone calls through CALL-E's one-shot Calls API (calle-ai SDK).

Implements the same Caller protocol as MockCaller, so the engine and the
Strands agent are oblivious to which one is wired in. Each call sends a
natural-language task (Asha's script) plus a result_schema, so CALL-E itself
returns the structured outcome — no transcript parsing here.

A call that never completes, completes without a valid outcome, or fails
inside the SDK (timeout, network, API error) comes back as
wrong_person_or_unclear rather than no_answer: we don't know what happened
on it, so it must not be retried or treated as a cancellation. A rejected
API key raises CalleKeyRejected (a StopRun): that's a setup problem, and
every call would fail.

Safety: refuses to dial any number not in ALLOWED_DEMO_PHONES (.env,
comma-separated). The demo dataset's numbers are fictional; only phones we
own may ever ring.
"""

import uuid

from calle import (
    CalleAPIError,
    CalleAuthenticationError,
    CalleClient,
    CalleConnectionError,
    CalleTimeoutError,
)

from ..caller import CallOutcome, ConfirmResult, OfferResult, StopRun, require_allowed
from ..models import Appointment, WaitlistEntry
from .scripts import confirm_summary, confirm_task, offer_summary, offer_task


class CalleKeyRejected(StopRun):
    """CALL-E rejected the API key."""


_CONFIRM_SCHEMA = {
    "type": "object",
    "required": ["outcome"],
    "properties": {
        "outcome": {
            "type": "string",
            "enum": [
                "confirmed",
                "cancelled",
                "reschedule",
                "no_answer",
                "wrong_person_or_unclear",
            ],
        },
        "notes": {"type": "string"},
    },
    "additionalProperties": False,
}

_OFFER_SCHEMA = {
    "type": "object",
    "required": ["outcome"],
    "properties": {
        "outcome": {
            "type": "string",
            "enum": ["accepted", "declined", "no_answer", "wrong_person_or_unclear"],
        },
        "notes": {"type": "string"},
    },
    "additionalProperties": False,
}


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

    def _run(self, key_base: str, phone: str, task: str, schema: dict) -> dict:
        require_allowed(phone)
        n = self._attempts[key_base] = self._attempts.get(key_base, 0) + 1
        self.calls_placed += 1
        try:
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
        except CalleAuthenticationError as err:
            raise CalleKeyRejected(f"CALL-E rejected the API key ({err})") from err
        except (CalleAPIError, CalleConnectionError, CalleTimeoutError) as err:
            # The call may or may not have rung. Flag it rather than crash
            # the run (the agent brain would otherwise stop mid-evening).
            return {"outcome": "wrong_person_or_unclear", "notes": f"CALL-E error: {err}"}
        result = call.get("structured_result") or {}
        valid = schema["properties"]["outcome"]["enum"]
        if call.get("status") != "completed" or result.get("outcome") not in valid:
            attempts = (call.get("recipients") or [{}])[0].get("attempts") or []
            sip = attempts[-1].get("failure_code") if attempts else None
            notes = f"call status: {call.get('status')}"
            if call.get("failure_message"):
                notes += f" · {call['failure_message']}"
            if sip:
                notes += f" · SIP {sip}"
            return {"outcome": "wrong_person_or_unclear", "notes": notes}
        return result

    def confirm_call(self, appt: Appointment, clinic_name: str) -> CallOutcome:
        task = confirm_task(self.persona, clinic_name, appt)
        result = self._run(f"{appt.id}-confirm", appt.phone, task, _CONFIRM_SCHEMA)
        outcome = ConfirmResult(result["outcome"])
        return CallOutcome(outcome, confirm_summary(appt, outcome, result.get("notes", "")))

    def offer_call(
        self, entry: WaitlistEntry, appt: Appointment, clinic_name: str
    ) -> CallOutcome:
        task = offer_task(self.persona, clinic_name, entry, appt)
        result = self._run(
            f"{appt.id}-offer-{entry.id}", entry.phone, task, _OFFER_SCHEMA
        )
        outcome = OfferResult(result["outcome"])
        return CallOutcome(
            outcome, offer_summary(entry, appt, outcome, result.get("notes", ""))
        )
