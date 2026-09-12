"""Live demo board: snapshots ClinicState to board.json after every change.

demo_board.html polls that file once a second, so the browser board updates
as calls land. Wire-up is one line: attach_board(state).
"""

import json
import os
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
