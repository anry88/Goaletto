"""Settle elapsed inactivity under the game's allocation gate, never by view count."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import require_utc
from goaletto.domain.player_form import idle_form
from goaletto.infrastructure.models import Player, PlayerRecovery


async def settle_form(session: AsyncSession, player: Player, at: datetime) -> None:
    require_utc(at)
    if player.status == "retired":
        return
    if player.form_activity_at is not None:
        new_form, new_steps = idle_form(
            player.form, player.form_activity_at, player.form_idle_steps or 0, at
        )
        if new_steps == (player.form_idle_steps or 0):
            return
    recovery = await session.get(PlayerRecovery, player.person_id)
    if recovery is not None and recovery.active_match_id is not None:
        return
    if player.form_activity_at is None:
        player.form_activity_at, player.form_idle_steps = at, 0
    else:
        player.form, player.form_idle_steps = new_form, new_steps


def form_activity(player: Player, at: datetime) -> None:
    require_utc(at)
    if player.form_activity_at is None or at >= player.form_activity_at:
        player.form_activity_at, player.form_idle_steps = at, 0
