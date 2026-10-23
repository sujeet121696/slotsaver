// Tests for the Worker's evening loop (worker/evening.mjs): node --test tests/
// No KV, no network: CALL-E and the rate limiter are fakes. These mirror
// tests/test_tools.py so the JavaScript loop can't drift from tools.py.

import assert from "node:assert/strict";
import { test } from "node:test";

import { MAX_STEP_ERRORS, initEveningState, runStep } from "../worker/evening.mjs";

const PHONE = "+919800000000"; // fictional, never dialed: createCall is a fake

/** Fake CALL-E: each created call gets the next scripted outcome. */
function fakeDeps(outcomes, { extraCalls = Infinity } = {}) {
  const queue = [...outcomes];
  const results = new Map();
  const deps = {
    created: [],
    extraCharged: 0,
    async createCall(req) {
      const id = `call-${deps.created.length + 1}`;
      deps.created.push(req);
      results.set(id, queue.shift() ?? "confirmed");
      return id;
    },
    async getCall(id) {
      return { status: "completed", structured_result: { outcome: results.get(id) } };
    },
    async chargeExtraCall() {
      if (deps.extraCharged >= extraCalls) return false;
      deps.extraCharged += 1;
      return true;
    },
  };
  return deps;
}

async function drive(state, deps) {
  for (let i = 0; i < 100 && state.status === "running"; i++) await runStep(state, deps);
  assert.equal(state.status, "done", "run should finish");
  return state;
}

const statuses = (state) => Object.fromEntries(state.appointments.map((a) => [a.id, a.status]));

test("dry run: confirm, cancel, backfill, no CALL-E and no charge", async () => {
  const deps = fakeDeps([]);
  const state = await drive(initEveningState([PHONE], true), deps);
  assert.deepEqual(statuses(state), { a1: "confirmed", a2: "backfilled" });
  assert.equal(state.recoveredInr, 800);
  assert.equal(deps.created.length, 0);
  assert.equal(deps.extraCharged, 0);
});

test("no answer is flagged until the one retry, like tools.py", async () => {
  const deps = fakeDeps(["no_answer", "confirmed", "confirmed"]);
  const state = initEveningState([PHONE], false);
  await runStep(state, deps); // place a1's confirm
  await runStep(state, deps); // read: no_answer
  assert.equal(state.appointments[0].status, "needs_attention");
  await drive(state, deps);
  assert.deepEqual(statuses(state), { a1: "confirmed", a2: "confirmed" });
  assert.match(deps.created[1].idKey, /-a1-retry$/);
});

test("two no-answers: flagged, never a third call", async () => {
  const deps = fakeDeps(["no_answer", "no_answer", "confirmed"]);
  const state = await drive(initEveningState([PHONE], false), deps);
  assert.equal(state.appointments[0].status, "needs_attention");
  assert.equal(state.calls.a1, 2);
  assert.equal(deps.created.length, 3);
});

test("unclear confirm: flagged, not retried, slot not offered", async () => {
  const deps = fakeDeps(["confirmed", "wrong_person_or_unclear"]);
  const state = await drive(initEveningState([PHONE], false), deps);
  assert.deepEqual(statuses(state), { a1: "confirmed", a2: "needs_attention" });
  assert.equal(deps.created.length, 2);
  assert.equal(state.waitlist.length, 1);
});

test("unclear offer pauses the backfill", async () => {
  const deps = fakeDeps(["confirmed", "cancelled", "wrong_person_or_unclear"]);
  const state = await drive(initEveningState([PHONE], false), deps);
  assert.deepEqual(statuses(state), { a1: "confirmed", a2: "cancelled" });
  assert.equal(state.recoveredInr, 0);
  assert.equal(deps.created.length, 3);
});

test("reschedule is not backfilled", async () => {
  const deps = fakeDeps(["confirmed", "reschedule"]);
  const state = await drive(initEveningState([PHONE], false), deps);
  assert.deepEqual(statuses(state), { a1: "confirmed", a2: "rescheduled" });
  assert.equal(deps.created.length, 2);
});

test("a waitlist person is called at most twice", async () => {
  const state = initEveningState([PHONE], false);
  state.appointments.push({ ...state.appointments[1], id: "a3", patient: "Imran Khan", time: "12:00 PM" });
  // all three cancel; Arjun declines the first two offers and is then skipped
  const deps = fakeDeps(["cancelled", "declined", "cancelled", "declined", "cancelled"]);
  await drive(state, deps);
  assert.equal(state.calls.w1, 2);
  assert.equal(deps.created.length, 5);
  assert.ok(state.log.includes("Slot 12:00 PM could not be refilled tonight"));
});

test("calls beyond the up-front charge are charged one by one", async () => {
  // a1: no answer + retry, a2: cancel, offer to Arjun = 4 calls, 3 booked
  const deps = fakeDeps(["no_answer", "confirmed", "cancelled", "accepted"]);
  const state = await drive(initEveningState([PHONE], false), deps);
  assert.equal(state.callBudget, 4);
  assert.equal(deps.extraCharged, 1);
  assert.equal(state.appointments[1].status, "backfilled");
});

test("daily cap reached mid-run: nothing dialed, slot left for the clinic", async () => {
  const deps = fakeDeps(["no_answer", "confirmed", "cancelled"], { extraCalls: 0 });
  const state = await drive(initEveningState([PHONE], false), deps);
  assert.equal(deps.created.length, 3);
  assert.equal(state.appointments[1].status, "cancelled");
  assert.ok(state.log.some((l) => l.includes("daily call limit reached")));
});

test("repeated CALL-E errors stop the run instead of leaving it running", async () => {
  const deps = fakeDeps([]);
  deps.getCall = async () => { throw new Error("CALL-E lookup failed: 500"); };
  const state = initEveningState([PHONE], false);
  for (let i = 0; i < MAX_STEP_ERRORS + 1; i++) await runStep(state, deps);
  assert.equal(state.status, "done");
  assert.equal(state.cursor.pendingCallId, null);
  assert.deepEqual(statuses(state), { a1: "needs_attention", a2: "needs_attention" });
});

test("one failed step is retried, not counted against the run", async () => {
  const deps = fakeDeps(["confirmed", "confirmed"]);
  const realCreate = deps.createCall;
  let failed = false;
  deps.createCall = async (req) => {
    if (!failed) { failed = true; throw new Error("CALL-E create failed: 503"); }
    return realCreate(req);
  };
  const state = await drive(initEveningState([PHONE], false), deps);
  assert.deepEqual(statuses(state), { a1: "confirmed", a2: "confirmed" });
  assert.equal(state.callsPlaced, 2);
  assert.equal(deps.extraCharged, 0);
});
