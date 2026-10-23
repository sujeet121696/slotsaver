"""Real phone calls on a Vobiz number, run by a Bolna voice agent (TELEPHONY.md option B).

Vobiz is telephony only (numbers, SIP, audio streams) and has no AI of its
own, so the conversation runs on Bolna, which dials out through the Vobiz
number linked in its dashboard. This module talks only to Bolna's REST API:
  POST /call                       start one outbound call -> execution_id
  GET  /executions/{execution_id}  poll until the call reaches an end status
Pure stdlib (urllib), so nothing new needs installing. The one-time Bolna
agent setup this expects is in docs/TELEPHONY.md §6.

Outcome mapping, following docs/RULES.md §2:
  - completed, with a valid outcome extracted -> that outcome
  - carrier said no-answer or busy, or voicemail picked up -> no_answer
    (the phone rang and no person spoke, so the engine's retry is right)
  - rescheduled by Bolna's calling-hours guardrail -> wrong_person_or_unclear
    at once: the call didn't happen now and may ring later, so a human
    has to know
  - anything else (failed, error, stopped, no valid outcome, no end status
    before the timeout, a failed start) -> wrong_person_or_unclear
  - a rejected API key (401/403) or a low Bolna balance raises StopRun:
    every later call would fail too.

Bolna's POST /call takes no idempotency key. So a call is counted before
the POST, and a POST that fails or times out is never sent again: the call
may already be ringing, and a second POST would ring the person twice.
Polls are reads, so a failed poll is simply tried again.
"""

import json
import time
import urllib.error
import urllib.request
from typing import Callable

from ..caller import CallOutcome, ConfirmResult, OfferResult, StopRun, require_allowed
from ..models import Appointment, WaitlistEntry
from .scripts import confirm_summary, confirm_task, offer_summary, offer_task

BOLNA_BASE_URL = "https://api.bolna.ai"

# Where the agent's extraction puts the outcome (TELEPHONY.md §6). Bolna
# nests extracted_data as category -> extraction name -> result, and the
# predefined value the model picked is the result's "objective".
EXTRACTION_CATEGORY = "SlotSaver"
EXTRACTION_NAME = "Outcome"

# Statuses after which nothing changes. "call-disconnected" is not one:
# Bolna is still filling in extracted_data at that point.
_FINISHED = {"completed", "no-answer", "busy", "failed", "canceled", "stopped", "error"}
_NOT_ANSWERED = {"no-answer", "busy"}
_RESCHEDULED = {"scheduled", "rescheduled"}

_CONFIRM_OUTCOMES = {r.value for r in ConfirmResult}
_OFFER_OUTCOMES = {r.value for r in OfferResult}


class BolnaHTTPError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class VobizAuthError(StopRun):
    """Bolna rejected the API key."""


class VobizBalanceError(StopRun):
    """Bolna's balance is too low; every later call would end the same way."""


# (method, url, JSON body or None, headers) -> parsed JSON response
Transport = Callable[[str, str, dict | None, dict], dict]


def urllib_transport(method: str, url: str, body: dict | None, headers: dict) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        with err:
            detail = err.read().decode(errors="replace")[:200]
        raise BolnaHTTPError(err.code, detail) from None


class VobizCaller:
    """Places real calls through Bolna on a Vobiz number."""

    def __init__(
        self,
        api_key: str,
        agent_id: str,
        persona: str = "Asha",
        from_number: str | None = None,
        timeout_seconds: float = 300.0,
        poll_seconds: float = 2.0,
        base_url: str = BOLNA_BASE_URL,
        transport: Transport = urllib_transport,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.api_key = api_key
        self.agent_id = agent_id
        self.persona = persona
        self.from_number = from_number
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        self.base_url = base_url.rstrip("/")
        self._transport = transport
        self._sleep = sleep
        self._clock = clock
        self.calls_placed = 0
        self._attempts: dict[str, int] = {}

    def confirm_call(self, appt: Appointment, clinic_name: str) -> CallOutcome:
        task = confirm_task(self.persona, clinic_name, appt)
        result = self._run(f"{appt.id}-confirm", appt.phone, task, _CONFIRM_OUTCOMES)
        outcome = ConfirmResult(result["outcome"])
        return CallOutcome(outcome, confirm_summary(appt, outcome, result["notes"]))

    def offer_call(
        self, entry: WaitlistEntry, appt: Appointment, clinic_name: str
    ) -> CallOutcome:
        task = offer_task(self.persona, clinic_name, entry, appt)
        result = self._run(
            f"{appt.id}-offer-{entry.id}", entry.phone, task, _OFFER_OUTCOMES
        )
        outcome = OfferResult(result["outcome"])
        return CallOutcome(outcome, offer_summary(entry, appt, outcome, result["notes"]))

    def _run(self, key_base: str, phone: str, task: str, valid: set[str]) -> dict:
        require_allowed(phone)
        n = self._attempts[key_base] = self._attempts.get(key_base, 0) + 1
        self.calls_placed += 1
        body = {
            "agent_id": self.agent_id,
            "recipient_phone_number": phone,
            "user_data": {"task": task, "slotsaver_call": f"{key_base}-attempt{n}"},
        }
        if self.from_number:
            body["from_phone_number"] = self.from_number
        try:
            started = self._request("POST", "/call", body)
        except StopRun:
            raise
        except (BolnaHTTPError, OSError, ValueError) as err:
            return _unclear(f"Bolna error starting the call: {err}")
        execution_id = started.get("execution_id")
        if not execution_id:
            return _unclear("Bolna returned no execution_id")
        return self._wait(execution_id, valid)

    def _wait(self, execution_id: str, valid: set[str]) -> dict:
        deadline = self._clock() + self.timeout_seconds
        status = "unknown"
        while True:
            try:
                execution = self._request("GET", f"/executions/{execution_id}")
            except StopRun:
                raise
            except (BolnaHTTPError, OSError, ValueError):
                execution = None
            if execution is not None:
                status = execution.get("status") or status
                if status == "balance-low":
                    raise VobizBalanceError("Bolna balance is too low; top up and rerun")
                if status in _RESCHEDULED:
                    return _unclear(
                        f"Bolna {status} the call (calling-hours guardrail?); "
                        "it may ring later"
                    )
                if status in _FINISHED:
                    return _classify(execution, valid)
            if self._clock() >= deadline:
                return _unclear(
                    f"no final status after {self.timeout_seconds:.0f}s (last: {status})"
                )
            self._sleep(self.poll_seconds)

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            return self._transport(method, f"{self.base_url}{path}", body, headers)
        except BolnaHTTPError as err:
            if err.status in (401, 403):
                raise VobizAuthError(f"Bolna rejected the API key ({err})") from None
            raise


def _unclear(notes: str) -> dict:
    return {"outcome": "wrong_person_or_unclear", "notes": notes}


def _extracted_outcome(execution: dict) -> dict:
    """The SlotSaver > Outcome extraction result, or {} if it isn't there."""
    data = execution.get("extracted_data")
    category = data.get(EXTRACTION_CATEGORY) if isinstance(data, dict) else None
    result = category.get(EXTRACTION_NAME) if isinstance(category, dict) else None
    return result if isinstance(result, dict) else {}


def _classify(execution: dict, valid: set[str]) -> dict:
    """Turns a finished Bolna execution into {"outcome", "notes"}."""
    status = execution.get("status")
    if status in _NOT_ANSWERED:
        return {"outcome": "no_answer", "notes": f"carrier: {status}"}
    if execution.get("answered_by_voice_mail"):
        return {"outcome": "no_answer", "notes": "voicemail"}
    extracted = _extracted_outcome(execution)
    outcome = extracted.get("objective")
    if status == "completed" and outcome in valid:
        return {"outcome": outcome, "notes": str(extracted.get("subjective") or "")}
    notes = f"call status: {status}"
    if execution.get("error_message"):
        notes += f" · {execution['error_message']}"
    hangup = (execution.get("telephony_data") or {}).get("hangup_reason")
    if hangup:
        notes += f" · {hangup}"
    if status == "completed":
        notes += f" · no valid {EXTRACTION_CATEGORY} > {EXTRACTION_NAME} extracted"
    return _unclear(notes)
