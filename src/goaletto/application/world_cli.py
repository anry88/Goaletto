"""World preview by default. --apply explicitly targets the configured game DB."""

import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from goaletto.application.world import PreparedWorld, initialize_world, prepare_world, world_summary
from goaletto.config import Settings
from goaletto.domain.finances import INITIAL_CAPITAL
from goaletto.domain.world import SKILL_NAMES, WorldOptions
from goaletto.infrastructure.database import Database


def preview_document(world: PreparedWorld) -> dict[str, Any]:
    leagues: list[dict[str, Any]] = []
    for league in world.plan.leagues:
        clubs: list[dict[str, Any]] = []
        for club in league.clubs:
            players = [
                {
                    "id": str(player.id),
                    "slot": player.slot,
                    "name": player.name,
                    "age": player.age,
                    "position": player.position,
                    "skills": dict(zip(SKILL_NAMES, player.skills, strict=True)),
                    "retirement": asdict(player.retirement),
                    "portrait": asdict(world.portraits[player.id]),
                }
                for player in club.players
            ]
            clubs.append(
                {
                    "id": str(club.id),
                    "name": club.name,
                    "owner": None,
                    "budget": world.plan.options.initial_budget,
                    "stadium_capacity": 5000,
                    "supporters": 2500,
                    "kit_primary": club.kit_primary,
                    "kit_secondary": club.kit_secondary,
                    "tie_break_order": club.tie_break_order,
                    "formation": "4-4-2",
                    "players": players,
                }
            )
        leagues.append({"id": str(league.id), "tier": league.tier, "clubs": clubs})
    return {
        "summary": world_summary(world),
        "leagues": leagues,
        "fixtures": [
            {
                "id": str(f.id),
                "season_id": str(f.season_id),
                "league_id": str(f.league_id),
                "home_club_id": str(f.home_club_id),
                "away_club_id": str(f.away_club_id),
                "round_number": f.round_number,
                "assigned_day": f.kickoff.date().isoformat(),
            }
            for f in world.fixtures
        ],
        "work_status": "held",
        "work": [asdict(job) for job in world.work],
        "database_written": False,
    }


async def _apply(options: WorldOptions, root: Path | None) -> dict[str, Any]:
    database = Database(Settings())
    try:
        async with database.transaction() as session:
            result = await initialize_world(
                session, options, initialized_at=datetime.now(UTC), root=root
            )
        return {
            "league_set_id": str(result.league_set_id),
            "season_id": str(result.season_id),
            "created": result.created,
            "calendar_jobs": "held" if result.created else "unchanged",
        }
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview or initialize a Goaletto league set")
    parser.add_argument("--key", default="initial")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--first-kickoff", required=True, help="First league UTC day (phase 4), ISO 8601"
    )
    parser.add_argument(
        "--budget", type=int, default=INITIAL_CAPITAL, help="Common starting budget, integer units"
    )
    parser.add_argument("--portrait-root", type=Path)
    parser.add_argument("--output", type=Path, help="Write complete offline preview JSON")
    parser.add_argument("--apply", action="store_true", help="Write to configured game DB")
    args = parser.parse_args()
    if args.apply and args.output:
        parser.error("--output is for offline previews only")
    try:
        options = WorldOptions(
            key=args.key,
            seed=args.seed,
            first_kickoff=datetime.fromisoformat(args.first_kickoff),
            initial_budget=args.budget,
        )
    except ValueError:
        parser.error("Use a short slug, valid seed/budget and UTC kickoff")
    try:
        if args.apply:
            result = asyncio.run(_apply(options, args.portrait_root))
        else:
            world = prepare_world(options, root=args.portrait_root)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(
                    json.dumps(
                        preview_document(world),
                        ensure_ascii=False,
                        sort_keys=True,
                        indent=2,
                        default=str,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            result = {**world_summary(world), "database_written": False}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    except Exception as error:
        # Never print exception bodies or SQL which may contain a credential-bearing DSN.
        raise SystemExit(
            f"World operation failed ({type(error).__name__}); "
            "check migration version, kickoff and unused age-compatible portraits."
        ) from None


if __name__ == "__main__":
    main()
