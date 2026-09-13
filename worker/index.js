/**
 * SlotSaver hosted-demo Worker: serves the static board (via `ASSETS`) plus
 * private, token-gated routes for placing real CALL-E calls from the page.
 *
 *   POST /api/call          { id, token }        -> { call_id }
 *   GET  /api/call?id&token                      -> { status, outcome }
 *   GET  /api/numbers?token                       -> [{ id, label }]
 *   POST /api/board         { token, snapshot }   -> { ok }   (see run_demo --push)
 *   GET  /api/board?token                          -> board.json-shaped snapshot
 *   GET  /api/evening/preview?token                -> board snapshot (cast, nothing called yet)
 *   GET  /api/evening/autorun?token                 -> { enabled }   (is the Cron Trigger armed?)
 *   POST /api/evening/autorun { token, enabled }    -> { enabled }   (arm/disarm it)
 *   POST /api/evening/start { token, dryRun? }    -> board snapshot  (manual trigger)
 *   POST /api/evening/step  { token, dryRun? }    -> board snapshot + done
 *
 * The evening orchestrator (confirm -> cancel -> backfill, ported from
 * engine.py) is a KV state machine advanced one small step per call — never
 * one long request — so it's safe under Workers' execution limits. Both the
 * page's button and the Cron Trigger (wrangler.jsonc) call the same step.
 *
 * Security: real phone numbers never reach the browser (masked label + id
 * only, mapped back server-side). Every route requires `token` to match the
 * CALL_TRIGGER_TOKEN secret (constant-time compare) or it 403s. CALLE_API_KEY
 * stays server-side. A KV cooldown + daily cap bounds credit spend.
 */

const CALLE_BASE = "https://api.heycall-e.com";
const CLINIC_NAME = "Dr. Meera's Dental Clinic";
const AGENT_PERSONA = "Asha";
const DOCTOR_NAME = "Dr. Meera";
const TEST_PATIENT_NAME = "Rohit Sharma"; // generic demo name, not a real patient
const CALLE_REGION = "IN";
const CALLE_LOCALE = "en-US";

const COOLDOWN_SECONDS = 60; // Cloudflare KV's minimum TTL is 60s — can't go lower
const DAILY_CAP = 5;

const CONFIRM_SCHEMA = {
  type: "object",
  required: ["outcome"],
  properties: {
    outcome: { type: "string", enum: ["confirmed", "cancelled", "reschedule", "no_answer"] },
    notes: { type: "string" },
  },
  additionalProperties: false,
};

const OFFER_SCHEMA = {
  type: "object",
  required: ["outcome"],
  properties: {
    outcome: { type: "string", enum: ["accepted", "declined", "no_answer"] },
    notes: { type: "string" },
  },
  additionalProperties: false,
};

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

/** Constant-time string equality (avoids leaking match length via timing). */
async function safeEqual(a, b) {
  const enc = new TextEncoder();
  const [ha, hb] = await Promise.all([
    crypto.subtle.digest("SHA-256", enc.encode(String(a ?? ""))),
    crypto.subtle.digest("SHA-256", enc.encode(String(b ?? ""))),
  ]);
  const va = new Uint8Array(ha), vb = new Uint8Array(hb);
  let diff = 0;
  for (let i = 0; i < va.length; i++) diff |= va[i] ^ vb[i];
  return diff === 0 && a !== undefined && a !== "" && b !== undefined && b !== "";
}

async function requireToken(request, env, tokenFromQuery) {
  const token = tokenFromQuery ?? new URL(request.url).searchParams.get("token");
  return safeEqual(token, env.CALL_TRIGGER_TOKEN);
}

function allowedPhones(env) {
  return String(env.ALLOWED_DEMO_PHONES || "")
    .split(",")
    .map((p) => p.trim())
    .filter(Boolean);
}

function maskPhone(phone) {
  // +918248164404 -> +9182••••404
  if (phone.length < 8) return "•".repeat(phone.length);
  return phone.slice(0, 5) + "••••" + phone.slice(-3);
}

async function tokenKeyPrefix(token) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(token));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 16);
}

async function checkAndBumpRateLimit(env, token) {
  if (!env.RATE_LIMIT) return { ok: true }; // fail open only if KV not bound (local/dev)
  const prefix = await tokenKeyPrefix(token);
  const cooldownKey = `cooldown:${prefix}`;
  if (await env.RATE_LIMIT.get(cooldownKey)) {
    return { ok: false, error: `Please wait ${COOLDOWN_SECONDS}s between calls.` };
  }
  const day = new Date().toISOString().slice(0, 10);
  const dailyKey = `daily:${prefix}:${day}`;
  const count = Number((await env.RATE_LIMIT.get(dailyKey)) || "0");
  if (count >= DAILY_CAP) {
    return { ok: false, error: `Daily limit of ${DAILY_CAP} calls reached.` };
  }
  await env.RATE_LIMIT.put(cooldownKey, "1", { expirationTtl: COOLDOWN_SECONDS });
  await env.RATE_LIMIT.put(dailyKey, String(count + 1), { expirationTtl: 86400 });
  return { ok: true };
}

function buildConfirmTask(patient, time) {
  return (
    `You are ${AGENT_PERSONA}, the friendly phone assistant of ${CLINIC_NAME}. ` +
    `Call ${patient} and confirm their appointment TOMORROW at ${time} with ` +
    `${DOCTOR_NAME}. Introduce yourself as the clinic's assistant up front. ` +
    `You are talking to a person — never press any phone keys and never ` +
    `wait on hold. The moment you have the answer, thank them, say goodbye, ` +
    `and end the call; keep it under 45 seconds. Outcomes: they will come ` +
    `(confirmed); they cancel (cancelled) — thank them and say the slot ` +
    `will be freed; they want a different time (reschedule); nobody ` +
    `answered or it wasn't them (no_answer).`
  );
}

function buildOfferTask(name, time) {
  return (
    `You are ${AGENT_PERSONA}, the friendly phone assistant of ${CLINIC_NAME}. ` +
    `Call ${name}, who asked us for an earlier appointment. A slot just ` +
    `opened TOMORROW at ${time} with ${DOCTOR_NAME}. Offer it to them. ` +
    `Introduce yourself as the clinic's assistant up front. You are ` +
    `talking to a person — never press any phone keys and never wait on ` +
    `hold. The moment you have the answer, thank them, say goodbye, and ` +
    `end the call; keep it under 45 seconds. Outcomes: they take the slot ` +
    `(accepted) — tell them they are booked and the clinic will see them ` +
    `tomorrow; they don't want it (declined) — they stay on the list; ` +
    `nobody answered (no_answer).`
  );
}

async function handleNumbers(request, env) {
  if (!(await requireToken(request, env))) return json({ error: "unauthorized" }, 403);
  const numbers = allowedPhones(env).map((phone, id) => ({ id, label: maskPhone(phone) }));
  return json({ numbers });
}

async function handlePostCall(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid JSON body" }, 400);
  }
  const { id, token, dryRun } = body || {};
  if (!(await requireToken(request, env, token))) return json({ error: "unauthorized" }, 403);

  const phones = allowedPhones(env);
  const idx = Number(id);
  if (!Number.isInteger(idx) || idx < 0 || idx >= phones.length) {
    return json({ error: "unknown number id" }, 400);
  }

  // Dry run stops here — never calls CALL-E or the rate limiter. The fake
  // call_id encodes its own start time so polling can simulate progress
  // statelessly (Workers keep no memory between requests).
  if (dryRun) {
    return json({ call_id: `dryrun-${Date.now()}-${crypto.randomUUID().slice(0, 8)}` });
  }

  const phone = phones[idx];

  const rl = await checkAndBumpRateLimit(env, token);
  if (!rl.ok) return json({ error: rl.error }, 429);

  if (!env.CALLE_API_KEY) return json({ error: "server not configured for real calls" }, 503);

  const idempotencyKey = `slotsaver-web-${Date.now()}-${crypto.randomUUID().slice(0, 8)}`;
  const res = await fetch(`${CALLE_BASE}/v1/calls`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.CALLE_API_KEY}`,
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify({
      task: buildConfirmTask(TEST_PATIENT_NAME, "10:00 AM"),
      recipients: [{ phones: [phone], region: CALLE_REGION, locale: CALLE_LOCALE }],
      result_schema: CONFIRM_SCHEMA,
    }),
  });
  if (!res.ok) {
    return json({ error: "CALL-E rejected the call request", status: res.status }, 502);
  }
  const created = await res.json();
  return json({ call_id: created.id });
}

async function handleGetCall(request, env) {
  const url = new URL(request.url);
  if (!(await requireToken(request, env))) return json({ error: "unauthorized" }, 403);
  const callId = url.searchParams.get("id");
  if (!callId) return json({ error: "missing id" }, 400);

  if (callId.startsWith("dryrun-")) {
    const startMs = Number(callId.split("-")[1]) || 0;
    const elapsed = Date.now() - startMs;
    if (elapsed < 4000) return json({ status: "in_progress", terminal: false });
    return json({
      status: "completed",
      terminal: true,
      outcome: "confirmed",
      notes: "simulated — no real call was placed, no credit spent",
    });
  }

  const res = await fetch(`${CALLE_BASE}/v1/calls/${encodeURIComponent(callId)}`, {
    headers: { Authorization: `Bearer ${env.CALLE_API_KEY}` },
  });
  if (!res.ok) return json({ error: "CALL-E lookup failed", status: res.status }, 502);
  const call = await res.json();

  const terminal = ["completed", "failed", "canceled"].includes(call.status);
  const result = call.structured_result || {};
  return json({
    status: call.status,
    terminal,
    outcome: result.outcome || null,
    notes: result.notes || null,
  });
}

// ---- live board relay ---------------------------------------------------
// Lets a local `run_demo.py` push its board.json snapshot here so the
// hosted page can show a real run, not just the scripted ?demo=1 replay.
// Token-gated both ways — a snapshot could carry real patient names.
const BOARD_KV_KEY = "board:latest";
const BOARD_TTL_SECONDS = 6 * 3600; // stale runs disappear on their own
const BOARD_MAX_BYTES = 20_000; // generous for this snapshot shape; blocks abuse

async function handlePostBoard(request, env) {
  if (!env.RATE_LIMIT) return json({ error: "server not configured" }, 503);
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid JSON body" }, 400);
  }
  const { token, snapshot } = body || {};
  if (!(await requireToken(request, env, token))) return json({ error: "unauthorized" }, 403);
  if (!snapshot || typeof snapshot !== "object") return json({ error: "missing snapshot" }, 400);

  const serialized = JSON.stringify(snapshot);
  if (serialized.length > BOARD_MAX_BYTES) return json({ error: "snapshot too large" }, 413);

  await env.RATE_LIMIT.put(BOARD_KV_KEY, serialized, { expirationTtl: BOARD_TTL_SECONDS });
  return json({ ok: true });
}

async function handleGetBoard(request, env) {
  if (!(await requireToken(request, env))) return json({ error: "unauthorized" }, 403);
  if (!env.RATE_LIMIT) return json({ error: "server not configured" }, 503);
  const stored = await env.RATE_LIMIT.get(BOARD_KV_KEY);
  if (!stored) return json({ error: "no run pushed yet" }, 404);
  return new Response(stored, {
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

// ---- evening orchestrator -------------------------------------------------
// Ports engine.py's confirm -> retry -> backfill loop as a KV state machine,
// advanced one step per call (place the next call, or poll the pending one).
// Uses the same "your own phone(s) play every role" cast as the local --real
// flow, since only ALLOWED_DEMO_PHONES may ever ring.
const EVENING_KV_KEY = "evening:state";
const EVENING_LOCK_KEY = "evening:lock";

function buildCast(phones) {
  const a = [
    { id: "a1", patient: TEST_PATIENT_NAME, time: "10:00 AM", doctor: DOCTOR_NAME, fee: 800, status: "scheduled", phone: phones[0] },
    { id: "a2", patient: "Priya Nair", time: "11:00 AM", doctor: DOCTOR_NAME, fee: 800, status: "scheduled", phone: phones[0] },
  ];
  const w = [{ id: "w1", name: "Arjun Das", phone: phones.length > 1 ? phones[1] : phones[0] }];
  return { appointments: a, waitlist: w };
}

function initEveningState(phones, dryRun) {
  const cast = buildCast(phones);
  return {
    date: new Date().toISOString().slice(0, 10),
    status: "running",
    clinicName: CLINIC_NAME,
    recoveredInr: 0,
    log: ["Asha starts tonight's confirmation round…"],
    appointments: cast.appointments,
    waitlist: cast.waitlist,
    cursor: { apptIndex: 0, phase: "confirm", waitlistIndex: 0, pendingCallId: null, pendingKind: null },
    dryRun: !!dryRun,
    dryRunStep: 0,
  };
}

function projectBoard(state) {
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

async function loadEveningState(env) {
  const raw = await env.RATE_LIMIT.get(EVENING_KV_KEY);
  return raw ? JSON.parse(raw) : null;
}

async function saveEveningState(env, state) {
  const serialized = JSON.stringify(state);
  await env.RATE_LIMIT.put(EVENING_KV_KEY, serialized, { expirationTtl: BOARD_TTL_SECONDS });
  await env.RATE_LIMIT.put(BOARD_KV_KEY, JSON.stringify(projectBoard(state)), { expirationTtl: BOARD_TTL_SECONDS });
}

async function calleCreateCall(env, { task, phone, schema, idKey }) {
  const res = await fetch(`${CALLE_BASE}/v1/calls`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.CALLE_API_KEY}`,
      "Content-Type": "application/json",
      "Idempotency-Key": idKey,
    },
    body: JSON.stringify({
      task,
      recipients: [{ phones: [phone], region: CALLE_REGION, locale: CALLE_LOCALE }],
      result_schema: schema,
    }),
  });
  if (!res.ok) throw new Error(`CALL-E create failed: ${res.status}`);
  const data = await res.json();
  return data.id;
}

async function calleGetCall(env, callId) {
  const res = await fetch(`${CALLE_BASE}/v1/calls/${encodeURIComponent(callId)}`, {
    headers: { Authorization: `Bearer ${env.CALLE_API_KEY}` },
  });
  if (!res.ok) throw new Error(`CALL-E lookup failed: ${res.status}`);
  return res.json();
}

function advanceToNextAppointment(state) {
  state.cursor.apptIndex += 1;
  state.cursor.phase = "confirm";
  state.cursor.waitlistIndex = 0;
}

function finishEvening(state) {
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

function applyOutcome(state, outcome) {
  const cursor = state.cursor;
  const appt = state.appointments[cursor.apptIndex];

  if (cursor.pendingKind === "confirm" || cursor.pendingKind === "retry") {
    if (outcome === "no_answer" && cursor.pendingKind === "confirm") {
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
    } else {
      appt.status = "needs_attention";
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

const DRY_RUN_OUTCOMES = ["confirmed", "cancelled", "accepted"];

async function stepEvening(env, state) {
  if (state.status !== "running") return state;
  const cursor = state.cursor;

  if (cursor.pendingCallId) {
    let outcome;
    if (state.dryRun) {
      outcome = DRY_RUN_OUTCOMES[state.dryRunStep] ?? "no_answer";
      state.dryRunStep += 1;
    } else {
      const call = await calleGetCall(env, cursor.pendingCallId);
      if (!["completed", "failed", "canceled"].includes(call.status)) {
        return state; // still ringing — nothing to do this tick
      }
      outcome = (call.structured_result || {}).outcome || "no_answer";
    }
    applyOutcome(state, outcome);
    cursor.pendingCallId = null;
    cursor.pendingKind = null;
    return state;
  }

  if (cursor.apptIndex >= state.appointments.length) {
    if (state.status === "running") finishEvening(state);
    return state;
  }

  const appt = state.appointments[cursor.apptIndex];
  if (cursor.phase === "confirm") {
    const kind = appt._retried ? "retry" : "confirm";
    const idKey = `slotsaver-evening-${state.date}-${appt.id}-${kind}`;
    const task = buildConfirmTask(appt.patient, appt.time);
    cursor.pendingCallId = state.dryRun
      ? `dryrun-${Date.now()}`
      : await calleCreateCall(env, { task, phone: appt.phone, schema: CONFIRM_SCHEMA, idKey });
    cursor.pendingKind = kind;
    state.log.push(`${kind === "retry" ? "Retry call" : "Confirm call"} → calling ${appt.patient}…`);
  } else if (cursor.phase === "backfill") {
    if (cursor.waitlistIndex >= state.waitlist.length) {
      state.log.push(`Slot ${appt.time} could not be refilled tonight`);
      advanceToNextAppointment(state);
    } else {
      const entry = state.waitlist[cursor.waitlistIndex];
      const idKey = `slotsaver-evening-${state.date}-${appt.id}-offer-${entry.id}`;
      const task = buildOfferTask(entry.name, appt.time);
      cursor.pendingCallId = state.dryRun
        ? `dryrun-${Date.now()}`
        : await calleCreateCall(env, { task, phone: entry.phone, schema: OFFER_SCHEMA, idKey });
      cursor.pendingKind = "offer";
      state.log.push(`Backfill call → calling ${entry.name}…`);
    }
  }
  return state;
}

/** Narrow race-condition guard: the manual button and the Cron Trigger
 * could otherwise both advance the same pending step at once. Not true
 * atomicity (KV has none), just enough to make that collision very
 * unlikely for a single-operator demo. */
async function withEveningLock(env, fn) {
  if (await env.RATE_LIMIT.get(EVENING_LOCK_KEY)) return { busy: true };
  await env.RATE_LIMIT.put(EVENING_LOCK_KEY, "1", { expirationTtl: 60 }); // KV's TTL floor
  try {
    return { busy: false, result: await fn() };
  } finally {
    await env.RATE_LIMIT.delete(EVENING_LOCK_KEY);
  }
}

/** Read-only: what tonight's cast WOULD be if a run started right now —
 * real names, "scheduled" status, nothing called yet. Purely computed, no
 * KV read/write, so it can never collide with or leak an actual run's
 * state; just lets the page show something other than a blank board while
 * waiting for a real run to begin. */
async function handleEveningPreview(request, env) {
  if (!(await requireToken(request, env))) return json({ error: "unauthorized" }, 403);
  const phones = allowedPhones(env);
  if (!phones.length) return json({ error: "no ALLOWED_DEMO_PHONES configured" }, 503);
  const state = initEveningState(phones, true);
  state.log = []; // nothing has actually started — the real log would be misleading here
  return json({ board: projectBoard(state) });
}

async function handleAutorunGet(request, env) {
  if (!(await requireToken(request, env))) return json({ error: "unauthorized" }, 403);
  if (!env.RATE_LIMIT) return json({ error: "server not configured" }, 503);
  const enabled = (await env.RATE_LIMIT.get(AUTORUN_KV_KEY)) === "1";
  return json({ enabled });
}

async function handleAutorunSet(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid JSON body" }, 400);
  }
  const { token, enabled } = body || {};
  if (!(await requireToken(request, env, token))) return json({ error: "unauthorized" }, 403);
  if (!env.RATE_LIMIT) return json({ error: "server not configured" }, 503);
  if (enabled) await env.RATE_LIMIT.put(AUTORUN_KV_KEY, "1");
  else await env.RATE_LIMIT.delete(AUTORUN_KV_KEY);
  return json({ enabled: !!enabled });
}

async function handleEveningStart(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid JSON body" }, 400);
  }
  const { token, dryRun } = body || {};
  if (!(await requireToken(request, env, token))) return json({ error: "unauthorized" }, 403);
  if (!env.RATE_LIMIT) return json({ error: "server not configured" }, 503);

  const phones = allowedPhones(env);
  if (!phones.length) return json({ error: "no ALLOWED_DEMO_PHONES configured" }, 503);
  if (!dryRun && !env.CALLE_API_KEY) return json({ error: "server not configured for real calls" }, 503);

  // A dry run must never clobber a real run that's actively in progress
  // (e.g. testing the UI while the automatic evening cron is mid-flight).
  const existing = await loadEveningState(env);
  if (dryRun && existing && existing.status === "running" && !existing.dryRun) {
    return json({ error: "a real evening run is in progress — wait for it to finish before testing" }, 409);
  }

  const state = initEveningState(phones, !!dryRun);
  await saveEveningState(env, state);
  return json({ board: projectBoard(state), done: false });
}

async function handleEveningStep(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid JSON body" }, 400);
  }
  const { token } = body || {};
  if (!(await requireToken(request, env, token))) return json({ error: "unauthorized" }, 403);
  if (!env.RATE_LIMIT) return json({ error: "server not configured" }, 503);

  const state = await loadEveningState(env);
  if (!state) return json({ error: "no evening run in progress — call /api/evening/start first" }, 404);
  if (state.status !== "running") return json({ board: projectBoard(state), done: true });

  const { busy, result } = await withEveningLock(env, async () => {
    try {
      await stepEvening(env, state);
    } catch (err) {
      state.log.push(`Error: ${err.message || "step failed"}`);
    }
    await saveEveningState(env, state);
    return state;
  });
  if (busy) return json({ board: projectBoard(state), done: false, busy: true });
  return json({ board: projectBoard(result), done: result.status !== "running" });
}

const AUTORUN_KV_KEY = "evening:autorun";

async function runEveningScheduledTick(env) {
  if (!env.RATE_LIMIT || !env.CALLE_API_KEY) return;
  if ((await env.RATE_LIMIT.get(AUTORUN_KV_KEY)) !== "1") return; // off by default
  const now = new Date();
  const afterStartTime = now.getUTCHours() > 13 || (now.getUTCHours() === 13 && now.getUTCMinutes() >= 30);
  const today = now.toISOString().slice(0, 10);

  let state = await loadEveningState(env);
  // A leftover dry-run test (any date) never counts as "today's real run
  // already happened" — only a real (non-dryRun) run for today does.
  const alreadyRanForReal = state && state.date === today && !state.dryRun;
  if (!alreadyRanForReal && afterStartTime) {
    const phones = allowedPhones(env);
    if (!phones.length) return;
    state = initEveningState(phones, false);
    await saveEveningState(env, state);
  }
  if (state && state.date === today && !state.dryRun && state.status === "running") {
    await withEveningLock(env, async () => {
      try {
        await stepEvening(env, state);
      } catch (err) {
        state.log.push(`Error: ${err.message || "step failed"}`);
      }
      await saveEveningState(env, state);
    });
  }
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (url.pathname === "/api/numbers" && request.method === "GET") {
      return handleNumbers(request, env);
    }
    if (url.pathname === "/api/call" && request.method === "POST") {
      return handlePostCall(request, env);
    }
    if (url.pathname === "/api/call" && request.method === "GET") {
      return handleGetCall(request, env);
    }
    if (url.pathname === "/api/board" && request.method === "POST") {
      return handlePostBoard(request, env);
    }
    if (url.pathname === "/api/board" && request.method === "GET") {
      return handleGetBoard(request, env);
    }
    if (url.pathname === "/api/evening/preview" && request.method === "GET") {
      return handleEveningPreview(request, env);
    }
    if (url.pathname === "/api/evening/autorun" && request.method === "GET") {
      return handleAutorunGet(request, env);
    }
    if (url.pathname === "/api/evening/autorun" && request.method === "POST") {
      return handleAutorunSet(request, env);
    }
    if (url.pathname === "/api/evening/start" && request.method === "POST") {
      return handleEveningStart(request, env);
    }
    if (url.pathname === "/api/evening/step" && request.method === "POST") {
      return handleEveningStep(request, env);
    }

    return env.ASSETS.fetch(request);
  },

  async scheduled(event, env, ctx) {
    ctx.waitUntil(runEveningScheduledTick(env));
  },
};
