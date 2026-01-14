import os
from enum import Enum
from typing import Final

from loguru import logger

DEBUG_ENV_VAR = "DEBUG_MODE"


class _StateKey(Enum):
    """Enum for state dictionary keys."""

    CONFIGURED = "configured"


_STATE: Final[dict[str, bool]] = {_StateKey.CONFIGURED.value: False}


def configure_logging() -> None:
    """Configure logging once, honoring the debug flag env var."""
    if _STATE[_StateKey.CONFIGURED.value]:
        return

    # Remove default handler
    logger.remove()

    level = "DEBUG" if _is_debug_enabled() else "INFO"
    logger.add(lambda msg: print(msg, end=""), level=level)
    _STATE[_StateKey.CONFIGURED.value] = True


def _is_debug_enabled() -> bool:
    value = os.getenv(DEBUG_ENV_VAR, "")
    return value.lower() in {"1", "true", "yes", "on"}
