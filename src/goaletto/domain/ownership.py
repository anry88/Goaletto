"""Calendar deadline only. Ownership mutations belong to application transactions."""

from calendar import monthrange
from datetime import datetime, timedelta


def ownership_deadline(last_game_action_at: datetime) -> datetime:
    return calendar_months_after(last_game_action_at, 6)


def calendar_months_after(last_game_action_at: datetime, months: int) -> datetime:
    if last_game_action_at.tzinfo is None or last_game_action_at.utcoffset() != timedelta(0):
        raise ValueError("Ownership action timestamp must be timezone-aware UTC")
    if type(months) is not int or not 1 <= months <= 6:
        raise ValueError("Ownership intervals must be one through six calendar months")
    month_index = last_game_action_at.year * 12 + last_game_action_at.month - 1 + months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    day = min(last_game_action_at.day, monthrange(year, month)[1])
    return last_game_action_at.replace(year=year, month=month, day=day)
