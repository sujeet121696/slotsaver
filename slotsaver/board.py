"""Live demo board: snapshots ClinicState to board.json after every change.

demo_board.html polls that file once a second, so the browser board updates
as calls land. Wire-up is one line: attach_board(state).
"""

import json
import os
from pathlib import Path

from .models import ClinicState, SlotStatus

BOARD_PATH = Path(os.environ.get("BOARD_PATH", "board.json"))


def attach_board(state: ClinicState, path: Path = BOARD_PATH) -> None:
    """Registers the snapshot writer and writes the opening board."""
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
