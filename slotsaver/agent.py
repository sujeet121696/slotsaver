"""The Strands agent brain: Asha's dispatcher for the evening run.

The agent orchestrates the same loop engine.py runs deterministically — but as
an LLM agent (AWS Strands Agents SDK) deciding which call to place next. All
state mutations happen inside tools, keyed by ids the tools hand out; the model
never edits the schedule directly, it only sequences calls.

The LLM provider is Groq through Strands' OpenAI-compatible model (set
GROQ_API_KEY in .env). engine.py remains the zero-dependency fallback brain.
"""

import json

from strands import Agent, tool
from strands.models.openai import OpenAIModel

from .caller import Caller, ConfirmResult, OfferResult
from .config import Config
from .models import ClinicState, SlotStatus

SYSTEM_PROMPT = """\
You are the dispatcher for {persona}, the phone assistant of {clinic}.
Tonight you must work through tomorrow's appointment list:

1. Call get_worklist first to see the schedule and the waitlist.
2. For every appointment with status "scheduled", place a confirm_call.
   - If the result is no_answer, retry exactly once. If still no answer,
     move on (the tool flags it for the clinic).
3. Whenever a patient cancels or reschedules, the slot is free: offer it to
   the waitlist with offer_slot, strictly in waitlist order, until someone
   accepts or the waitlist is exhausted. Never skip ahead.
4. Never call the same person more than twice tonight.
5. When every appointment is handled, call get_morning_report and present it
   to the clinic owner in 2-3 friendly sentences, including rupees recovered.

Work step by step and place one call at a time."""


def build_agent(state: ClinicState, caller: Caller, config: Config) -> Agent:
    """Wires the shared clinic state and a Caller into a Strands agent."""

    recovered = {"total": 0}

    def _appt(appointment_id: str):
        for appt in state.appointments:
            if appt.id == appointment_id:
                return appt
        return None

    @tool
    def get_worklist() -> str:
        """Tomorrow's appointments and the waitlist, with ids and statuses."""
        return json.dumps(
            {
                "appointments": [
                    {
                        "id": a.id,
                        "patient": a.patient,
                        "time": a.time,
                        "doctor": a.doctor,
                        "fee_inr": a.fee,
                        "status": a.status.value,
                    }
                    for a in state.appointments
                ],
                "waitlist": [
                    {"id": w.id, "name": w.name} for w in state.waitlist
                ],
            }
        )

    @tool
    def confirm_call(appointment_id: str) -> str:
        """Place a confirmation call for one appointment. Returns the outcome:
        confirmed, cancelled, reschedule, or no_answer."""
        appt = _appt(appointment_id)
        if appt is None:
            return f"error: no appointment with id {appointment_id}"
        outcome = caller.confirm_call(appt, state.clinic_name)
        state.log(f"Confirm call → {outcome.summary}")
        result = outcome.result
        if result is ConfirmResult.CONFIRMED:
            appt.status = SlotStatus.CONFIRMED
        elif result is ConfirmResult.CANCELLED:
            appt.status = SlotStatus.CANCELLED
        elif result is ConfirmResult.RESCHEDULE:
            appt.status = SlotStatus.RESCHEDULED
            state.log(f"{appt.patient} moved to a future slot; {appt.time} freed")
        else:
            appt.status = SlotStatus.NEEDS_ATTENTION
            state.log(f"{appt.patient} unreachable — flagged for the clinic")
        state.notify()
        return json.dumps({"result": result.value, "summary": outcome.summary})

    @tool
    def offer_slot(waitlist_id: str, appointment_id: str) -> str:
        """Offer a freed slot to one waitlist entry. On accept, the slot is
        rebooked to them automatically. Returns accepted/declined/no_answer."""
        appt = _appt(appointment_id)
        if appt is None:
            return f"error: no appointment with id {appointment_id}"
        entry = next((w for w in state.waitlist if w.id == waitlist_id), None)
        if entry is None:
            return f"error: no waitlist entry with id {waitlist_id}"
        outcome = caller.offer_call(entry, appt, state.clinic_name)
        state.log(f"Backfill call → {outcome.summary}")
        if outcome.result is OfferResult.ACCEPTED:
            appt.status = SlotStatus.BACKFILLED
            appt.patient = entry.name
            appt.phone = entry.phone
            state.waitlist.remove(entry)
            recovered["total"] += appt.fee
            state.log(f"Slot {appt.time} refilled — ₹{appt.fee} recovered")
        return json.dumps(
            {"result": outcome.result.value, "summary": outcome.summary}
        )

    @tool
    def get_morning_report() -> str:
        """The morning report: counts per status and rupees recovered."""
        counts = {status: 0 for status in SlotStatus}
        for appt in state.appointments:
            counts[appt.status] += 1
        report = {
            "confirmed": counts[SlotStatus.CONFIRMED],
            "cancelled": counts[SlotStatus.CANCELLED] + counts[SlotStatus.BACKFILLED],
            "backfilled": counts[SlotStatus.BACKFILLED],
            "rescheduled": counts[SlotStatus.RESCHEDULED],
            "needs_attention": counts[SlotStatus.NEEDS_ATTENTION],
            "recovered_inr": recovered["total"],
        }
        state.log(
            f"Morning report: {report['confirmed']} confirmed · "
            f"{report['cancelled']} cancelled · {report['backfilled']} backfilled · "
            f"₹{report['recovered_inr']} recovered"
        )
        return json.dumps(report)

    model = OpenAIModel(
        client_args={
            "api_key": config.groq_api_key,
            "base_url": config.groq_base_url,
        },
        model_id=config.groq_model,
    )
    return Agent(
        model=model,
        tools=[get_worklist, confirm_call, offer_slot, get_morning_report],
        system_prompt=SYSTEM_PROMPT.format(
            persona=config.agent_persona, clinic=config.clinic_name
        ),
    )


def run_evening_agent(state: ClinicState, caller: Caller, config: Config) -> str:
    """Runs the whole evening as one agent conversation; returns its report."""
    agent = build_agent(state, caller, config)
    result = agent(
        "It's 7pm — run tonight's confirmation round for tomorrow's schedule."
    )
    return str(result)
