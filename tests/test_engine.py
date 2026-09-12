"""Engine tests — pure stdlib, no keys, no calls: python3 -m unittest discover tests

Exercises every branch of the evening loop on the MockCaller: a straight
confirmation, a cancellation backfilled after a decline, a no-answer retried,
and a reschedule.
"""

import unittest

from slotsaver.caller import ConfirmResult, MockCaller, OfferResult
from slotsaver.demo_data import demo_state, real_demo_state
from slotsaver.engine import run_evening
from slotsaver.models import SlotStatus


class TestEveningRun(unittest.TestCase):
    def test_full_scenario_every_branch(self):
        state = demo_state("Test Clinic")
        caller = MockCaller(
            confirm_script={
                "+919800000002": [ConfirmResult.CANCELLED],
                "+919800000003": [ConfirmResult.NO_ANSWER, ConfirmResult.CONFIRMED],
                "+919800000004": [ConfirmResult.RESCHEDULE],
            },
            offer_script={
                "+919800000011": [OfferResult.DECLINED],
            },
        )

        report = run_evening(state, caller)

        by_id = {a.id: a for a in state.appointments}
        self.assertIs(by_id["a1"].status, SlotStatus.CONFIRMED)
        self.assertIs(by_id["a2"].status, SlotStatus.BACKFILLED)
        self.assertEqual(by_id["a2"].patient, "Arjun Das")  # waitlist took the slot
        self.assertIs(by_id["a3"].status, SlotStatus.CONFIRMED)  # after retry
        self.assertIs(by_id["a4"].status, SlotStatus.RESCHEDULED)

        self.assertEqual(report["confirmed"], 2)
        self.assertEqual(report["cancelled"], 1)
        self.assertEqual(report["backfilled"], 1)
        self.assertEqual(report["rescheduled"], 1)
        self.assertEqual(report["recovered"], 800)

        # 4 confirms + 1 retry + 2 backfill offers (Kavya declines, Arjun accepts)
        self.assertEqual(caller.calls_placed, 7)
        # Kavya declined so she stays on the waitlist; Arjun was consumed
        self.assertEqual([w.name for w in state.waitlist], ["Kavya Menon"])

    def test_unfillable_slot_recovers_nothing(self):
        state = demo_state("Test Clinic")
        caller = MockCaller(
            confirm_script={"+919800000002": [ConfirmResult.CANCELLED]},
            offer_script={
                "+919800000011": [OfferResult.DECLINED],
                "+919800000012": [OfferResult.NO_ANSWER],
            },
        )

        report = run_evening(state, caller)

        by_id = {a.id: a for a in state.appointments}
        self.assertIs(by_id["a2"].status, SlotStatus.CANCELLED)  # stayed empty
        self.assertEqual(report["recovered"], 0)

    def test_take_scenario_matches_rehearsal(self):
        phone = "+919800000000"
        state = real_demo_state("Test Clinic", phone, patient_name="Sujeet")
        caller = MockCaller(
            confirm_script={
                phone: [ConfirmResult.CONFIRMED, ConfirmResult.CANCELLED],
            },
        )

        report = run_evening(state, caller)

        by_id = {a.id: a for a in state.appointments}
        self.assertIs(by_id["a1"].status, SlotStatus.CONFIRMED)
        self.assertIs(by_id["a2"].status, SlotStatus.BACKFILLED)
        self.assertEqual(report["recovered"], 800)
        self.assertEqual(caller.calls_placed, 3)


if __name__ == "__main__":
    unittest.main()
