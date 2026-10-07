"""Tool-guard and unclear-outcome tests — pure stdlib, no keys, no calls.

The agent brain can call the tools in any order, so these check that the
tools themselves refuse what the rules forbid, and that a wrong-person or
unreadable result never gets retried, frees a slot or advances the waitlist.
"""

import unittest

from slotsaver.caller import ConfirmResult, MockCaller, OfferResult
from slotsaver.demo_data import demo_state
from slotsaver.engine import run_evening
from slotsaver.models import SlotStatus
from slotsaver.tools import EveningTools, GuardError


def _by_id(state):
    return {a.id: a for a in state.appointments}


class TestUnclearOutcomes(unittest.TestCase):
    def test_unclear_confirm_is_flagged_not_retried_or_freed(self):
        state = demo_state("Test Clinic")
        caller = MockCaller(
            confirm_script={"+919800000002": [ConfirmResult.WRONG_PERSON_OR_UNCLEAR]}
        )

        report = run_evening(state, caller)

        self.assertIs(_by_id(state)["a2"].status, SlotStatus.NEEDS_ATTENTION)
        self.assertEqual(report["needs_attention"], 1)
        self.assertEqual(report["recovered"], 0)
        # 4 confirms, no retry and no backfill offer for a2
        self.assertEqual(caller.calls_placed, 4)
        self.assertEqual(len(state.waitlist), 2)

    def test_unclear_offer_pauses_backfill(self):
        state = demo_state("Test Clinic")
        caller = MockCaller(
            confirm_script={"+919800000002": [ConfirmResult.CANCELLED]},
            offer_script={"+919800000011": [OfferResult.WRONG_PERSON_OR_UNCLEAR]},
        )

        report = run_evening(state, caller)

        self.assertIs(_by_id(state)["a2"].status, SlotStatus.CANCELLED)
        self.assertEqual(report["recovered"], 0)
        # 4 confirms + 1 offer; Arjun is never called
        self.assertEqual(caller.calls_placed, 5)
        self.assertEqual([w.name for w in state.waitlist], ["Kavya Menon", "Arjun Das"])

    def test_reschedule_is_not_backfilled(self):
        state = demo_state("Test Clinic")
        caller = MockCaller(confirm_script={"+919800000004": [ConfirmResult.RESCHEDULE]})

        run_evening(state, caller)

        self.assertIs(_by_id(state)["a4"].status, SlotStatus.RESCHEDULED)
        self.assertEqual(caller.calls_placed, 4)


class TestToolGuards(unittest.TestCase):
    def setUp(self):
        self.state = demo_state("Test Clinic")

    def test_cannot_offer_a_slot_that_is_not_cancelled(self):
        caller = MockCaller()
        tools = EveningTools(self.state, caller)
        tools.confirm("a1")  # confirmed

        with self.assertRaises(GuardError):
            tools.offer("w1", "a1")
        self.assertEqual(_by_id(self.state)["a1"].patient, "Rohit Sharma")
        self.assertEqual(caller.calls_placed, 1)

    def test_cannot_skip_waitlist_order(self):
        caller = MockCaller(confirm_script={"+919800000002": [ConfirmResult.CANCELLED]})
        tools = EveningTools(self.state, caller)
        tools.confirm("a2")

        with self.assertRaises(GuardError):
            tools.offer("w2", "a2")  # w1 is next
        self.assertEqual(caller.calls_placed, 1)

    def test_retry_only_after_no_answer(self):
        caller = MockCaller()
        tools = EveningTools(self.state, caller)
        tools.confirm("a1")

        with self.assertRaises(GuardError):
            tools.confirm("a1")  # already confirmed
        self.assertEqual(caller.calls_placed, 1)

    def test_at_most_one_retry(self):
        caller = MockCaller(
            confirm_script={
                "+919800000001": [ConfirmResult.NO_ANSWER, ConfirmResult.NO_ANSWER]
            }
        )
        tools = EveningTools(self.state, caller)
        tools.confirm("a1")
        tools.confirm("a1")

        with self.assertRaises(GuardError):
            tools.confirm("a1")
        self.assertIs(_by_id(self.state)["a1"].status, SlotStatus.NEEDS_ATTENTION)
        self.assertEqual(caller.calls_placed, 2)

    def test_waitlist_person_called_at_most_twice(self):
        caller = MockCaller(
            confirm_script={
                "+919800000001": [ConfirmResult.CANCELLED],
                "+919800000002": [ConfirmResult.CANCELLED],
                "+919800000003": [ConfirmResult.CANCELLED],
            },
            offer_script={
                "+919800000011": [OfferResult.DECLINED] * 3,
                "+919800000012": [OfferResult.DECLINED] * 3,
            },
        )

        run_evening(self.state, caller)

        # 4 confirms + Kavya and Arjun twice each; the third slot gets nobody
        self.assertEqual(caller.calls_placed, 8)
        self.assertIs(_by_id(self.state)["a3"].status, SlotStatus.CANCELLED)


if __name__ == "__main__":
    unittest.main()
