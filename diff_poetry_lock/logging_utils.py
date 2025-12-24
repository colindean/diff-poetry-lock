import logging
import os
from typing import Final

DEBUG_ENV_VAR = "DIFF_POETRY_LOCK_DEBUG"

_STATE: Final[dict[str, bool]] = {"configured": False}


def configure_logging() -> None:
    """Configure root logging once, honoring the debug flag env var."""
    if _STATE["configured"] or logging.getLogger().handlers:
        return

    level = logging.DEBUG if _is_debug_enabled() else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(name)s - %(funcName)s: %(message)s")
    _STATE["configured"] = True


def _is_debug_enabled() -> bool:
    value = os.getenv(DEBUG_ENV_VAR, "")
    return value.lower() in {"1", "true", "yes", "on"}
