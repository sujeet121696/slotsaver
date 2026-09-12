"""All runtime configuration comes from the environment (or a local .env).

Nothing here is hardcoded: swap CALLE_API_KEY (or any setting) in .env and the
app picks it up on next start — no code changes. With no key set, the app runs
with the MockCaller, so local testing never requires credentials.
"""

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines); real env vars take precedence."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Config:
    calle_api_key: str | None
    calle_region: str
    calle_locale: str
    calle_timeout_seconds: float
    clinic_name: str
    agent_persona: str
    doctor_name: str
    groq_api_key: str | None
    groq_model: str
    groq_base_url: str
    test_patient_name: str

    @property
    def real_calls_enabled(self) -> bool:
        return bool(self.calle_api_key)

    @property
    def agent_brain_enabled(self) -> bool:
        return bool(self.groq_api_key)


def load_config(env_file: str | Path = ".env") -> Config:
    _load_dotenv(Path(env_file))
    return Config(
        calle_api_key=os.environ.get("CALLE_API_KEY") or None,
        calle_region=os.environ.get("CALLE_REGION", "IN"),
        calle_locale=os.environ.get("CALLE_LOCALE", "en-US"),
        calle_timeout_seconds=float(os.environ.get("CALLE_TIMEOUT_SECONDS", "300")),
        clinic_name=os.environ.get("CLINIC_NAME", "Dr. Meera's Dental Clinic"),
        agent_persona=os.environ.get("AGENT_PERSONA", "Asha"),
        doctor_name=os.environ.get("DOCTOR_NAME", "Dr. Meera"),
        groq_api_key=os.environ.get("GROQ_API_KEY") or None,
        groq_model=os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
        groq_base_url=os.environ.get(
            "GROQ_BASE_URL", "https://api.groq.com/openai/v1"
        ),
        test_patient_name=os.environ.get("TEST_PATIENT_NAME", "Rohit Sharma"),
    )
