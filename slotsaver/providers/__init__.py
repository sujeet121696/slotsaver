"""Real call providers behind the Caller protocol (docs/TELEPHONY.md).

  calle.py    CALL-E: a whole calling agent, structured result built in
  vobiz.py    a Vobiz number with a Bolna voice agent on top
  scripts.py  Asha's wording and log lines, shared so providers can't drift

CALLER in .env picks one. Imports stay lazy, so the mock run needs neither
provider's packages.
"""

from ..config import Config

LABELS = {"calle": "CALL-E", "vobiz": "Vobiz via Bolna"}


def real_caller(config: Config):
    """The real caller CALLER picks, built from .env settings."""
    if config.caller == "vobiz":
        from .vobiz import VobizCaller

        return VobizCaller(
            config.bolna_api_key,
            config.bolna_agent_id,
            config.agent_persona,
            from_number=config.vobiz_from_number,
            timeout_seconds=config.bolna_timeout_seconds,
        )
    from .calle import CalleCaller

    return CalleCaller(
        config.calle_api_key,
        config.agent_persona,
        region=config.calle_region,
        locale=config.calle_locale,
        timeout_seconds=config.calle_timeout_seconds,
    )
