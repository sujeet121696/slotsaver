"""Fictional demo clinic — made-up people and +91-format demo numbers.

Real patient data never ships with the repo; a real deployment would read
these from the clinic's own sheet.
"""

from .models import Appointment, ClinicState, WaitlistEntry


def demo_state(
    clinic_name: str,
    patient_name: str = "Rohit Sharma",
    doctor: str = "Dr. Meera",
) -> ClinicState:
    return ClinicState(
        clinic_name=clinic_name,
        appointments=[
            Appointment("a1", patient_name, "+919800000001", "10:00 AM", doctor, 800),
            Appointment("a2", "Priya Nair", "+919800000002", "11:00 AM", doctor, 800),
            Appointment("a3", "Imran Khan", "+919800000003", "12:00 PM", doctor, 1200),
            Appointment("a4", "Sunita Rao", "+919800000004", "4:00 PM", doctor, 800),
        ],
        waitlist=[
            WaitlistEntry("w1", "Kavya Menon", "+919800000011"),
            WaitlistEntry("w2", "Arjun Das", "+919800000012"),
        ],
    )


def real_demo_state(
    clinic_name: str,
    phone: str,
    patient_name: str = "Rohit Sharma",
    doctor: str = "Dr. Meera",
) -> ClinicState:
    """Trimmed 3-call cast for the demo take: one phone plays every patient.
    Answer as: patient_name (confirm), Priya (cancel), Arjun (accept the
    freed slot). Used by --real (real calls) and --take (mock rehearsal)."""
    return ClinicState(
        clinic_name=clinic_name,
        appointments=[
            Appointment("a1", patient_name, phone, "10:00 AM", doctor, 800),
            Appointment("a2", "Priya Nair", phone, "11:00 AM", doctor, 800),
        ],
        waitlist=[
            WaitlistEntry("w1", "Arjun Das", phone),
        ],
    )
