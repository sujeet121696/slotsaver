"""Run the full evening loop: python3 -m slotsaver.run_demo [--brain engine|strands]

--brain engine  (default) deterministic loop, zero dependencies, zero cost
--brain strands the Strands agent brain orchestrating the same tools
                (needs GROQ_API_KEY in .env)
--slow N        pause N seconds around each mock call so the live board
                (demo_board.html) updates at real-call pace
--take          mock rehearsal of the EXACT --real scenario: same cast, same
                names, same 3-call order, zero cost. Rehearse with --take
                until smooth, then swap it for --real.
--real          PLACE REAL PHONE CALLS (~3 calls, spends credits) using the
                same trimmed cast, your allowlisted phone playing every
                patient. Needs CALLE_API_KEY + ALLOWED_DEMO_PHONES.

Every run also writes board.json after each state change — serve the live
board with `python3 -m http.server 8787` and open
http://localhost:8787/demo_board.html

Scripted scenario exercising every branch:
- Rohit confirms straight away
- Priya cancels -> backfill: Kavya declines, Arjun accepts (slot rescued)
- Imran doesn't answer, then confirms on the retry
- Sunita asks to reschedule
"""

import sys
import time

from .board import attach_board
from .caller import ConfirmResult, MockCaller, OfferResult
from .config import load_config
from .demo_data import demo_state, real_demo_state
from .engine import run_evening


class SlowCaller:
    """Wraps a caller with a pause per call so the board updates visibly."""

    def __init__(self, inner, delay: float) -> None:
        self.inner = inner
        self.delay = delay

    def confirm_call(self, appt, clinic_name):
        time.sleep(self.delay)
        return self.inner.confirm_call(appt, clinic_name)

    def offer_call(self, entry, appt, clinic_name):
        time.sleep(self.delay)
        return self.inner.offer_call(entry, appt, clinic_name)

    @property
    def calls_placed(self):
        return self.inner.calls_placed


def build_caller(config):
    if config.real_calls_enabled:
        # Rehearsals stay on the mock (zero credits); real calls need the
        # explicit --real flag.
        print("note: CALLE_API_KEY is set — pass --real to place real calls; "
              "this run uses the mock\n")
    return MockCaller(
        confirm_script={
            "+919800000002": [ConfirmResult.CANCELLED],
            "+919800000003": [ConfirmResult.NO_ANSWER, ConfirmResult.CONFIRMED],
            "+919800000004": [ConfirmResult.RESCHEDULE],
        },
        offer_script={
            "+919800000011": [OfferResult.DECLINED],
        },
    )


def main() -> None:
    brain = "engine"
    if "--brain" in sys.argv:
        brain = sys.argv[sys.argv.index("--brain") + 1]
    if brain not in ("engine", "strands"):
        raise SystemExit(f"unknown brain '{brain}' — use engine or strands")

    config = load_config()

    if "--real" in sys.argv:
        from .calle_caller import CalleCaller, allowed_phones

        if not config.real_calls_enabled:
            raise SystemExit("--real needs CALLE_API_KEY in .env")
        phones = sorted(allowed_phones())
        if not phones:
            raise SystemExit("--real needs ALLOWED_DEMO_PHONES in .env")
        idx = sys.argv.index("--real")
        arg = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
        if arg.startswith("+"):
            phone = arg
            if phone not in phones:
                raise SystemExit(f"{phone} is not in ALLOWED_DEMO_PHONES")
        elif len(phones) == 1:
            phone = phones[0]
        else:
            raise SystemExit(
                "several allowlisted phones — pick one: "
                f"--real {' | '.join(phones)}"
            )
        state = real_demo_state(
            config.clinic_name,
            phone,
            patient_name=config.test_patient_name,
            doctor=config.doctor_name,
        )
        caller = CalleCaller(
            config.calle_api_key,
            config.agent_persona,
            region=config.calle_region,
            locale=config.calle_locale,
            timeout_seconds=config.calle_timeout_seconds,
        )
        calls = len(state.appointments) + len(state.waitlist)
        print(f"REAL CALLS: about to dial {phone} ~{calls} times (spends credits).")
        print(
            f"Answer as: {config.test_patient_name} (confirm) → "
            f"Priya (cancel) → Arjun (accept the slot)."
        )
        if input("Type yes to continue: ").strip().lower() != "yes":
            raise SystemExit("aborted, no calls placed")
    elif "--take" in sys.argv:
        # Free rehearsal of the exact --real scenario: same cast and order,
        # scripted outcomes instead of real calls.
        rehearsal_phone = "+919800000000"  # fictional — mock only, never dialed
        state = real_demo_state(
            config.clinic_name,
            rehearsal_phone,
            patient_name=config.test_patient_name,
            doctor=config.doctor_name,
        )
        caller = MockCaller(
            confirm_script={
                rehearsal_phone: [ConfirmResult.CONFIRMED, ConfirmResult.CANCELLED],
            },
            # the waitlist offer defaults to accepted — same as the take
        )
        print(
            f"TAKE REHEARSAL (mock, ₹0): {config.test_patient_name} confirms → "
            f"Priya cancels → Arjun accepts. Swap --take for --real to record.\n"
        )
    else:
        state = demo_state(
            config.clinic_name,
            patient_name=config.test_patient_name,
            doctor=config.doctor_name,
        )
        caller = build_caller(config)

    if "--real" not in sys.argv and "--slow" in sys.argv:
        caller = SlowCaller(caller, float(sys.argv[sys.argv.index("--slow") + 1]))

    attach_board(state)

    print(f"SlotSaver evening run — {state.clinic_name} — mock caller — brain: {brain}\n")

    if brain == "strands":
        if not config.agent_brain_enabled:
            raise SystemExit("--brain strands needs GROQ_API_KEY in .env")
        from .agent import run_evening_agent

        report = run_evening_agent(state, caller, config)
        print(f"\nAgent's report to the clinic:\n{report}\n")
    else:
        run_evening(state, caller)

    for line in state.ops_log:
        print(f"  {line}")

    print("\nTomorrow's board:")
    for appt in state.appointments:
        print(f"  {appt.time:>9}  {appt.status.value:<16} {appt.patient}")
    print(f"\nCalls placed: {caller.calls_placed}")


if __name__ == "__main__":
    main()
