"""Pure neutral-final ticket split (AND-125 computational subpackage).

This module is a deterministic calculator only.  It does not touch the ORM,
migrations, bot/application/worker, match runtime, ledger, portraits or ``en.json``.
It does not assign kickoff times, book a venue in the database, run extra time
or send notifications: the orchestration (Codex) owns all of that.

Contract
--------
Explicit frozen input :class:`NeutralFinalSnapshot`:

* ``venue_id`` — opaque neutral-arena identifier chosen by the orchestration.
  The venue club (if any) receives no payout from this function.
* ``capacity`` — arena seats for this final (nonnegative ``BIGINT``).
* ``ticket_price`` — integer price per spectator (nonnegative ``BIGINT``).
* ``club_a_id`` / ``club_b_id`` — the two finalists (distinct UUIDs).
* ``supporters_a`` / ``supporters_b`` — frozen supporter counts of each finalist.
* ``policy_version`` — must equal :data:`NEUTRAL_FINAL_POLICY`.
* ``seed`` — explicit nonnegative ``BIGINT`` (``bool`` rejected) used only to
  break the odd-remainder tie deterministically.
* ``neutral`` — explicit flag, must be ``True``.  Neutrality travels as this
  flag, never as a home/away permutation.

:func:`settle_neutral_final` returns an immutable :class:`NeutralFinalResult`
with ``crowd``, ``gross``, ``payout_a``, ``payout_b``, ``odd_recipient`` plus
an explicit ``home_advantage = 0`` and ``neutral = True``.

Design assumptions (implementation choices, NOT user-approved balance)
----------------------------------------------------------------------
* Demand reuses the *shape* of ``stadium-demand-v1`` (see ``domain/stadium.py``
  and ``docs/stadium.md``) but with a fixed neutral rate
  :data:`NEUTRAL_DEMAND_BPS` = 8000 applied to the **sum** of both supporter
  bases: ``crowd = min(capacity, (supporters_a + supporters_b) * 8000 // 10000)``.
  Both clubs therefore influence demand symmetrically; swapping A/B never
  changes ``crowd``/``gross``.  The 8000 value mirrors the base of the current
  stadium policy without its tier/place/form bonuses (a neutral final has no
  host), and is a starting implementation constant, not a previously approved
  user coefficient.
* Gross is integer-only: ``gross = crowd * ticket_price`` (no ``float``).
  It must fit a nonnegative ``BIGINT``; otherwise :func:`settle_neutral_final`
  raises ``ValueError`` so the caller picks a smaller venue/price instead of
  silently overflowing.
* The split is exactly even: ``half, rem = divmod(gross, 2)``.  When ``rem``
  is 0 both clubs receive ``half`` and ``odd_recipient`` is ``None``.  When
  ``rem`` is 1 the extra minimal unit goes to one finalist, chosen
  deterministically from ``seed`` plus the sorted club IDs and venue ID via
  ``stable_seed``.  Hence ``payout_a + payout_b == gross`` always and
  ``abs(payout_a - payout_b) <= 1``.  There is no host-takes-all payout and no
  third payout to the venue.
* ``home_advantage`` is always integer ``0``; consumers must read this field
  instead of inferring advantage from slot order.

Integration note for Codex (durable wiring lives outside this pure module)
--------------------------------------------------------------------------
* Freeze before start (at lineup lock / when both finalists are known) and
  persist durably: ``venue_id``, ``capacity``, ``ticket_price``, both club IDs,
  both supporter counts, ``seed``, ``policy_version``.  Never recompute from
  live tables on replay: supporters drift, expansions change capacity, prices
  change.  Retry/replay must reload the same frozen snapshot.
* At the final result, write **two** ledger credits atomically in one DB
  transaction together with the durable result row: one credit of ``payout_a``
  to ``club_a_id``, one of ``payout_b`` to ``club_b_id``.  Commit once.
* Idempotency keys are per fixture+club, e.g.
  ``f"neutral-final:{fixture_id}:{club_id}"`` (or ``uuid5(fixture_id, club)``),
  with a unique constraint on the ledger operation key.  A retry replays the
  same snapshot, derives the same amounts, and reuses the same keys, so the
  second attempt is a no-op instead of a second payout.
* Retry/rollback safety: this function has no side effects, so re-calling it
  cannot pay twice.  A rolled-back transaction removes both credits together;
  a retried transaction hits the existing idempotency keys and inserts nothing
  new.  The odd-remainder recipient is already pinned in the frozen result, so
  a different ``seed`` must never be introduced on retry.
* Still outside this module: venue booking/availability, capacity upgrades
  racing the freeze, extra time/penalty runtime (AND-124), kickoff/jobs inside
  the ten-day horizon, notification outbox, the actual ledger schema, and where
  ``ticket_price``/supporter counts come from.  Unknown/eliminated opponents
  must never acquire invented times or payouts here.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from goaletto.domain.finances import MAX_MONEY, money
from goaletto.domain.world import stable_seed

NEUTRAL_FINAL_POLICY = "neutral-final-v1"
NEUTRAL_DEMAND_BPS = 8000
HOME_ADVANTAGE = 0

__all__ = [
    "NEUTRAL_FINAL_POLICY",
    "NEUTRAL_DEMAND_BPS",
    "HOME_ADVANTAGE",
    "NeutralFinalSnapshot",
    "NeutralFinalResult",
    "settle_neutral_final",
]


def _check_seed(seed: int) -> None:
    if type(seed) is not int or not 0 <= seed < 2**63:
        raise ValueError("Seed must fit nonnegative BIGINT")


def _check_club_id(value: UUID, name: str) -> None:
    if not isinstance(value, UUID):
        raise ValueError(f"{name} must be UUID")


@dataclass(frozen=True)
class NeutralFinalSnapshot:
    """Frozen explicit inputs for one neutral final."""

    policy_version: str
    venue_id: UUID
    capacity: int
    ticket_price: int
    club_a_id: UUID
    club_b_id: UUID
    supporters_a: int
    supporters_b: int
    seed: int
    neutral: bool

    def __post_init__(self) -> None:
        if self.policy_version != NEUTRAL_FINAL_POLICY:
            raise ValueError("Invalid neutral-final policy")
        _check_club_id(self.venue_id, "Venue id")
        _check_club_id(self.club_a_id, "Club A id")
        _check_club_id(self.club_b_id, "Club B id")
        if self.club_a_id == self.club_b_id:
            raise ValueError("Neutral final needs two distinct clubs")
        money(self.capacity)
        money(self.ticket_price)
        money(self.supporters_a)
        money(self.supporters_b)
        _check_seed(self.seed)
        if type(self.neutral) is not bool or self.neutral is not True:
            raise ValueError("Neutral final must stay neutral; use league runtime for home/away")


@dataclass(frozen=True)
class NeutralFinalResult:
    """Immutable neutral-final settlement (inputs pinned + computed split)."""

    policy_version: str
    venue_id: UUID
    capacity: int
    ticket_price: int
    club_a_id: UUID
    club_b_id: UUID
    supporters_a: int
    supporters_b: int
    seed: int
    neutral: bool
    home_advantage: int
    crowd: int
    gross: int
    payout_a: int
    payout_b: int
    odd_recipient: UUID | None

    def __post_init__(self) -> None:
        if self.policy_version != NEUTRAL_FINAL_POLICY:
            raise ValueError("Invalid neutral-final policy")
        _check_club_id(self.venue_id, "Venue id")
        _check_club_id(self.club_a_id, "Club A id")
        _check_club_id(self.club_b_id, "Club B id")
        if self.club_a_id == self.club_b_id:
            raise ValueError("Neutral final needs two distinct clubs")
        money(self.capacity)
        money(self.ticket_price)
        money(self.supporters_a)
        money(self.supporters_b)
        _check_seed(self.seed)
        if type(self.neutral) is not bool or self.neutral is not True:
            raise ValueError("Neutral final must stay neutral; use league runtime for home/away")
        if type(self.home_advantage) is not int or self.home_advantage != 0:
            raise ValueError("Neutral final grants no home advantage")
        money(self.crowd)
        money(self.gross)
        money(self.payout_a)
        money(self.payout_b)
        if self.crowd > self.capacity:
            raise ValueError("Crowd exceeds venue capacity")
        expected_crowd = min(
            self.capacity, (self.supporters_a + self.supporters_b) * NEUTRAL_DEMAND_BPS // 10_000
        )
        if self.crowd != expected_crowd:
            raise ValueError("Crowd differs from frozen demand policy")
        if self.gross != self.crowd * self.ticket_price:
            raise ValueError("Gross must equal crowd times ticket price")
        if self.gross > MAX_MONEY:
            raise ValueError("Gross must fit a nonnegative BIGINT")
        if self.payout_a + self.payout_b != self.gross:
            raise ValueError("Payouts must sum to gross")
        if abs(self.payout_a - self.payout_b) > 1:
            raise ValueError("Payouts differ by more than one money unit")
        if self.gross % 2 == 0:
            if self.odd_recipient is not None:
                raise ValueError("Even gross has no odd recipient")
        else:
            recipient = _odd_recipient(
                seed=self.seed,
                venue_id=self.venue_id,
                club_a_id=self.club_a_id,
                club_b_id=self.club_b_id,
            )
            if self.odd_recipient != recipient:
                raise ValueError("Odd recipient differs from frozen draw")
            larger = self.club_a_id if self.payout_a > self.payout_b else self.club_b_id
            if larger != self.odd_recipient:
                raise ValueError("Odd recipient must receive the extra money unit")


def _odd_recipient(*, seed: int, venue_id: UUID, club_a_id: UUID, club_b_id: UUID) -> UUID:
    """Deterministically pick which finalist takes an odd remainder unit.

    Sorted by hex so swapping A/B keeps the same club (not the same slot) as
    the recipient.  Stable for a fixed seed; different seeds may pick either
    club but each seed always replays the same choice.
    """
    low_hex, high_hex = sorted((club_a_id.hex, club_b_id.hex))
    key = f"{NEUTRAL_FINAL_POLICY}:odd:{low_hex}:{high_hex}:{venue_id.hex}"
    draw = stable_seed(seed, key) % 2
    winner_hex = low_hex if draw == 0 else high_hex
    if club_a_id.hex == winner_hex:
        return club_a_id
    return club_b_id


def settle_neutral_final(snapshot: NeutralFinalSnapshot) -> NeutralFinalResult:
    """Split neutral-final ticket income evenly between the two finalists.

    :param snapshot: frozen arena + participant inputs (see module docstring).
    :returns: immutable result with ``crowd``, ``gross``, ``payout_a``,
        ``payout_b``, ``odd_recipient`` (``None`` on even gross), plus
        ``neutral=True`` and ``home_advantage=0``.
    :raises ValueError: on any invalid input or on ``BIGINT`` overflow.
    """
    if not isinstance(snapshot, NeutralFinalSnapshot):
        raise ValueError("Snapshot must be a NeutralFinalSnapshot")
    # Dataclass validation already ran in __post_init__; recompute purely.
    total_supporters = snapshot.supporters_a + snapshot.supporters_b
    crowd = min(snapshot.capacity, total_supporters * NEUTRAL_DEMAND_BPS // 10_000)
    money(crowd)
    gross = money(crowd * snapshot.ticket_price)
    half, remainder = divmod(gross, 2)
    recipient = (
        _odd_recipient(
            seed=snapshot.seed,
            venue_id=snapshot.venue_id,
            club_a_id=snapshot.club_a_id,
            club_b_id=snapshot.club_b_id,
        )
        if remainder
        else None
    )
    payout_a = half + int(recipient == snapshot.club_a_id)
    payout_b = half + int(recipient == snapshot.club_b_id)
    return NeutralFinalResult(
        policy_version=snapshot.policy_version,
        venue_id=snapshot.venue_id,
        capacity=snapshot.capacity,
        ticket_price=snapshot.ticket_price,
        club_a_id=snapshot.club_a_id,
        club_b_id=snapshot.club_b_id,
        supporters_a=snapshot.supporters_a,
        supporters_b=snapshot.supporters_b,
        seed=snapshot.seed,
        neutral=True,
        home_advantage=0,
        crowd=crowd,
        gross=gross,
        payout_a=payout_a,
        payout_b=payout_b,
        odd_recipient=recipient,
    )
