# Working on Goaletto

- Read README.md, docs/game-rules.md and the relevant docs before changing behavior.
- User instructions override older GDD examples. Current rules are in the root GDD v2,
  docs/game-rules.md and docs/match-clock.md; docs/reference/original-gdd-v1.md is archival.
- Python only for application code. No JavaScript, Mini App, public frontend or new
  web API unless the user explicitly changes that scope.
- Keep domain simulation pure: no aiogram, SQLAlchemy, network calls, wall-clock reads
  or unseeded random generators in domain modules.
- Match ticks are 60 seconds. Regulation: 30 ticks, 3 game minutes each, 3-minute
  halftime. Wall-clock duration is 33 minutes.
- Each league set has 5 leagues of exactly 10 clubs. New managers enter only tier 5.
  Promotion/relegation happens simultaneously, within a set, with two clubs each way.
  Tier 1 has no promotion; tier 5 has no relegation; no playoffs.
- Unowned clubs use comparable baseline squads, fixed lineups, no training or AI/LLM
  integration. Automatic substitutions are injury-only; retirements receive youth
  replacements in the offseason. See docs/bot-clubs.md.
- Ownership expires after six calendar months without an explicit game action.
  Periodic reminders and automated matches never extend it. Reclaim is tier-5 only.
- PostgreSQL owns durable state, accepted commands, scheduled jobs and notification
  outbox. Make ticks, season rollover, club claims and money movements idempotent.
- Do not store game state only in aiogram FSM or process memory.
- Secrets stay in environment variables or host-only secret files. Never print bot
  tokens or credential-bearing database URLs.
- Art is a small approval pilot. Preserve separate player/staff banks and stable
  component IDs. Direction C — Dugout was approved on 2026-10-11; retain a small technical pilot
  and review illustrated component quality before mass production.
- This repository is proprietary; do not change licensing without user instruction.
- Keep Linear tasks aligned with meaningful completed work. Do not close unimplemented
  gameplay tasks merely because scaffolding exists.
- Run ruff, mypy and relevant pytest checks. PostgreSQL integration checks use an
  isolated GOALETTO_TEST_DATABASE_URL, never a production database.
