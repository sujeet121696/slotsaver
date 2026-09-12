from dataclasses import dataclass, field
from enum import Enum


class SlotStatus(str, Enum):
    SCHEDULED = "scheduled"
    CONFIRMED = "confirmed"
    RESCHEDULED = "rescheduled"
    CANCELLED = "cancelled"
    BACKFILLED = "backfilled"
    NEEDS_ATTENTION = "needs_attention"


@dataclass
class Appointment:
    id: str
    patient: str
    phone: str  # E.164, e.g. "+919800000001"
    time: str  # display time of tomorrow's slot, e.g. "11:00 AM"
    doctor: str
    fee: int  # INR, used for the "revenue recovered" report
    status: SlotStatus = SlotStatus.SCHEDULED


@dataclass
class WaitlistEntry:
    id: str
    name: str
    phone: str


@dataclass
class ClinicState:
    clinic_name: str
    appointments: list[Appointment]
    waitlist: list[WaitlistEntry]
    ops_log: list[str] = field(default_factory=list)
    # Optional observer (e.g. the live demo board) called after every change.
    on_change: object = field(default=None, repr=False)

    def log(self, message: str) -> None:
        self.ops_log.append(message)
        self.notify()

    def notify(self) -> None:
        if self.on_change is not None:
            self.on_change(self)
