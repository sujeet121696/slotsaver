"""The Strands agent brain: Asha's dispatcher for the evening run.

The agent orchestrates the same loop engine.py runs deterministically — but as
an LLM agent (AWS Strands Agents SDK) deciding which call to place next. All
state mutations happen inside tools.py's EveningTools, keyed by ids the tools
hand out; the model never edits the schedule directly, it only sequences
calls. A call that breaks a rule comes back as an error string and dials
nobody, so the prompt below is guidance, not the enforcement.

The LLM provider is Groq through Strands' OpenAI-compatible model (set
GROQ_API_KEY in .env). engine.py remains the zero-dependency fallback brain.
"""

import json

from strands import Agent, tool
from strands.models.openai import OpenAIModel

from .caller import Caller
from .config import Config
from .models import ClinicState
from .tools import EveningTools, GuardError

SYSTEM_PROMPT = """\
You are the dispatcher for {persona}, the phone assistant of {clinic}.
Tonight you must work through tomorrow's appointment list:

1. Call get_worklist first to see the schedule and the waitlist.
2. For every appointment with status "scheduled", place a confirm_call.
   - If the result is no_answer, retry exactly once. If still no answer,
     move on (the tool flags it for the clinic).
   - If the result is wrong_person_or_unclear, do not retry and do not
     offer the slot. The tool flags it for a human; move on.
3. Whenever a patient cancels, the slot is free: offer it to the waitlist
   with offer_slot, strictly in waitlist order, until someone accepts or
   the waitlist is exhausted. Never skip ahead. A reschedule keeps the
   patient's booking, so its slot is NOT offered to the waitlist.
   If an offer comes back wrong_person_or_unclear, stop offering that slot.
4. Never call the same person more than twice tonight.
5. When every appointment is handled, call get_morning_report and present it
   to the clinic owner in 2-3 friendly sentences, including rupees recovered.

Work step by step and place one call at a time. If a tool returns an error,
read it: it tells you what the rules allow next."""


def build_agent(state: ClinicState, caller: Caller, config: Config) -> Agent:
    """Wires the shared clinic state and a Caller into a Strands agent."""

    tools = EveningTools(state, caller)

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
        """Place a confirmation call for one appointment (or its one retry
        after a no_answer). Returns the outcome: confirmed, cancelled,
        reschedule, no_answer, or wrong_person_or_unclear."""
        try:
            outcome = tools.confirm(appointment_id)
        except GuardError as err:
            return f"error: {err}"
        return json.dumps({"result": outcome.result.value, "summary": outcome.summary})

    @tool
    def offer_slot(waitlist_id: str, appointment_id: str) -> str:
        """Offer a cancelled slot to the next waitlist entry in order. On
        accept, the slot is rebooked to them automatically. Returns accepted,
        declined, no_answer, or wrong_person_or_unclear."""
        try:
            outcome = tools.offer(waitlist_id, appointment_id)
        except GuardError as err:
            return f"error: {err}"
        return json.dumps({"result": outcome.result.value, "summary": outcome.summary})

    @tool
    def get_morning_report() -> str:
        """The morning report: counts per status and rupees recovered."""
        return json.dumps(tools.morning_report())

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
