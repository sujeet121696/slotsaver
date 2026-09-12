"""Place exactly ONE real CALL-E confirm call to a phone you own.

    .venv/bin/python -m slotsaver.test_call +91XXXXXXXXXX

Spends 1 of the free-call budget. The number must be listed in
ALLOWED_DEMO_PHONES in .env — the caller refuses anything else.
"""

import sys

from .calle_caller import CalleCaller, allowed_phones
from .config import load_config
from .models import Appointment
from .run_demo import _ensure_utf8_stdio


def main() -> None:
    _ensure_utf8_stdio()

    if len(sys.argv) != 2 or not sys.argv[1].startswith("+"):
        raise SystemExit("usage: python -m slotsaver.test_call +91XXXXXXXXXX (E.164)")
    phone = sys.argv[1]

    config = load_config()
    if not config.real_calls_enabled:
        raise SystemExit("CALLE_API_KEY is not set in .env")
    if phone not in allowed_phones():
        raise SystemExit(
            f"{phone} is not in ALLOWED_DEMO_PHONES (.env). Add it first — "
            "real calls may only reach phones we own."
        )

    appt = Appointment(
        "test1", config.test_patient_name, phone, "10:00 AM", config.doctor_name, 800
    )
    caller = CalleCaller(
        config.calle_api_key,
        config.agent_persona,
        region=config.calle_region,
        locale=config.calle_locale,
        timeout_seconds=config.calle_timeout_seconds,
    )

    print(f"Placing ONE confirm call to {phone} — answer as the patient…")
    outcome = caller.confirm_call(appt, config.clinic_name)
    print(f"\nResult: {outcome.result.value}\nSummary: {outcome.summary}")


if __name__ == "__main__":
    main()
