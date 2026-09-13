/**
 * SlotSaver hosted-demo Worker.
 *
 * Serves the static demo board (via the `ASSETS` binding) and adds two
 * private, token-gated API routes so the page's "Call me" button can place
 * a real CALL-E test call without running anything locally:
 *
 *   POST /api/call         { id, token }        -> { call_id }
 *   GET  /api/call?id&token                     -> { status, outcome, summary }
 *   GET  /api/numbers?token                     -> [{ id, label }]
 *
 * Security model:
 *   - The real allowlisted phone numbers never reach the browser. The
 *     client only ever sees a masked label + a small integer `id`; the
 *     Worker maps that id back to a real number itself, server-side, from
 *     the ALLOWED_DEMO_PHONES secret. A public page's HTML/JS source is
 *     always viewable by anyone, hidden UI or not — so nothing sensitive
 *     may ever be embedded in it, only referenced by an opaque id.
 *   - Every route requires `token` to equal the CALL_TRIGGER_TOKEN secret,
 *     compared in constant time. Without it every route 403s.
 *   - CALLE_API_KEY lives only in Worker secrets and is attached to the
 *     CALL-E request server-side; it is never sent to or readable by the
 *     browser.
 *   - A KV-backed cooldown + daily cap limits how many calls one token can
 *     trigger, so a mistake or a leaked token can't silently drain credit.
 */

const CALLE_BASE = "https://api.heycall-e.com";
const CLINIC_NAME = "Dr. Meera's Dental Clinic";
const AGENT_PERSONA = "Asha";
const DOCTOR_NAME = "Dr. Meera";
const TEST_PATIENT_NAME = "Rohit Sharma"; // generic demo name, not a real patient
const CALLE_REGION = "IN";
const CALLE_LOCALE = "en-US";

const COOLDOWN_SECONDS = 20;
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

function confirmTask() {
  return (
    `You are ${AGENT_PERSONA}, the friendly phone assistant of ${CLINIC_NAME}. ` +
    `Call ${TEST_PATIENT_NAME} and confirm their appointment TOMORROW at ` +
    `10:00 AM with ${DOCTOR_NAME}. Introduce yourself as the clinic's ` +
    `assistant up front. You are talking to a person — never press any ` +
    `phone keys and never wait on hold. The moment you have the answer, ` +
    `thank them, say goodbye, and end the call; keep it under 45 seconds. ` +
    `Outcomes: they will come (confirmed); they cancel (cancelled) — thank ` +
    `them and say the slot will be freed; they want a different time ` +
    `(reschedule); nobody answered or it wasn't them (no_answer).`
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
  const { id, token } = body || {};
  if (!(await requireToken(request, env, token))) return json({ error: "unauthorized" }, 403);

  const phones = allowedPhones(env);
  const idx = Number(id);
  if (!Number.isInteger(idx) || idx < 0 || idx >= phones.length) {
    return json({ error: "unknown number id" }, 400);
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
      task: confirmTask(),
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

    return env.ASSETS.fetch(request);
  },
};
