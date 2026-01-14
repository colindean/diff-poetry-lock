import logging
import os
from enum import Enum
from typing import Final

DEBUG_ENV_VAR = "DEBUG_MODE"


class _StateKey(Enum):
    """Enum for state dictionary keys."""

    CONFIGURED = "configured"


_STATE: Final[dict[str, bool]] = {_StateKey.CONFIGURED.value: False}


def configure_logging() -> None:
    """Configure root logging once, honoring the debug flag env var."""
    if _STATE[_StateKey.CONFIGURED.value] or logging.getLogger().handlers:
        return

    level = logging.DEBUG if _is_debug_enabled() else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(name)s - %(funcName)s: %(message)s")
    _STATE[_StateKey.CONFIGURED.value] = True


def _is_debug_enabled() -> bool:
    value = os.getenv(DEBUG_ENV_VAR, "")
    return value.lower() in {"1", "true", "yes", "on"}
