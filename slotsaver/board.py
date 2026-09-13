"""Live demo board: snapshots ClinicState to board.json after every change.

demo_board.html polls that file once a second, so the browser board updates
as calls land. Wire-up is one line: attach_board(state).

Optionally also relays each snapshot to the hosted Cloudflare Worker (see
worker/index.js's POST /api/board), so the live public board can show a
real local run instead of only the scripted ?demo=1 replay. Opt-in only —
set both BOARD_PUSH_URL and BOARD_PUSH_TOKEN in .env to enable it; with
either unset, nothing changes and no network call is ever made.
"""

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from .models import ClinicState, SlotStatus

def _default_board_path() -> Path:
    # Read lazily (not at import time) so a BOARD_PATH set only in .env — not
    # a real shell env var — still takes effect: run_demo imports this module
    # before config.load_config() has parsed .env.
    return Path(os.environ.get("BOARD_PATH", "board.json"))


def attach_board(state: ClinicState, path: Path | None = None) -> None:
    """Registers the snapshot writer and writes the opening board."""
    if path is None:
        path = _default_board_path()
    state.on_change = lambda s: _write(s, path)
    state.notify()


def _write(state: ClinicState, path: Path) -> None:
    recovered = sum(
        a.fee for a in state.appointments if a.status is SlotStatus.BACKFILLED
    )
    snapshot = {
        "clinic": state.clinic_name,
        "recovered_inr": recovered,
        "appointments": [
            {
                "time": a.time,
                "patient": a.patient,
                "doctor": a.doctor,
                "fee_inr": a.fee,
                "status": a.status.value,
            }
            for a in state.appointments
        ],
        "waitlist": [w.name for w in state.waitlist],
        "log": state.ops_log[-12:],
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snapshot, indent=2))
    tmp.replace(path)  # atomic swap so the poller never reads a half-write

    _push(snapshot)


def _push(snapshot: dict) -> None:
    """Best-effort relay to the hosted board endpoint; never raises — a
    network hiccup or missing config must never interrupt a real run."""
    url = os.environ.get("BOARD_PUSH_URL")
    token = os.environ.get("BOARD_PUSH_TOKEN")
    if not url or not token:
        return
    body = json.dumps({"token": token, "snapshot": snapshot}).encode()
    request = urllib.request.Request(
        url,
        data=body,
        # Cloudflare's bot protection silently drops requests carrying
        # Python's default "Python-urllib/x.y" User-Agent (connection reset,
        # no HTTP response at all) — a custom one passes through cleanly.
        headers={
            "Content-Type": "application/json",
            "User-Agent": "SlotSaver-BoardPush/1.0",
        },
        method="POST",
    )
    # Retry with backoff: pushes fire once per state change (several per
    # call outcome), and a burst of them in quick succession appears to hit
    # a short-lived edge rate limit — an immediate retry lands inside the
    # same window and fails too. A brief pause before retrying gives that
    # window time to clear. Without this, a dropped push (especially the
    # LAST one of a run) leaves the public board frozen on a stale
    # snapshot — including possibly never showing the final morning
    # report — for everyone watching.
    for attempt, backoff in enumerate((0, 0.6, 1.5), start=1):
        if backoff:
            time.sleep(backoff)
        try:
            urllib.request.urlopen(request, timeout=5).close()
            return
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == 3:
                pass  # local board.json already wrote; the run continues
