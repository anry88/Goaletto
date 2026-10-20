"""One confirmed name per ownership; drafts and refusals survive process restarts."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.academy import current_season
from goaletto.application.game_actions import Actor, PermissionDenied, owned_club
from goaletto.domain.club_names import validate_name
from goaletto.domain.game_cycle import midnight
from goaletto.infrastructure.models import ClubNameChoice, ClubNameInput


@dataclass(frozen=True)
class RenameScreen:
    ownership_id: UUID
    name: str
    state: str
    draft_id: UUID | None
    candidate: str | None
    allowed: bool


async def screen(session: AsyncSession, actor: Actor) -> RenameScreen:
    owner, club = await owned_club(session, actor)
    row = await session.get(ClubNameChoice, owner.id)
    if row is None:
        row = ClubNameChoice(ownership_id=owner.id, state="offer")
        session.add(row)
        await session.flush()
    season = await current_season(session, club.id)
    offseason = (
        season.status == "scheduled"
        and season.cycle_epoch is not None
        and actor.at < midnight(season.cycle_epoch)
    )
    allowed = row.state != "done" and (row.declined_at is None or offseason)
    return RenameScreen(owner.id, club.name, row.state, row.draft_id, row.candidate, allowed)


async def action(
    session: AsyncSession,
    actor: Actor,
    ownership_id: UUID,
    kind: str,
    draft_id: UUID | None = None,
) -> RenameScreen:
    view = await screen(session, actor)
    if view.ownership_id != ownership_id:
        raise PermissionDenied("Stale ownership")
    row = await session.get(ClubNameChoice, ownership_id)
    assert row is not None
    if row.state == "done":
        return view
    if not view.allowed:
        return view
    if kind == "input":
        # Old edit/confirm/decline buttons cannot replace a newer preview.
        if draft_id != row.draft_id:
            raise PermissionDenied("Stale name preview")
        row.state, row.candidate = "input", None
        row.draft_id = actor.operation_id
    elif kind == "keep":
        if draft_id != row.draft_id:
            raise PermissionDenied("Stale name preview")
        row.state, row.candidate, row.draft_id = "declined", None, None
        row.declined_at = actor.at
    elif kind == "confirm":
        if row.state != "preview" or draft_id is None or row.draft_id != draft_id:
            raise PermissionDenied("Stale name preview")
        _, club = await owned_club(session, actor)
        assert row.candidate is not None
        name = validate_name(row.candidate)
        if name == club.name:
            raise ValueError("Name has not changed")
        row.original_name, row.changed_at = club.name, actor.at
        club.name, row.state = name, "done"
        from goaletto.application.analytics import Surface
        from goaletto.application.game_actions import user_effect

        owner, _ = await owned_club(session, actor)
        await user_effect(
            session, actor, "club_opened", Surface.CLUB, ownership=owner, club_id=club.id
        )
    else:
        raise ValueError("Invalid rename action")
    await session.flush()
    return await screen(session, actor)


async def submit(session: AsyncSession, actor: Actor, value: str) -> RenameScreen:
    view = await screen(session, actor)
    row = await session.get(ClubNameChoice, view.ownership_id)
    assert row is not None
    old = await session.get(ClubNameInput, actor.operation_id)
    if old is not None:
        if old.ownership_id != view.ownership_id or old.value != value:
            raise PermissionDenied("Name input operation reused")
        return view
    if not view.allowed or row.state != "input":
        raise PermissionDenied("Name input is not open")
    name = validate_name(value)
    if name == view.name:
        raise ValueError("Name has not changed")
    session.add(
        ClubNameInput(operation_id=actor.operation_id, ownership_id=view.ownership_id, value=value)
    )
    row.candidate, row.draft_id, row.state = name, actor.operation_id, "preview"
    await session.flush()
    return await screen(session, actor)
