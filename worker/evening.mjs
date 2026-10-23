/**
 * The hosted evening loop: engine.py's confirm -> retry -> backfill rules as
 * a pure state machine, advanced one small step per request or Cron tick.
 *
 * No KV, no fetch, no env: index.js passes in the CALL-E calls and the
 * extra-call charge, so tests/worker_evening.test.mjs can drive every
 * branch with fakes. Keep the rules in step with slotsaver/tools.py.
 */

export const CLINIC_NAME = "Dr. Meera's Dental Clinic";
export const AGENT_PERSONA = "Asha";
export const DOCTOR_NAME = "Dr. Meera";
export const TEST_PATIENT_NAME = "Rohit Sharma"; // generic demo name, not a real patient

export const MAX_CALLS_PER_PERSON = 2; // same as tools.py
export const MAX_STEP_ERRORS = 5; // consecutive failed steps before the run stops

export const CONFIRM_SCHEMA = {
  type: "object",
  required: ["outcome"],
  properties: {
    outcome: {
      type: "string",
      enum: ["confirmed", "cancelled", "reschedule", "no_answer", "wrong_person_or_unclear"],
    },
    notes: { type: "string" },
  },
  additionalProperties: false,
};

export const OFFER_SCHEMA = {
  type: "object",
  required: ["outcome"],
  properties: {
    outcome: { type: "string", enum: ["accepted", "declined", "no_answer", "wrong_person_or_unclear"] },
    notes: { type: "string" },
  },
  additionalProperties: false,
};

const TERMINAL_STATUSES = ["completed", "failed", "canceled"];

export function buildConfirmTask(patient, time) {
  return (
    `You are ${AGENT_PERSONA}, the friendly phone assistant of ${CLINIC_NAME}. ` +
    `Call ${patient} and first confirm you are speaking with ${patient} — if ` +
    `the person on the line says they are someone else, or you cannot tell ` +
    `who you are speaking with, do not discuss the appointment; end the call ` +
    `politely (wrong_person_or_unclear). Otherwise, confirm their ` +
    `appointment TOMORROW at ${time} with ${DOCTOR_NAME}. Introduce yourself as the clinic's assistant up front. ` +
    `You are talking to a person — never press any phone keys and never ` +
    `wait on hold. The moment you have the answer, thank them, say goodbye, ` +
    `and end the call; keep it under 45 seconds. Outcomes: they will come ` +
    `(confirmed); they cancel (cancelled) — thank them and say the slot ` +
    `will be freed; they want a different time (reschedule); nobody ` +
    `answered (no_answer); you reached someone but could not confirm it was ` +
    `${patient}, or the call was too ambiguous to classify ` +
    `(wrong_person_or_unclear).`
  );
}

export function buildOfferTask(name, time) {
  return (
    `You are ${AGENT_PERSONA}, the friendly phone assistant of ${CLINIC_NAME}. ` +
    `Call ${name}, who asked us for an earlier appointment, and first ` +
    `confirm you are speaking with ${name} — if the person on the line says ` +
    `they are someone else, or you cannot tell who you are speaking with, do ` +
    `not offer the slot; end the call politely (wrong_person_or_unclear). ` +
    `Otherwise, tell them a slot just opened TOMORROW at ${time} with ` +
    `${DOCTOR_NAME} and offer it to them. ` +
    `Introduce yourself as the clinic's assistant up front. You are ` +
    `talking to a person — never press any phone keys and never wait on ` +
    `hold. The moment you have the answer, thank them, say goodbye, and ` +
    `end the call; keep it under 45 seconds. Outcomes: they take the slot ` +
    `(accepted) — tell them they are booked and the clinic will see them ` +
    `tomorrow; they don't want it (declined) — they stay on the list; ` +
    `nobody answered (no_answer); you reached someone but could not confirm ` +
    `it was ${name}, or the call was too ambiguous to classify ` +
    `(wrong_person_or_unclear).`
  );
}

// Uses the same "your own phone(s) play every role" cast as the local --real
// flow, since only ALLOWED_DEMO_PHONES may ever ring.
export function buildCast(phones) {
  const a = [
    { id: "a1", patient: TEST_PATIENT_NAME, time: "10:00 AM", doctor: DOCTOR_NAME, fee: 800, status: "scheduled", phone: phones[0] },
    { id: "a2", patient: "Priya Nair", time: "11:00 AM", doctor: DOCTOR_NAME, fee: 800, status: "scheduled", phone: phones[0] },
  ];
  const w = [{ id: "w1", name: "Arjun Das", phone: phones.length > 1 ? phones[1] : phones[0] }];
  return { appointments: a, waitlist: w };
}

/** Calls the run is charged for up front: one per appointment and waitlist
 * entry. Retries and repeat offers beyond that are charged one by one. */
export function expectedCalls(state) {
  return state.appointments.length + state.waitlist.length;
}

export function initEveningState(phones, dryRun) {
  const cast = buildCast(phones);
  const state = {
    date: new Date().toISOString().slice(0, 10),
    // CALL-E dedupes idempotency keys forever, so keys carry a per-run id:
    // a second run on the same day must dial, not get the first run's
    // cached calls back.
    runId: crypto.randomUUID().slice(0, 8),
    status: "running",
    clinicName: CLINIC_NAME,
    recoveredInr: 0,
    log: ["Asha starts tonight's confirmation round…"],
    appointments: cast.appointments,
    waitlist: cast.waitlist,
    cursor: { apptIndex: 0, phase: "confirm", waitlistIndex: 0, pendingCallId: null, pendingKind: null },
    dryRun: !!dryRun,
    dryRunStep: 0,
    calls: {}, // appointment/waitlist id -> calls placed tonight
    callsPlaced: 0,
    stepErrors: 0,
  };
  state.callBudget = expectedCalls(state);
  return state;
}

export function projectBoard(state) {
  return {
    clinic: state.clinicName,
    recovered_inr: state.recoveredInr,
    appointments: state.appointments.map((a) => ({
      time: a.time, patient: a.patient, doctor: a.doctor, fee_inr: a.fee, status: a.status,
    })),
    waitlist: state.waitlist.map((w) => w.name),
    log: state.log.slice(-12),
  };
}

/** Fills fields a state saved by an older build lacks. */
function upgrade(state) {
  if (!state.runId) state.runId = crypto.randomUUID().slice(0, 8);
  if (!state.calls) state.calls = {};
  if (typeof state.callsPlaced !== "number") state.callsPlaced = 0;
  if (typeof state.callBudget !== "number") state.callBudget = expectedCalls(state);
  if (typeof state.stepErrors !== "number") state.stepErrors = 0;
}

/** A result we can act on, or "wrong_person_or_unclear" when the call never
 * completed or its outcome is missing/outside the schema. Not "no_answer":
 * we don't know what happened, so it's never retried or read as a cancel. */
export function readOutcome(call, schema) {
  const outcome = (call.structured_result || {}).outcome;
  if (call.status !== "completed" || !schema.properties.outcome.enum.includes(outcome)) {
    return "wrong_person_or_unclear";
  }
  return outcome;
}

function advanceToNextAppointment(state) {
  state.cursor.apptIndex += 1;
  state.cursor.phase = "confirm";
  state.cursor.waitlistIndex = 0;
}

export function finishEvening(state) {
  state.status = "done";
  const counts = { confirmed: 0, cancelled: 0, backfilled: 0, rescheduled: 0, needs_attention: 0 };
  for (const a of state.appointments) {
    if (a.status === "backfilled") { counts.backfilled++; counts.cancelled++; }
    else if (a.status in counts) counts[a.status]++;
  }
  state.log.push(
    `Morning report: ${counts.confirmed} confirmed · ${counts.cancelled} cancelled · ` +
    `${counts.backfilled} backfilled · ₹${state.recoveredInr} recovered`
  );
}

export function applyOutcome(state, outcome) {
  const cursor = state.cursor;
  const appt = state.appointments[cursor.apptIndex];

  if (cursor.pendingKind === "confirm" || cursor.pendingKind === "retry") {
    if (outcome === "no_answer" && cursor.pendingKind === "confirm") {
      // Same as tools.py: flagged until the one retry answers.
      appt.status = "needs_attention";
      state.log.push(`Confirm call → ${appt.patient} did not answer`);
      appt._retried = true; // next placement for this appt becomes the one retry
      return;
    }
    const label = cursor.pendingKind === "retry" ? "Retry call" : "Confirm call";
    if (outcome === "confirmed") {
      appt.status = "confirmed";
      state.log.push(`${label} → ${appt.patient} confirmed ${appt.time}`);
      advanceToNextAppointment(state);
    } else if (outcome === "cancelled") {
      appt.status = "cancelled";
      state.log.push(`${label} → ${appt.patient} cancelled ${appt.time}`);
      cursor.phase = "backfill";
      cursor.waitlistIndex = 0;
    } else if (outcome === "reschedule") {
      appt.status = "rescheduled";
      state.log.push(`${label} → ${appt.patient} asked to reschedule ${appt.time}`);
      state.log.push(`${appt.patient} moved to a future slot; ${appt.time} freed`);
      advanceToNextAppointment(state);
    } else if (outcome === "wrong_person_or_unclear") {
      appt.status = "needs_attention";
      state.log.push(`${label} → unclear who answered for ${appt.patient}`);
      state.log.push(`${appt.patient}: couldn't confirm who answered — flagged for a human to check`);
      advanceToNextAppointment(state);
    } else {
      appt.status = "needs_attention";
      state.log.push(`${label} → ${appt.patient} did not answer`);
      state.log.push(`${appt.patient} unreachable — flagged for the clinic`);
      advanceToNextAppointment(state);
    }
  } else if (cursor.pendingKind === "offer") {
    const entry = state.waitlist[cursor.waitlistIndex];
    if (outcome === "accepted") {
      appt.status = "backfilled";
      appt.patient = entry.name;
      appt.phone = entry.phone;
      state.recoveredInr += appt.fee;
      state.waitlist.splice(cursor.waitlistIndex, 1);
      state.log.push(`Backfill call → ${entry.name} accepted the ${appt.time} slot`);
      state.log.push(`Slot ${appt.time} refilled — ₹${appt.fee} recovered`);
      advanceToNextAppointment(state);
    } else if (outcome === "wrong_person_or_unclear") {
      state.log.push(`Backfill call → unclear who answered for ${entry.name}`);
      state.log.push(`Slot ${appt.time}: couldn't confirm who answered — backfill paused for a human to check`);
      advanceToNextAppointment(state);
    } else {
      state.log.push(
        `Backfill call → ${entry.name} ${outcome === "declined" ? "declined" : "did not answer"} the ${appt.time} slot`
      );
      cursor.waitlistIndex += 1;
    }
  }

  if (cursor.apptIndex >= state.appointments.length && state.status === "running") {
    finishEvening(state);
  }
}

/** Index of the next waitlist entry this slot may be offered to, skipping
 * anyone already called MAX_CALLS_PER_PERSON times (tools.next_candidate). */
function nextCandidateIndex(state) {
  let i = state.cursor.waitlistIndex;
  while (i < state.waitlist.length && (state.calls[state.waitlist[i].id] || 0) >= MAX_CALLS_PER_PERSON) {
    i += 1;
  }
  return i;
}

/** Real runs only: a call beyond what the run was charged for must be
 * charged first. Returns false when the daily cap says no. */
async function reserveCall(state, deps) {
  if (state.dryRun || state.callsPlaced < state.callBudget) return true;
  if (!(await deps.chargeExtraCall())) return false;
  state.callBudget += 1; // charged now, so a failed create retried next step isn't charged twice
  return true;
}

async function placeCall(state, deps, { personId, task, phone, schema, idKey }) {
  const callId = state.dryRun
    ? `dryrun-${Date.now()}`
    : await deps.createCall({ task, phone, schema, idKey });
  state.calls[personId] = (state.calls[personId] || 0) + 1;
  state.callsPlaced += 1;
  return callId;
}

const DRY_RUN_OUTCOMES = ["confirmed", "cancelled", "accepted"];

/**
 * Advances the run by one step: polls the pending call, or places the next
 * one. `deps` = { createCall({task, phone, schema, idKey}) -> id,
 * getCall(id) -> call, chargeExtraCall() -> bool }. Throws on CALL-E errors;
 * runStep() is the wrapper that counts them.
 */
export async function stepEvening(state, deps) {
  if (state.status !== "running") return state;
  upgrade(state);
  const cursor = state.cursor;

  if (cursor.pendingCallId) {
    let outcome;
    if (state.dryRun) {
      outcome = DRY_RUN_OUTCOMES[state.dryRunStep] ?? "no_answer";
      state.dryRunStep += 1;
    } else {
      const call = await deps.getCall(cursor.pendingCallId);
      if (!TERMINAL_STATUSES.includes(call.status)) {
        return state; // still ringing — nothing to do this tick
      }
      outcome = readOutcome(call, cursor.pendingKind === "offer" ? OFFER_SCHEMA : CONFIRM_SCHEMA);
    }
    applyOutcome(state, outcome);
    cursor.pendingCallId = null;
    cursor.pendingKind = null;
    return state;
  }

  if (cursor.apptIndex >= state.appointments.length) {
    finishEvening(state);
    return state;
  }

  const appt = state.appointments[cursor.apptIndex];
  if (cursor.phase === "confirm") {
    if (!(await reserveCall(state, deps))) {
      appt.status = "needs_attention";
      state.log.push(`${appt.patient}: daily call limit reached — flagged for the clinic`);
      advanceToNextAppointment(state);
      return state;
    }
    const kind = appt._retried ? "retry" : "confirm";
    cursor.pendingCallId = await placeCall(state, deps, {
      personId: appt.id,
      task: buildConfirmTask(appt.patient, appt.time),
      phone: appt.phone,
      schema: CONFIRM_SCHEMA,
      idKey: `slotsaver-evening-${state.date}-${state.runId}-${appt.id}-${kind}`,
    });
    cursor.pendingKind = kind;
    state.log.push(`${kind === "retry" ? "Retry call" : "Confirm call"} → calling ${appt.patient}…`);
  } else if (cursor.phase === "backfill") {
    cursor.waitlistIndex = nextCandidateIndex(state);
    if (cursor.waitlistIndex >= state.waitlist.length) {
      state.log.push(`Slot ${appt.time} could not be refilled tonight`);
      advanceToNextAppointment(state);
      return state;
    }
    if (!(await reserveCall(state, deps))) {
      state.log.push(`Slot ${appt.time}: daily call limit reached — left for the clinic to refill`);
      advanceToNextAppointment(state);
      return state;
    }
    const entry = state.waitlist[cursor.waitlistIndex];
    cursor.pendingCallId = await placeCall(state, deps, {
      personId: entry.id,
      task: buildOfferTask(entry.name, appt.time),
      phone: entry.phone,
      schema: OFFER_SCHEMA,
      idKey: `slotsaver-evening-${state.date}-${state.runId}-${appt.id}-offer-${entry.id}`,
    });
    cursor.pendingKind = "offer";
    state.log.push(`Backfill call → calling ${entry.name}…`);
  }
  return state;
}

/**
 * stepEvening() plus error accounting: a failed step is logged and retried
 * on the next tick (idempotency keys make a retried create safe), but after
 * MAX_STEP_ERRORS failures in a row the run stops instead of staying
 * "running" until its KV entry expires.
 */
export async function runStep(state, deps) {
  try {
    await stepEvening(state, deps);
    state.stepErrors = 0;
  } catch (err) {
    state.stepErrors = (state.stepErrors || 0) + 1;
    state.log.push(`Error: ${err.message || "step failed"}`);
    if (state.stepErrors >= MAX_STEP_ERRORS) stopAfterErrors(state);
  }
  return state;
}

function stopAfterErrors(state) {
  state.log.push(`Run stopped after ${MAX_STEP_ERRORS} failed steps in a row — check CALL-E and the Worker logs`);
  for (const a of state.appointments) {
    if (a.status === "scheduled") a.status = "needs_attention";
  }
  state.cursor.pendingCallId = null;
  state.cursor.pendingKind = null;
  finishEvening(state);
}
