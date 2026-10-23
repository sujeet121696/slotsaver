"""VobizCaller (Bolna on a Vobiz number): pure stdlib, no keys, no calls.

Unit tests swap Bolna's HTTP API for a scripted fake transport and time for
a fake clock. The end-to-end test runs a fake Bolna server on 127.0.0.1 and
drives a whole evening through the real urllib transport. Either way
nothing is dialed and nothing leaves the machine.

Fake executions follow the shapes in Bolna's docs (checked 2026-10-23):
execution statuses from GET /executions/{id}, and extracted_data nested as
category -> extraction name -> {"objective", "subjective", ...}.
"""

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from slotsaver.caller import ConfirmResult, OfferResult
from slotsaver.config import load_config
from slotsaver.demo_data import real_demo_state
from slotsaver.engine import run_evening
from slotsaver.models import SlotStatus
from slotsaver.providers import real_caller
from slotsaver.providers.vobiz import (
    BolnaHTTPError,
    VobizAuthError,
    VobizBalanceError,
    VobizCaller,
)

PHONE = "+919800000000"  # fictional; never dialed
API_KEY = "test-key"


def execution(outcome=None, status="completed", notes="", **extra):
    """A finished Bolna execution, with the SlotSaver > Outcome extraction."""
    extracted = None
    if outcome is not None:
        extracted = {
            "SlotSaver": {
                "Outcome": {
                    "objective": outcome,
                    "subjective": notes,
                    "confidence": 0.9,
                    "confidence_label": "High",
                }
            }
        }
    return {"status": status, "extracted_data": extracted, **extra}


class FakeBolna:
    """Answers POST /call with an execution id, then each GET with the next
    scripted execution (the last one repeats), raising it if it's an error."""

    def __init__(self, polls=(), start=None):
        self.polls = list(polls)
        self.start = start if start is not None else {"execution_id": "ex-1", "status": "queued"}
        self.posts = []
        self.gets = 0

    def __call__(self, method, url, body, headers):
        assert headers["Authorization"] == f"Bearer {API_KEY}"
        if method == "POST":
            self.posts.append(body)
            if isinstance(self.start, Exception):
                raise self.start
            return self.start
        self.gets += 1
        item = self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]
        if isinstance(item, Exception):
            raise item
        return item


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@mock.patch.dict(os.environ, {"ALLOWED_DEMO_PHONES": PHONE})
class TestVobizCaller(unittest.TestCase):
    def _caller(self, bolna, timeout=300.0, **kwargs):
        clock = FakeClock()
        return VobizCaller(
            API_KEY, "agent-1", transport=bolna, timeout_seconds=timeout,
            sleep=clock.sleep, clock=clock, **kwargs,
        )

    def _confirm(self, bolna, **kwargs):
        appt = real_demo_state("Test Clinic", PHONE).appointments[0]
        return self._caller(bolna, **kwargs).confirm_call(appt, "Test Clinic")

    # outcomes

    def test_completed_call_returns_the_extracted_outcome(self):
        bolna = FakeBolna([{"status": "ringing"}, {"status": "call-disconnected"},
                           execution("cancelled")])
        self.assertIs(self._confirm(bolna).result, ConfirmResult.CANCELLED)
        self.assertEqual(bolna.gets, 3)  # call-disconnected is not the end

    def test_offer_call_maps_accepted(self):
        state = real_demo_state("Test Clinic", PHONE)
        caller = self._caller(FakeBolna([execution("accepted")]))
        outcome = caller.offer_call(state.waitlist[0], state.appointments[0], "Test Clinic")
        self.assertIs(outcome.result, OfferResult.ACCEPTED)

    def test_carrier_no_answer_busy_and_voicemail_are_no_answer(self):
        for ex in (execution(status="no-answer"), execution(status="busy"),
                   execution("confirmed", answered_by_voice_mail=True)):
            self.assertIs(self._confirm(FakeBolna([ex])).result, ConfirmResult.NO_ANSWER, ex)

    def test_failed_or_unreadable_calls_are_unclear_never_no_answer(self):
        for ex in (execution(status="failed", error_message="SIP 503"),
                   execution(status="error"),
                   execution(None),            # no extraction at all
                   execution("maybe"),         # not an outcome word
                   execution("accepted"),      # an offer outcome on a confirm call
                   {"status": "completed", "extracted_data": {"outcome": "confirmed"}}):  # flat, not Bolna's shape
            outcome = self._confirm(FakeBolna([ex]))
            self.assertIs(outcome.result, ConfirmResult.WRONG_PERSON_OR_UNCLEAR, ex)

    def test_unclear_summary_carries_the_reason(self):
        outcome = self._confirm(FakeBolna([execution(None)]))
        self.assertIn("no valid SlotSaver > Outcome extracted", outcome.summary)

    def test_rescheduled_call_is_unclear_at_once(self):
        bolna = FakeBolna([{"status": "rescheduled"}])
        outcome = self._confirm(bolna)
        self.assertIs(outcome.result, ConfirmResult.WRONG_PERSON_OR_UNCLEAR)
        self.assertIn("may ring later", outcome.summary)
        self.assertEqual(bolna.gets, 1)

    # failures and safety

    def test_poll_timeout_is_unclear_and_never_redials(self):
        bolna = FakeBolna([{"status": "in-progress"}])
        outcome = self._confirm(bolna, timeout=10.0)
        self.assertIs(outcome.result, ConfirmResult.WRONG_PERSON_OR_UNCLEAR)
        self.assertIn("no final status", outcome.summary)
        self.assertEqual(len(bolna.posts), 1)

    def test_failed_start_is_unclear_and_not_resent(self):
        for error in (BolnaHTTPError(500, "boom"), TimeoutError("timed out"), OSError("reset")):
            bolna = FakeBolna(start=error)
            self.assertIs(self._confirm(bolna).result, ConfirmResult.WRONG_PERSON_OR_UNCLEAR)
            self.assertEqual(len(bolna.posts), 1)

    def test_a_failed_poll_is_retried(self):
        bolna = FakeBolna([OSError("reset"), execution("confirmed")])
        self.assertIs(self._confirm(bolna).result, ConfirmResult.CONFIRMED)

    def test_bad_api_key_stops_the_run(self):
        for start in (BolnaHTTPError(401, "unauthorized"), BolnaHTTPError(403, "forbidden")):
            with self.assertRaises(VobizAuthError):
                self._confirm(FakeBolna(start=start))
        with self.assertRaises(VobizAuthError):
            self._confirm(FakeBolna([BolnaHTTPError(401, "unauthorized")]))

    def test_low_balance_stops_the_run(self):
        with self.assertRaises(VobizBalanceError):
            self._confirm(FakeBolna([{"status": "ringing"}, execution(status="balance-low")]))

    def test_refuses_numbers_outside_the_allowlist(self):
        bolna = FakeBolna([execution("confirmed")])
        appt = real_demo_state("Test Clinic", "+919811111111").appointments[0]
        with self.assertRaises(RuntimeError):
            self._caller(bolna).confirm_call(appt, "Test Clinic")
        self.assertEqual(bolna.posts, [])

    # request body

    def test_request_carries_the_shared_wording_and_from_number(self):
        bolna = FakeBolna([execution("confirmed")])
        self._confirm(bolna, from_number="+918000000000")
        body = bolna.posts[0]
        self.assertEqual(body["agent_id"], "agent-1")
        self.assertEqual(body["recipient_phone_number"], PHONE)
        self.assertEqual(body["from_phone_number"], "+918000000000")
        self.assertIn("Test Clinic", body["user_data"]["task"])
        self.assertIn("keep it under 45 seconds", body["user_data"]["task"])
        self.assertNotIn("retry_config", body)  # the engine owns retries
        self.assertNotIn("bypass_call_guardrails", body)

    def test_retry_is_a_new_attempt_and_counted(self):
        bolna = FakeBolna([execution(status="no-answer")])
        caller = self._caller(bolna)
        appt = real_demo_state("Test Clinic", PHONE).appointments[0]
        caller.confirm_call(appt, "Test Clinic")
        caller.confirm_call(appt, "Test Clinic")
        self.assertEqual(caller.calls_placed, 2)
        tags = [p["user_data"]["slotsaver_call"] for p in bolna.posts]
        self.assertEqual(tags, [f"{appt.id}-confirm-attempt1", f"{appt.id}-confirm-attempt2"])


class FakeBolnaServer:
    """Bolna's two endpoints on 127.0.0.1. Each call is in progress on the
    first poll and completed with the next scripted outcome on the second."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []
        self.executions = {}
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, code, payload):
                data = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _authorized(self):
                fake.requests.append((self.command, self.path, self.headers.get("Authorization")))
                if self.headers.get("Authorization") != f"Bearer {API_KEY}":
                    self._reply(401, {"error": 401, "message": "unauthorized"})
                    return False
                return True

            def do_POST(self):
                if not self._authorized():
                    return
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                execution_id = f"ex-{len(fake.executions) + 1}"
                fake.executions[execution_id] = {"body": body, "polls": 0,
                                                 "outcome": fake.outcomes.pop(0)}
                self._reply(200, {"message": "done", "status": "queued",
                                  "execution_id": execution_id})

            def do_GET(self):
                if not self._authorized():
                    return
                ex = fake.executions[self.path.rsplit("/", 1)[-1]]
                ex["polls"] += 1
                if ex["polls"] == 1:
                    self._reply(200, {"status": "in-progress", "extracted_data": None})
                else:
                    self._reply(200, execution(ex["outcome"]))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


@mock.patch.dict(os.environ, {"ALLOWED_DEMO_PHONES": PHONE})
class TestVobizEndToEnd(unittest.TestCase):
    def test_real_scenario_over_http(self):
        # The same 3-call cast as --real: confirm, cancel, waitlist accepts.
        state = real_demo_state("Test Clinic", PHONE)
        with FakeBolnaServer(["confirmed", "cancelled", "accepted"]) as bolna:
            caller = VobizCaller(API_KEY, "agent-1", base_url=bolna.url, poll_seconds=0.01)
            report = run_evening(state, caller)

        statuses = [a.status for a in state.appointments]
        self.assertEqual(statuses, [SlotStatus.CONFIRMED, SlotStatus.BACKFILLED])
        self.assertEqual(report["backfilled"], 1)
        self.assertEqual(caller.calls_placed, 3)
        posts = [r for r in bolna.requests if r[0] == "POST"]
        self.assertEqual(len(posts), 3)
        self.assertTrue(all(r[1] == "/call" for r in posts))
        tasks = [ex["body"]["user_data"]["task"] for ex in bolna.executions.values()]
        self.assertIn("confirm their appointment", tasks[0])
        self.assertIn("a slot just opened", tasks[2])

    def test_wrong_key_over_http_stops_the_run(self):
        state = real_demo_state("Test Clinic", PHONE)
        with FakeBolnaServer(["confirmed"]) as bolna:
            caller = VobizCaller("wrong-key", "agent-1", base_url=bolna.url)
            with self.assertRaises(VobizAuthError):
                run_evening(state, caller)
        self.assertEqual(len(bolna.requests), 1)


class TestCallerSetting(unittest.TestCase):
    def _config(self, env):
        with mock.patch.dict(os.environ, env, clear=True):
            return load_config(env_file="does-not-exist.env")

    def test_default_is_calle(self):
        config = self._config({"CALLE_API_KEY": "k"})
        self.assertEqual(config.caller, "calle")
        self.assertTrue(config.real_calls_enabled)

    def test_vobiz_needs_both_bolna_keys(self):
        self.assertFalse(self._config({"CALLER": "vobiz", "CALLE_API_KEY": "k",
                                       "BOLNA_API_KEY": "k"}).real_calls_enabled)
        config = self._config({"CALLER": "vobiz", "BOLNA_API_KEY": "k",
                               "BOLNA_AGENT_ID": "a"})
        self.assertTrue(config.real_calls_enabled)

    def test_unknown_caller_is_a_clear_error(self):
        with self.assertRaises(SystemExit):
            self._config({"CALLER": "twilio"})

    def test_factory_builds_vobiz_from_settings(self):
        config = self._config({"CALLER": "vobiz", "BOLNA_API_KEY": "k",
                               "BOLNA_AGENT_ID": "a", "VOBIZ_FROM_NUMBER": "+918000000000"})
        caller = real_caller(config)
        self.assertIsInstance(caller, VobizCaller)
        self.assertEqual(caller.from_number, "+918000000000")


if __name__ == "__main__":
    unittest.main()
