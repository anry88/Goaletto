"""Calendar deadline only. Ownership mutations belong to application transactions."""

from calendar import monthrange
from datetime import datetime, timedelta


def ownership_deadline(last_game_action_at: datetime) -> datetime:
    if last_game_action_at.tzinfo is None or last_game_action_at.utcoffset() != timedelta(0):
        raise ValueError("Ownership action timestamp must be timezone-aware UTC")
    month_index = last_game_action_at.year * 12 + last_game_action_at.month - 1 + 6
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    day = min(last_game_action_at.day, monthrange(year, month)[1])
    return last_game_action_at.replace(year=year, month=month, day=day)
