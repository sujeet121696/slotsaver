/**
 * SlotSaver hosted-demo Worker: serves the static board (via `ASSETS`) plus
 * private, token-gated routes for placing real CALL-E calls from the page.
 *
 *   POST /api/call          { id, token }        -> { call_id }
 *   GET  /api/call?id                             -> { status, outcome }
 *   GET  /api/numbers                             -> [{ id, label }]
 *   POST /api/board         { token, snapshot }   -> { ok }   (board.py push, BOARD_PUSH_URL)
 *   GET  /api/board                               -> board.json-shaped snapshot
 *   GET  /api/evening/preview                     -> board snapshot (cast, nothing called yet)
 *   GET  /api/evening/autorun                     -> { enabled }   (is the Cron Trigger armed?)
 *   POST /api/evening/autorun { token, enabled }  -> { enabled }   (arm/disarm it)
 *   POST /api/evening/start { token, dryRun? }    -> board snapshot  (manual trigger)
 *   POST /api/evening/step  { token }             -> board snapshot + done
 *
 * POST routes take the token in the JSON body; GET routes take it in the
 * X-SlotSaver-Token header, never the query string, so it stays out of
 * URLs and request logs.
 *
 * The evening orchestrator (confirm -> cancel -> backfill, ported from
 * engine.py) lives in evening.mjs: a KV state machine advanced one small
 * step per call — never one long request — so it's safe under Workers'
 * execution limits. Both the page's button and the Cron Trigger
 * (wrangler.jsonc) call the same step.
 *
 * Security: real phone numbers never reach the browser (masked label + id
 * only, mapped back server-side). Every route requires the token to match the
 * CALL_TRIGGER_TOKEN secret (constant-time compare) or it 403s. CALLE_API_KEY
 * stays server-side. A KV cooldown + daily cap bounds credit spend.
 */

import {
  CONFIRM_SCHEMA,
  TEST_PATIENT_NAME,
  buildConfirmTask,
  expectedCalls,
  initEveningState,
  projectBoard,
  runStep,
} from "./evening.mjs";

const CALLE_BASE = "https://api.heycall-e.com";
const CALLE_REGION = "IN";
const CALLE_LOCALE = "en-US";
const TOKEN_HEADER = "X-SlotSaver-Token";

const COOLDOWN_SECONDS = 60; // Cloudflare KV's minimum TTL is 60s — can't go lower
const DAILY_CAP = 5;

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

/** POST routes pass the token from their JSON body; GET routes read the header. */
async function requireToken(request, env, tokenFromBody) {
  const token = tokenFromBody ?? request.headers.get(TOKEN_HEADER);
  return safeEqual(token, env.CALL_TRIGGER_TOKEN);
}

function allowedPhones(env) {
  return String(env.ALLOWED_DEMO_PHONES || "")
    .split(",")
    .map((p) => p.trim())
    .filter(Boolean);
}

function maskPhone(phone) {
  // +15551234567 -> +1555••••567
  if (phone.length < 8) return "•".repeat(phone.length);
  return phone.slice(0, 5) + "••••" + phone.slice(-3);
}

async function tokenKeyPrefix(token) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(token));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 16);
}

/** `cost` = calls this request may place; an evening run books its whole cast up front. */
async function checkAndBumpRateLimit(env, token, cost = 1) {
  if (!env.RATE_LIMIT) return { ok: true }; // fail open only if KV not bound (local/dev)
  const prefix = await tokenKeyPrefix(token);
  const cooldownKey = `cooldown:${prefix}`;
  if (await env.RATE_LIMIT.get(cooldownKey)) {
    return { ok: false, error: `Please wait ${COOLDOWN_SECONDS}s between calls.` };
  }
  const day = new Date().toISOString().slice(0, 10);
  const dailyKey = `daily:${prefix}:${day}`;
  const count = Number((await env.RATE_LIMIT.get(dailyKey)) || "0");
  if (count + cost > DAILY_CAP) {
    return { ok: false, error: `Daily limit of ${DAILY_CAP} calls reached.` };
  }
  await env.RATE_LIMIT.put(cooldownKey, "1", { expirationTtl: COOLDOWN_SECONDS });
  await env.RATE_LIMIT.put(dailyKey, String(count + cost), { expirationTtl: 86400 });
  return { ok: true };
}

/** Mid-run charge for one call beyond what an evening run booked up front
 * (a retry or a repeat offer). Daily cap only: the cooldown is between
 * requests, not between the calls inside one run. */
async function chargeExtraCall(env, token) {
  if (!env.RATE_LIMIT) return true; // same fail-open as above (local/dev only)
  const prefix = await tokenKeyPrefix(token);
  const dailyKey = `daily:${prefix}:${new Date().toISOString().slice(0, 10)}`;
  const count = Number((await env.RATE_LIMIT.get(dailyKey)) || "0");
  if (count + 1 > DAILY_CAP) return false;
  await env.RATE_LIMIT.put(dailyKey, String(count + 1), { expirationTtl: 86400 });
  return true;
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
// The loop itself lives in evening.mjs; this part is the KV storage, the
// lock, and the CALL-E + rate-limit plumbing it's handed.
const EVENING_KV_KEY = "evening:state";
const EVENING_LOCK_KEY = "evening:lock";

/** What evening.mjs's runStep() needs from the outside world. Extra calls
 * are charged to CALL_TRIGGER_TOKEN's counter: the page's token must equal
 * it, so manual and Cron runs share one daily cap. */
function eveningDeps(env) {
  return {
    async createCall({ task, phone, schema, idKey }) {
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
    },
    async getCall(callId) {
      const res = await fetch(`${CALLE_BASE}/v1/calls/${encodeURIComponent(callId)}`, {
        headers: { Authorization: `Bearer ${env.CALLE_API_KEY}` },
      });
      if (!res.ok) throw new Error(`CALL-E lookup failed: ${res.status}`);
      return res.json();
    },
    chargeExtraCall: () => chargeExtraCall(env, env.CALL_TRIGGER_TOKEN),
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
  if (existing && existing.status === "running" && !existing.dryRun) {
    return json({ error: "a real evening run is in progress — wait for it to finish" }, 409);
  }

  const state = initEveningState(phones, !!dryRun);
  if (!dryRun) {
    // Same cooldown + daily cap as single calls, charged for the expected
    // calls (one per appointment and waitlist entry). Retries and repeat
    // offers are charged one by one as the run reaches them.
    const rl = await checkAndBumpRateLimit(env, token, expectedCalls(state));
    if (!rl.ok) return json({ error: rl.error }, 429);
  }
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
    // Re-read under the lock: another step may have saved since the read above.
    const state = await loadEveningState(env);
    if (!state || state.status !== "running") return state;
    await runStep(state, eveningDeps(env));
    await saveEveningState(env, state);
    return state;
  });
  if (busy) return json({ board: projectBoard(state), done: false, busy: true });
  if (!result) return json({ error: "evening run expired" }, 404);
  return json({ board: projectBoard(result), done: result.status !== "running" });
}

const AUTORUN_KV_KEY = "evening:autorun";

async function runEveningScheduledTick(env) {
  if (!env.RATE_LIMIT || !env.CALLE_API_KEY || !env.CALL_TRIGGER_TOKEN) return;
  if ((await env.RATE_LIMIT.get(AUTORUN_KV_KEY)) !== "1") return; // off by default
  const now = new Date();
  const afterStartTime = now.getUTCHours() > 13 || (now.getUTCHours() === 13 && now.getUTCMinutes() >= 30);
  const today = now.toISOString().slice(0, 10);

  const existing = await loadEveningState(env);
  // A leftover dry-run test (any date) never counts as "today's real run
  // already happened" — only a real (non-dryRun) run for today does.
  const alreadyRanForReal = existing && existing.date === today && !existing.dryRun;
  if (!alreadyRanForReal && afterStartTime) {
    const phones = allowedPhones(env);
    if (!phones.length) return;
    const state = initEveningState(phones, false);
    // Same charge as the manual button. When the cooldown or daily cap says
    // no, nothing starts; a later tick in the window tries again.
    const rl = await checkAndBumpRateLimit(env, env.CALL_TRIGGER_TOKEN, expectedCalls(state));
    if (!rl.ok) return;
    await saveEveningState(env, state);
  }
  await withEveningLock(env, async () => {
    // Read under the lock, like /api/evening/step, so a step saved by the
    // page in the meantime isn't overwritten with a stale copy.
    const state = await loadEveningState(env);
    if (!state || state.date !== today || state.dryRun || state.status !== "running") return;
    await runStep(state, eveningDeps(env));
    await saveEveningState(env, state);
  });
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
