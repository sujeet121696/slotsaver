"""CALL-E errors, board push and call estimate — pure stdlib, no keys, no calls.

CALL-E's client and urlopen are replaced with fakes, so nothing is dialed
and nothing leaves the machine.
"""

import importlib.util
import os
import time
import unittest
from unittest import mock

from slotsaver import board
from slotsaver.caller import ConfirmResult, StopRun
from slotsaver.demo_data import demo_state, real_demo_state
from slotsaver.run_demo import max_calls

HAS_CALLE = importlib.util.find_spec("calle") is not None
PHONE = "+919800000000"  # fictional; the client below is a mock


@unittest.skipUnless(HAS_CALLE, "calle-ai not installed")
class TestCalleErrors(unittest.TestCase):
    def _caller(self, error):
        from slotsaver.providers.calle import CalleCaller

        with mock.patch("slotsaver.providers.calle.CalleClient") as client_cls:
            client_cls.return_value.calls.create_and_wait.side_effect = error
            caller = CalleCaller("test-key")
        return caller

    def _appt(self):
        return real_demo_state("Test Clinic", PHONE).appointments[0]

    @mock.patch.dict(os.environ, {"ALLOWED_DEMO_PHONES": PHONE})
    def test_sdk_failure_is_unclear_not_a_crash(self):
        from calle import CalleConnectionError, CalleTimeoutError

        for error in (CalleTimeoutError("timed out"), CalleConnectionError("reset")):
            outcome = self._caller(error).confirm_call(self._appt(), "Test Clinic")
            self.assertIs(outcome.result, ConfirmResult.WRONG_PERSON_OR_UNCLEAR)
            self.assertIn("CALL-E error", outcome.summary)

    @mock.patch.dict(os.environ, {"ALLOWED_DEMO_PHONES": PHONE})
    def test_bad_api_key_still_stops_the_run(self):
        from calle import CalleAuthenticationError

        error = CalleAuthenticationError(code="unauthorized", message="bad key", status_code=401)
        with self.assertRaises(StopRun):
            self._caller(error).confirm_call(self._appt(), "Test Clinic")


class TestBoardPush(unittest.TestCase):
    @mock.patch.dict(
        os.environ,
        {"BOARD_PUSH_URL": "https://example.invalid/api/board", "BOARD_PUSH_TOKEN": "t"},
    )
    def test_slow_push_does_not_block_the_run_and_sends_the_latest(self):
        sent = []

        def slow_urlopen(request, timeout):
            time.sleep(0.3)
            sent.append(request.data)
            return mock.MagicMock()

        with mock.patch("slotsaver.board.urllib.request.urlopen", slow_urlopen):
            started = time.monotonic()
            board._push({"n": 1})
            board._push({"n": 2})
            board._push({"n": 3})
            self.assertLess(time.monotonic() - started, 0.2)
            pusher = board._pusher
            if pusher is not None:
                pusher.join(timeout=5)

        # the first push may already be in flight; the newest is always sent
        self.assertTrue(sent[-1].endswith(b'"n": 3}}'))
        self.assertLessEqual(len(sent), 2)


class TestCallEstimate(unittest.TestCase):
    def test_worst_case_calls(self):
        # 2 appointments × (confirm + retry) + Arjun offered both slots
        self.assertEqual(max_calls(real_demo_state("Test Clinic", PHONE)), 6)
        # 4 × 2 + 2 waitlist people × 2 calls each
        self.assertEqual(max_calls(demo_state("Test Clinic")), 12)


if __name__ == "__main__":
    unittest.main()
