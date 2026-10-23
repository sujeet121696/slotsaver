"""Asha's call wording and ops-log summaries, shared by every real provider.

docs/TELEPHONY.md §4.7: the wording must be the same on every provider, so
CALL-E and Vobiz (via Bolna) both build their task text here instead of each
keeping a copy that could drift.
"""

from ..caller import ConfirmResult, OfferResult
from ..models import Appointment, WaitlistEntry

# Outcomes whose provider notes are worth showing in the ops log.
NOTED_CONFIRM = {ConfirmResult.NO_ANSWER, ConfirmResult.WRONG_PERSON_OR_UNCLEAR}
NOTED_OFFER = {OfferResult.NO_ANSWER, OfferResult.WRONG_PERSON_OR_UNCLEAR}


def confirm_task(persona: str, clinic_name: str, appt: Appointment) -> str:
    return (
        f"You are {persona}, the friendly phone assistant of {clinic_name}. "
        f"Call {appt.patient} and first confirm you are speaking with "
        f"{appt.patient} — if the person on the line says they are someone "
        f"else, or you cannot tell who you are speaking with, do not discuss "
        f"the appointment; end the call politely (wrong_person_or_unclear). "
        f"Otherwise, confirm their appointment TOMORROW at "
        f"{appt.time} with {appt.doctor}. Introduce yourself as the clinic's "
        f"assistant up front. You are talking to a person — never press any "
        f"phone keys and never wait on hold. The moment you have the answer, "
        f"thank them, say goodbye, and end the call; keep it under 45 seconds. "
        f"Outcomes: they will come (confirmed); they cancel "
        f"(cancelled) — thank them and say the slot will be freed; they want a "
        f"different time (reschedule); nobody answered (no_answer); you "
        f"reached someone but could not confirm it was {appt.patient}, or the "
        f"call was too ambiguous to classify (wrong_person_or_unclear)."
    )


def offer_task(
    persona: str, clinic_name: str, entry: WaitlistEntry, appt: Appointment
) -> str:
    return (
        f"You are {persona}, the friendly phone assistant of {clinic_name}. "
        f"Call {entry.name}, who asked us for an earlier appointment, and "
        f"first confirm you are speaking with {entry.name} — if the person on "
        f"the line says they are someone else, or you cannot tell who you are "
        f"speaking with, do not offer the slot; end the call politely "
        f"(wrong_person_or_unclear). Otherwise, tell them a slot just opened "
        f"TOMORROW at {appt.time} with {appt.doctor} and offer it to "
        f"them. Introduce yourself as the clinic's assistant up front. You are "
        f"talking to a person — never press any phone keys and never wait on "
        f"hold. The moment you have the answer, thank them, say goodbye, and "
        f"end the call; keep it under 45 seconds. "
        f"Outcomes: they take the slot (accepted) — "
        f"tell them they are booked and the clinic will see them tomorrow; they "
        f"don't want it (declined) — they stay on the list; nobody answered "
        f"(no_answer); you reached someone but could not confirm it was "
        f"{entry.name}, or the call was too ambiguous to classify "
        f"(wrong_person_or_unclear)."
    )


def confirm_summary(appt: Appointment, outcome: ConfirmResult, notes: str = "") -> str:
    summary = {
        ConfirmResult.CONFIRMED: f"{appt.patient} confirmed {appt.time}",
        ConfirmResult.CANCELLED: f"{appt.patient} cancelled {appt.time}",
        ConfirmResult.RESCHEDULE: f"{appt.patient} asked to reschedule {appt.time}",
        ConfirmResult.NO_ANSWER: f"{appt.patient} did not answer",
        ConfirmResult.WRONG_PERSON_OR_UNCLEAR: f"unclear who answered for {appt.patient}",
    }[outcome]
    if outcome in NOTED_CONFIRM and notes:
        summary += f" ({notes})"
    return summary


def offer_summary(
    entry: WaitlistEntry, appt: Appointment, outcome: OfferResult, notes: str = ""
) -> str:
    summary = {
        OfferResult.ACCEPTED: f"{entry.name} accepted the {appt.time} slot",
        OfferResult.DECLINED: f"{entry.name} declined the {appt.time} slot",
        OfferResult.NO_ANSWER: f"{entry.name} did not answer",
        OfferResult.WRONG_PERSON_OR_UNCLEAR: f"unclear who answered for {entry.name}",
    }[outcome]
    if outcome in NOTED_OFFER and notes:
        summary += f" ({notes})"
    return summary
