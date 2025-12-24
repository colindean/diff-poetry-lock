import logging
import os

DEBUG_ENV_VAR = "DIFF_POETRY_LOCK_DEBUG"

_CONFIGURED = False


def configure_logging() -> None:
    """Configure root logging once, honoring the debug flag env var."""
    global _CONFIGURED
    if _CONFIGURED or logging.getLogger().handlers:
        return

    level = logging.DEBUG if _is_debug_enabled() else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(name)s - %(funcName)s: %(message)s")
    _CONFIGURED = True


def _is_debug_enabled() -> bool:
    value = os.getenv(DEBUG_ENV_VAR, "")
    return value.lower() in {"1", "true", "yes", "on"}
