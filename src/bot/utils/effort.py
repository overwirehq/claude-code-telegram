"""Helpers for the per-conversation Claude effort override."""

from typing import Optional, cast

from claude_agent_sdk import EffortLevel
from telegram.ext import ContextTypes

EFFORT_LEVELS: tuple[EffortLevel, ...] = (
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)
EFFORT_STATE_KEY = "claude_effort"


def get_effort(context: ContextTypes.DEFAULT_TYPE) -> Optional[EffortLevel]:
    """Return the validated effort override for the current conversation."""
    user_data = context.user_data
    if user_data is None:
        return None

    value = user_data.get(EFFORT_STATE_KEY)
    if value in EFFORT_LEVELS:
        return cast(EffortLevel, value)
    return None


def set_effort(
    context: ContextTypes.DEFAULT_TYPE, effort: Optional[EffortLevel]
) -> None:
    """Set an effort override, or remove it to restore the SDK default."""
    user_data = context.user_data
    if user_data is None:
        return

    if effort is None:
        user_data.pop(EFFORT_STATE_KEY, None)
    else:
        user_data[EFFORT_STATE_KEY] = effort
