"""Hand-count acceptance for first/second match-opportunity KPIs; no database.

Expected n/N below are counted by hand, never by the calculator under test.
T is the fixed report time, all intervals are [start, end) UTC.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from goaletto.application.match_opportunities import (
    CoverageState,
    FixtureProjection,
    ManagerProjection,
    OwnershipProjection,
    UserEffectProjection,
    evaluate_match_opportunities,
)

FULL = CoverageState(True, True, True)


def dt(y: int, mo: int, d: int, h: int = 12, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def manager(
    *, internal: bool = False, enabled: bool = True, bot: bool = False
) -> ManagerProjection:
    return ManagerProjection(uuid4(), internal, enabled, bot)


def ownership(
    mgr: ManagerProjection,
    club_id,  # type: ignore[no-untyped-def]
    claimed: datetime,
    ended: datetime | None = None,
) -> OwnershipProjection:
    return OwnershipProjection(uuid4(), mgr.manager_id, club_id, claimed, ended)


def fixture(
    club_id,  # type: ignore[no-untyped-def]
    *,
    kind: str = "league",
    status: str = "finished",
    scheduled: datetime,
    lock: datetime | None = None,
    actual_start: datetime | None = None,
    actual_finish: datetime | None = None,
    official: bool = False,
) -> FixtureProjection:
    return FixtureProjection(
        uuid4(),
        club_id,
        kind,
        status,
        scheduled,
        lock if lock is not None else scheduled - timedelta(minutes=1),
        actual_start,
        actual_finish,
        official,
    )


def effect(
    mgr: ManagerProjection,
    fix: FixtureProjection,
    own: OwnershipProjection,
    *,
    name: str = "match_opened",
    origin: str = "user",
    occurred: datetime,
    recorded: datetime | None = None,
    key: str | None = None,
) -> UserEffectProjection:
    return UserEffectProjection(
        key or f"k-{uuid4().hex}",
        mgr.manager_id,
        fix.fixture_id,
        own.ownership_id,
        name,
        origin,
        occurred,
        recorded or occurred,
        1,
    )


def mature_fixture(club_id, start: datetime) -> FixtureProjection:  # type: ignore[no-untyped-def]
    finish = start + timedelta(minutes=33)
    return fixture(
        club_id,
        scheduled=start,
        lock=start - timedelta(minutes=1),
        actual_start=start,
        actual_finish=finish,
    )


def test_first_participation_two_of_four() -> None:
    # Hand count: 4 mature first windows. A opens board, B sends a valid
    # command, C only gets delivery, D silent. Expected first 2/4.
    report_at = dt(2026, 10, 20, 12)
    start = dt(2026, 10, 11, 18)
    managers = tuple(manager() for _ in range(4))
    clubs = [uuid4() for _ in range(4)]
    owns = tuple(
        ownership(m, c, dt(2026, 10, 10, 12)) for m, c in zip(managers, clubs, strict=True)
    )
    fixes = tuple(mature_fixture(c, start) for c in clubs)
    effects = (
        effect(
            managers[0],
            fixes[0],
            owns[0],
            name="match_report_opened",
            occurred=start + timedelta(hours=1),
        ),
        effect(
            managers[1],
            fixes[1],
            owns[1],
            name="match_command_accepted",
            occurred=start + timedelta(hours=2),
        ),
        # Duplicate of B's command with the same stable key: still one manager.
        UserEffectProjection(
            effects_dup_key := "dup-key-b",
            managers[1].manager_id,
            fixes[1].fixture_id,
            owns[1].ownership_id,
            "match_command_accepted",
            "user",
            start + timedelta(hours=2, minutes=5),
            start + timedelta(hours=2, minutes=5),
            1,
        ),
        effect(
            managers[1],
            fixes[1],
            owns[1],
            name="match_command_accepted",
            occurred=start + timedelta(hours=2, minutes=5),
            key=effects_dup_key,
        ),
        # C only gets delivery: system event, never activity.
        effect(
            managers[2],
            fixes[2],
            owns[2],
            name="result_delivered",
            origin="system",
            occurred=start + timedelta(hours=1),
        ),
        # Applied after accepted is system work, not a second human action.
        effect(
            managers[1],
            fixes[1],
            owns[1],
            name="match_command_applied",
            origin="system",
            occurred=start + timedelta(hours=3),
        ),
    )
    report = evaluate_match_opportunities(
        managers=managers,
        ownerships=owns,
        fixtures=fixes,
        effects=effects,
        coverage=FULL,
        report_at=report_at,
    )
    assert report.first.eligible_N == 4
    assert report.first.participated_n == 2
    assert report.first.rate == pytest.approx(0.5)
    assert report.first.waiting == 0
    # C and D are mature non-participants, not waiting/coverage.
    assert report.first.unknown_late == 0


def test_second_return_one_of_one_plus_waiting() -> None:
    # Hand count: A and B both participate in first. A's second window is
    # mature and A opens it (1/1). B's second fixture is still in the future,
    # so B waits. Second must not divide by all starters or by both first
    # participants before maturity.
    report_at = dt(2026, 10, 27, 12)
    first_start = dt(2026, 10, 11, 18)
    second_start_mature = dt(2026, 10, 18, 18)
    second_start_future = dt(2026, 10, 28, 18)
    managers = (manager(), manager())
    clubs = [uuid4(), uuid4()]
    owns = (
        ownership(managers[0], clubs[0], dt(2026, 10, 10, 12)),
        ownership(managers[1], clubs[1], dt(2026, 10, 10, 12)),
    )
    firsts = (mature_fixture(clubs[0], first_start), mature_fixture(clubs[1], first_start))
    seconds = (
        mature_fixture(clubs[0], second_start_mature),
        fixture(
            clubs[1],
            scheduled=second_start_future,
            lock=second_start_future - timedelta(minutes=1),
            actual_start=None,
            actual_finish=None,
            status="scheduled",
        ),
    )
    effects = (
        effect(managers[0], firsts[0], owns[0], occurred=first_start + timedelta(hours=1)),
        effect(managers[1], firsts[1], owns[1], occurred=first_start + timedelta(hours=1)),
        effect(
            managers[0],
            seconds[0],
            owns[0],
            name="match_report_opened",
            occurred=second_start_mature + timedelta(hours=1),
        ),
    )
    report = evaluate_match_opportunities(
        managers=managers,
        ownerships=owns,
        fixtures=firsts + seconds,
        effects=effects,
        coverage=FULL,
        report_at=report_at,
    )
    assert report.first.eligible_N == 2
    assert report.first.participated_n == 2
    assert report.second.eligible_N == 1
    assert report.second.participated_n == 1
    assert report.second.waiting == 1
    assert report.second.rate == pytest.approx(1.0)
    assert report.second_no_first == 0  # both participated first


def test_first_fixture_in_seven_days_is_waiting() -> None:
    # First fixture 9 days after claim, report 1h after finish: window
    # [start-24h, finish+24h) is not mature yet. Waiting, not a miss.
    claim = dt(2026, 10, 11, 12)
    start = dt(2026, 10, 20, 18)
    finish = start + timedelta(minutes=33)
    report_at = finish + timedelta(hours=1)
    m = manager()
    club = uuid4()
    own = ownership(m, club, claim)
    fix = mature_fixture(club, start)
    report = evaluate_match_opportunities(
        managers=(m,),
        ownerships=(own,),
        fixtures=(fix,),
        effects=(),
        coverage=FULL,
        report_at=report_at,
    )
    assert report.first.eligible_N == 0
    assert report.first.waiting == 1
    assert report.first.rate is None


def test_claim_exact_lock_admits_claim_after_skips() -> None:
    # Claim exactly at lock admits; claim 1s after lock misses this fixture.
    start = dt(2026, 10, 11, 18)
    lock = start - timedelta(minutes=1)
    report_at = dt(2026, 10, 13, 12)
    ma = manager()
    mb = manager()
    ca, cb = uuid4(), uuid4()
    owna = ownership(ma, ca, lock)
    ownb = ownership(mb, cb, lock + timedelta(seconds=1))
    fa = mature_fixture(ca, start)
    fb = mature_fixture(cb, start)
    # Rebuild fb with the same lock so mb is strictly after it.
    fb = FixtureProjection(
        fb.fixture_id,
        fb.club_id,
        fb.fixture_type,
        fb.status,
        fb.scheduled_start,
        lock,
        fb.actual_start,
        fb.actual_finish,
        False,
    )
    report = evaluate_match_opportunities(
        managers=(ma, mb),
        ownerships=(owna, ownb),
        fixtures=(fa, fb),
        effects=(),
        coverage=FULL,
        report_at=report_at,
    )
    by_id = {d.manager_id: d for d in report.details}
    assert by_id[ma.manager_id].first_status == "mature"
    # mb has no qualifying fixture: explicit no_calendar, not waiting-as-miss.
    assert by_id[mb.manager_id].first_status == "no_calendar"
    assert report.first.eligible_N == 1
    assert report.first.no_calendar == 1


def test_postponement_and_pause_shift_window() -> None:
    # Scheduled 10 Oct, postponed to actual 18 Oct 18:00-18:33. Window is
    # [17 Oct 18:00, 19 Oct 18:33). Report 18 Oct 20:00 is still immature even
    # though the calendar date passed long ago.
    scheduled = dt(2026, 10, 10, 18)
    actual = dt(2026, 10, 18, 18)
    finish = actual + timedelta(minutes=33)
    report_at = dt(2026, 10, 18, 20)
    m = manager()
    club = uuid4()
    own = ownership(m, club, dt(2026, 10, 9, 12))
    fix = fixture(
        club,
        scheduled=scheduled,
        lock=scheduled - timedelta(minutes=1),
        actual_start=actual,
        actual_finish=finish,
    )
    act = effect(m, fix, own, occurred=dt(2026, 10, 18, 19))
    report = evaluate_match_opportunities(
        managers=(m,),
        ownerships=(own,),
        fixtures=(fix,),
        effects=(act,),
        coverage=FULL,
        report_at=report_at,
    )
    assert report.first.waiting == 1
    assert report.first.eligible_N == 0
    # Same shifted window is mature one day later and the action counts.
    late_report = evaluate_match_opportunities(
        managers=(m,),
        ownerships=(own,),
        fixtures=(fix,),
        effects=(act,),
        coverage=FULL,
        report_at=finish + timedelta(hours=24),
    )
    assert late_report.first.eligible_N == 1
    assert late_report.first.participated_n == 1


def test_cancelled_skipped_pending_predecessor_blocks() -> None:
    # Cancelled fixtures are skipped: a cancelled first round plus a mature
    # second round gives first=mature(second round). But a pending earlier
    # candidate blocks a later mature one: first=waiting.
    report_at = dt(2026, 10, 20, 12)
    m_cancel = manager()
    club_c = uuid4()
    own_c = ownership(m_cancel, club_c, dt(2026, 10, 9, 12))
    cancelled = fixture(
        club_c,
        status="cancelled",
        scheduled=dt(2026, 10, 11, 18),
        actual_start=None,
        actual_finish=None,
    )
    mature = mature_fixture(club_c, dt(2026, 10, 18, 18))
    rep_cancel = evaluate_match_opportunities(
        managers=(m_cancel,),
        ownerships=(own_c,),
        fixtures=(cancelled, mature),
        effects=(),
        coverage=FULL,
        report_at=report_at,
    )
    assert rep_cancel.details[0].first_fixture_id == mature.fixture_id
    assert rep_cancel.details[0].first_status == "mature"

    m_block = manager()
    club_b = uuid4()
    own_b = ownership(m_block, club_b, dt(2026, 10, 9, 12))
    pending = fixture(
        club_b,
        status="scheduled",
        scheduled=dt(2026, 10, 11, 18),
        actual_start=None,
        actual_finish=None,
    )
    later = mature_fixture(club_b, dt(2026, 10, 18, 18))
    # Make the later fixture mature at report time; the earlier pending must
    # still block skipping to it.
    rep_block = evaluate_match_opportunities(
        managers=(m_block,),
        ownerships=(own_b,),
        fixtures=(pending, later),
        effects=(),
        coverage=FULL,
        report_at=report_at,
    )
    assert rep_block.details[0].first_fixture_id == pending.fixture_id
    assert rep_block.details[0].first_status == "waiting"
    assert rep_block.first.eligible_N == 0


def test_ownership_interruption_and_reclaim() -> None:
    # A owns at lock but loses the club before the window end: interrupted.
    # B reclaims the same club later and owns the next mature fixture: B's
    # first is that next fixture, not a continuation of A.
    start1 = dt(2026, 10, 11, 18)
    finish1 = start1 + timedelta(minutes=33)
    window_end1 = finish1 + timedelta(hours=24)
    start2 = dt(2026, 10, 18, 18)
    report_at = dt(2026, 10, 20, 12)
    ma = manager()
    mb = manager()
    club = uuid4()
    owna = ownership(ma, club, dt(2026, 10, 10, 12), ended=finish1 + timedelta(hours=1))
    ownb = ownership(mb, club, finish1 + timedelta(hours=2))
    assert owna.ended_at is not None and owna.ended_at < window_end1
    f1 = mature_fixture(club, start1)
    f2 = mature_fixture(club, start2)
    report = evaluate_match_opportunities(
        managers=(ma, mb),
        ownerships=(owna, ownb),
        fixtures=(f1, f2),
        effects=(),
        coverage=FULL,
        report_at=report_at,
    )
    by_id = {d.manager_id: d for d in report.details}
    assert by_id[ma.manager_id].first_status == "interrupted"
    assert by_id[ma.manager_id].first_fixture_id == f1.fixture_id
    # mb claimed after f1's lock? No: mb claimed after f1 window started but
    # before f2 lock, so f1 is not qualifying for mb (claim after lock) and
    # f2 is mb's first mature opportunity.
    assert by_id[mb.manager_id].first_fixture_id == f2.fixture_id
    assert by_id[mb.manager_id].first_status == "mature"
    assert report.first.interrupted == 1
    assert report.first.eligible_N == 1


def test_wrong_fixture_or_ownership_ignored() -> None:
    # Effects for another fixture or another ownership period do not count.
    # other_own starts after the first lock, so the first fixture still
    # belongs to own; an effect stamped with other_own is a wrong-ownership
    # ref and must be ignored.
    report_at = dt(2026, 10, 20, 12)
    start = dt(2026, 10, 11, 18)
    m = manager()
    club = uuid4()
    own = ownership(m, club, dt(2026, 10, 10, 12))
    other_own = OwnershipProjection(uuid4(), m.manager_id, club, dt(2026, 10, 11, 19), None)
    fix = mature_fixture(club, start)
    other_fix = mature_fixture(club, start + timedelta(days=7))
    effects = (
        effect(m, other_fix, own, occurred=start + timedelta(hours=1)),
        effect(m, fix, other_own, occurred=start + timedelta(hours=1)),
    )
    report = evaluate_match_opportunities(
        managers=(m,),
        ownerships=(own, other_own),
        fixtures=(fix, other_fix),
        effects=effects,
        coverage=FULL,
        report_at=report_at,
    )
    assert report.first.eligible_N == 1
    assert report.first.participated_n == 0


def test_internal_optout_bot_excluded() -> None:
    report_at = dt(2026, 10, 20, 12)
    start = dt(2026, 10, 11, 18)
    mi = manager(internal=True)
    mo = manager(enabled=False)
    mb = manager(bot=True)
    managers = (mi, mo, mb)
    clubs = [uuid4() for _ in managers]
    owns = tuple(
        ownership(m, c, dt(2026, 10, 10, 12)) for m, c in zip(managers, clubs, strict=True)
    )
    fixes = tuple(mature_fixture(c, start) for c in clubs)
    effects = tuple(
        effect(m, f, o, occurred=start + timedelta(hours=1))
        for m, f, o in zip(managers, fixes, owns, strict=True)
    )
    report = evaluate_match_opportunities(
        managers=managers,
        ownerships=owns,
        fixtures=fixes,
        effects=effects,
        coverage=FULL,
        report_at=report_at,
    )
    assert report.excluded_managers == 3
    assert report.first.eligible_N == 0
    assert report.first.participated_n == 0
    assert report.first.rate is None
    assert all(d.first_status == "excluded" for d in report.details)


def test_recorded_late_is_unknown_then_visible() -> None:
    # Occurred inside the window but recorded after report: unknown_late, not n.
    start = dt(2026, 10, 11, 18)
    finish = start + timedelta(minutes=33)
    window_end = finish + timedelta(hours=24)
    m = manager()
    club = uuid4()
    own = ownership(m, club, dt(2026, 10, 10, 12))
    fix = mature_fixture(club, start)
    act = effect(
        m,
        fix,
        own,
        occurred=window_end - timedelta(hours=1),
        recorded=window_end + timedelta(hours=1),
    )
    early = evaluate_match_opportunities(
        managers=(m,),
        ownerships=(own,),
        fixtures=(fix,),
        effects=(act,),
        coverage=FULL,
        report_at=window_end,
    )
    assert early.first.eligible_N == 1
    assert early.first.participated_n == 0
    assert early.first.unknown_late == 1
    later = evaluate_match_opportunities(
        managers=(m,),
        ownerships=(own,),
        fixtures=(fix,),
        effects=(act,),
        coverage=FULL,
        report_at=window_end + timedelta(hours=2),
    )
    assert later.first.participated_n == 1
    assert later.first.unknown_late == 0


def test_exact_24h_boundaries() -> None:
    # Window [start-24h, finish+24h): lower bound inclusive, upper exclusive.
    start = dt(2026, 10, 11, 18)
    finish = start + timedelta(minutes=33)
    window_start = start - timedelta(hours=24)
    window_end = finish + timedelta(hours=24)
    m_in = manager()
    m_out = manager()
    ca, cb = uuid4(), uuid4()
    owna = ownership(m_in, ca, dt(2026, 10, 10, 12))
    ownb = ownership(m_out, cb, dt(2026, 10, 10, 12))
    fa = mature_fixture(ca, start)
    fb = mature_fixture(cb, start)
    eff_in = effect(m_in, fa, owna, occurred=window_start)
    eff_out = effect(m_out, fb, ownb, occurred=window_end)
    report = evaluate_match_opportunities(
        managers=(m_in, m_out),
        ownerships=(owna, ownb),
        fixtures=(fa, fb),
        effects=(eff_in, eff_out),
        coverage=FULL,
        report_at=window_end,
    )
    by_id = {d.manager_id: d for d in report.details}
    assert by_id[m_in.manager_id].first_participated is True
    assert by_id[m_out.manager_id].first_participated is False
    assert report.first.participated_n == 1
    # Maturity boundary itself is inclusive: report exactly at window end counts.
    assert report.first.eligible_N == 2
    early = evaluate_match_opportunities(
        managers=(m_in,),
        ownerships=(owna,),
        fixtures=(fa,),
        effects=(eff_in,),
        coverage=FULL,
        report_at=window_end - timedelta(seconds=1),
    )
    assert early.first.waiting == 1
    assert early.first.eligible_N == 0


def test_zero_mature_is_not_zero_percent() -> None:
    m = manager()
    club = uuid4()
    own = ownership(m, club, dt(2026, 10, 10, 12))
    future = fixture(
        club,
        status="scheduled",
        scheduled=dt(2026, 10, 25, 18),
        actual_start=None,
        actual_finish=None,
    )
    report = evaluate_match_opportunities(
        managers=(m,),
        ownerships=(own,),
        fixtures=(future,),
        effects=(),
        coverage=FULL,
        report_at=dt(2026, 10, 13, 12),
    )
    assert report.first.eligible_N == 0
    assert report.first.rate is None
    assert report.first.waiting == 1


def test_incomplete_coverage_is_not_proven_absence() -> None:
    # Same mature setup, but any incomplete history/events turns mature into
    # explicit coverage instead of a false denominator.
    start = dt(2026, 10, 11, 18)
    report_at = dt(2026, 10, 20, 12)
    for cov in (
        CoverageState(False, True, True),
        CoverageState(True, False, True),
        CoverageState(True, True, False),
    ):
        m = manager()
        club = uuid4()
        own = ownership(m, club, dt(2026, 10, 10, 12))
        fix = mature_fixture(club, start)
        report = evaluate_match_opportunities(
            managers=(m,),
            ownerships=(own,),
            fixtures=(fix,),
            effects=(),
            coverage=cov,
            report_at=report_at,
        )
        assert report.coverage_limited is True
        assert report.first.eligible_N == 0
        assert report.first.coverage == 1
        assert report.first.rate is None


def test_abandoned_unsupported_and_cup_skipped() -> None:
    # Abandoned without a proven official result is unsupported (blocks, no
    # false KPI) even with has_official_result carried for future mapping.
    # Cup fixtures are a separate route and are skipped to the next league one.
    report_at = dt(2026, 10, 20, 12)
    m_aban = manager()
    club_a = uuid4()
    own_a = ownership(m_aban, club_a, dt(2026, 10, 10, 12))
    aban = fixture(
        club_a,
        status="abandoned",
        scheduled=dt(2026, 10, 11, 18),
        actual_start=dt(2026, 10, 11, 18),
        actual_finish=dt(2026, 10, 11, 18, 33),
        official=False,
    )
    rep_aban = evaluate_match_opportunities(
        managers=(m_aban,),
        ownerships=(own_a,),
        fixtures=(aban,),
        effects=(),
        coverage=FULL,
        report_at=report_at,
    )
    assert rep_aban.details[0].first_status == "unsupported"
    assert rep_aban.first.eligible_N == 0
    assert rep_aban.first.unsupported == 1

    m_cup = manager()
    club_c = uuid4()
    own_c = ownership(m_cup, club_c, dt(2026, 10, 10, 12))
    cup = fixture(
        club_c,
        kind="cup",
        scheduled=dt(2026, 10, 11, 18),
        actual_start=dt(2026, 10, 11, 18),
        actual_finish=dt(2026, 10, 11, 18, 33),
    )
    league = mature_fixture(club_c, dt(2026, 10, 18, 18))
    rep_cup = evaluate_match_opportunities(
        managers=(m_cup,),
        ownerships=(own_c,),
        fixtures=(cup, league),
        effects=(),
        coverage=FULL,
        report_at=report_at,
    )
    assert rep_cup.details[0].first_fixture_id == league.fixture_id
    assert rep_cup.details[0].first_status == "mature"


def test_non_activity_never_counts() -> None:
    # System/automation origins, delivery, applied and unknown names are not
    # activity even inside a mature window.
    report_at = dt(2026, 10, 20, 12)
    start = dt(2026, 10, 11, 18)
    m = manager()
    club = uuid4()
    own = ownership(m, club, dt(2026, 10, 10, 12))
    fix = mature_fixture(club, start)
    noise = (
        effect(
            m, fix, own, name="match_opened", origin="system", occurred=start + timedelta(hours=1)
        ),
        effect(
            m,
            fix,
            own,
            name="match_opened",
            origin="automation",
            occurred=start + timedelta(hours=1),
        ),
        effect(
            m,
            fix,
            own,
            name="result_delivered",
            origin="system",
            occurred=start + timedelta(hours=1),
        ),
        effect(
            m,
            fix,
            own,
            name="match_command_applied",
            origin="system",
            occurred=start + timedelta(hours=1),
        ),
        effect(m, fix, own, name="club_opened", origin="user", occurred=start + timedelta(hours=1)),
    )
    report = evaluate_match_opportunities(
        managers=(m,),
        ownerships=(own,),
        fixtures=(fix,),
        effects=noise,
        coverage=FULL,
        report_at=report_at,
    )
    assert report.first.participated_n == 0
    assert report.first.eligible_N == 1


def test_missing_actual_times_is_unavailable() -> None:
    # Finished without actual times cannot form [actual-24h, actual+24h): it
    # blocks as unavailable rather than silently skipping to a later fixture.
    report_at = dt(2026, 10, 20, 12)
    m = manager()
    club = uuid4()
    own = ownership(m, club, dt(2026, 10, 10, 12))
    broken = fixture(
        club,
        status="finished",
        scheduled=dt(2026, 10, 11, 18),
        actual_start=None,
        actual_finish=None,
    )
    later = mature_fixture(club, dt(2026, 10, 18, 18))
    report = evaluate_match_opportunities(
        managers=(m,),
        ownerships=(own,),
        fixtures=(broken, later),
        effects=(),
        coverage=FULL,
        report_at=report_at,
    )
    assert report.details[0].first_fixture_id == broken.fixture_id
    assert report.details[0].first_status == "unavailable"
    assert report.first.unavailable == 1


def test_strict_inputs_and_determinism() -> None:
    m = manager()
    club = uuid4()
    own = ownership(m, club, dt(2026, 10, 10, 12))
    fix = mature_fixture(club, dt(2026, 10, 11, 18))
    report_at = dt(2026, 10, 20, 12)
    with pytest.raises(ValueError):
        evaluate_match_opportunities(
            managers=(m, m),
            ownerships=(own,),
            fixtures=(fix,),
            effects=(),
            coverage=FULL,
            report_at=report_at,
        )
    with pytest.raises(ValueError):
        evaluate_match_opportunities(
            managers=(m,),
            ownerships=(own,),
            fixtures=(fix, fix),
            effects=(),
            coverage=FULL,
            report_at=report_at,
        )
    naive = datetime(2026, 10, 20, 12)
    with pytest.raises(ValueError):
        evaluate_match_opportunities(
            managers=(m,),
            ownerships=(own,),
            fixtures=(fix,),
            effects=(),
            coverage=FULL,
            report_at=naive,
        )
    unknown_own = ownership(manager(), uuid4(), dt(2026, 10, 10, 12))
    with pytest.raises(ValueError):
        evaluate_match_opportunities(
            managers=(m,),
            ownerships=(unknown_own,),
            fixtures=(fix,),
            effects=(),
            coverage=FULL,
            report_at=report_at,
        )
    # Deterministic regardless of input order.
    ma = manager()
    mb = manager()
    ca, cb = uuid4(), uuid4()
    oa = ownership(ma, ca, dt(2026, 10, 10, 12))
    ob = ownership(mb, cb, dt(2026, 10, 10, 12))
    fa = mature_fixture(ca, dt(2026, 10, 11, 18))
    fb = mature_fixture(cb, dt(2026, 10, 11, 18))
    first = evaluate_match_opportunities(
        managers=(ma, mb),
        ownerships=(oa, ob),
        fixtures=(fa, fb),
        effects=(),
        coverage=FULL,
        report_at=report_at,
    )
    second = evaluate_match_opportunities(
        managers=(mb, ma),
        ownerships=(ob, oa),
        fixtures=(fb, fa),
        effects=(),
        coverage=FULL,
        report_at=report_at,
    )
    assert [d.manager_id for d in first.details] == [d.manager_id for d in second.details]
    assert first.first.eligible_N == second.first.eligible_N == 2


def test_both_clubs_share_fixture_and_preclaim_activity_is_excluded() -> None:
    from dataclasses import replace

    start = dt(2026, 10, 11, 18)
    a, b = manager(), manager()
    ca, cb = uuid4(), uuid4()
    oa = ownership(a, ca, start - timedelta(hours=1))
    ob = ownership(b, cb, start - timedelta(days=2))
    fa = mature_fixture(ca, start)
    fb = replace(fa, club_id=cb)
    result = evaluate_match_opportunities(
        managers=(a, b),
        ownerships=(oa, ob),
        fixtures=(fa, fb),
        effects=(
            effect(a, fa, oa, occurred=start - timedelta(hours=2)),
            effect(b, fb, ob, occurred=start),
        ),
        coverage=FULL,
        report_at=start + timedelta(days=2),
    )
    assert (result.first.participated_n, result.first.eligible_N) == (1, 2)


def test_ended_ownership_does_not_admit_later_fixture() -> None:
    start = dt(2026, 10, 11, 18)
    m, club = manager(), uuid4()
    own = ownership(m, club, start - timedelta(days=4), start - timedelta(days=2))
    result = evaluate_match_opportunities(
        managers=(m,),
        ownerships=(own,),
        fixtures=(mature_fixture(club, start),),
        effects=(),
        coverage=FULL,
        report_at=start + timedelta(days=2),
    )
    assert result.first.eligible_N == 0 and result.first.interrupted == 0
    assert result.first.no_calendar == 1


def test_postponed_fixture_order_uses_actual_start() -> None:
    from dataclasses import replace

    m, club = manager(), uuid4()
    early, later = dt(2026, 10, 11, 18), dt(2026, 10, 18, 18)
    own = ownership(m, club, early - timedelta(days=2))
    postponed = replace(
        mature_fixture(club, early), actual_start=later, actual_finish=later + timedelta(minutes=33)
    )
    other = mature_fixture(club, early + timedelta(days=1))
    result = evaluate_match_opportunities(
        managers=(m,),
        ownerships=(own,),
        fixtures=(postponed, other),
        effects=(),
        coverage=FULL,
        report_at=later + timedelta(days=2),
    )
    assert result.details[0].first_fixture_id == other.fixture_id


def test_report_validator_rejects_unreconciled_groups_and_nan_rate() -> None:
    from dataclasses import asdict

    from goaletto.application.match_opportunities import validate_match_opportunity_report

    m, club = manager(), uuid4()
    start = dt(2026, 10, 11, 18)
    report = evaluate_match_opportunities(
        managers=(m,),
        ownerships=(ownership(m, club, start - timedelta(days=2)),),
        fixtures=(mature_fixture(club, start),),
        effects=(),
        coverage=FULL,
        report_at=start + timedelta(days=2),
    )
    valid = asdict(report)
    validate_match_opportunity_report(valid)
    valid["first"]["waiting"] = 1
    with pytest.raises(ValueError, match="reconcile"):
        validate_match_opportunity_report(valid)
    valid = asdict(report)
    valid["first"]["rate"] = float("nan")
    with pytest.raises(ValueError, match="Rate"):
        validate_match_opportunity_report(valid)
