# Working on Goaletto

- `main` is the common integration branch. Start each new task branch from the
  latest `origin/main`; use the `codex/` prefix by default. After review and
  relevant checks, merge completed work into `main` and publish the updated
  `main` before the next agent wave. Do not keep developing from old agent or
  integration branches. Coordinate shared models, migrations and bot/worker
  wiring with Codex; preserve other agents' uncommitted changes.
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
- Footballers age one game year per season. At rollover, make one persisted draw
  for the upcoming season being their last, with increasing chance at ages 32-38
  and certainty at 38. Warn for that full season, then retire; never reroll on retry.
  Preserve face identity while applying age layers. See docs/player-lifecycle.md.
- The agreed retirement probabilities at ages 32..38 are 5/10/20/35/55/80/100%.
  An announced last season forbids player trades; cancel open listings and release
  reservations idempotently. Check status under lock at listing, bid and settlement.
- New portrait banks are male-only for now. Clothing takes current club primary/
  secondary colors at render time; never change face identity to recolor a kit.
- PostgreSQL owns durable state, accepted commands, scheduled jobs and notification
  outbox. Make ticks, season rollover, club claims and money movements idempotent.
- Do not store game state only in aiogram FSM or process memory.
- Secrets stay in environment variables or host-only secret files. Never print bot
  tokens or credential-bearing database URLs.
- User chose whole illustrated portraits after the part-combination review on 2026-10-11.
  Preserve separate male player/staff banks, stable appearance IDs and rejected pilot IDs.
  Direction C — Dugout stays approved. Export 256px/96px, retain sources separately,
  recolor only garment material masks, preserve person identity across player/staff careers.
- First set: 20 players per club, 1,000 unique player appearances for 50 clubs, no reuse
  across people. A small repeated bank is insufficient. Reserve unique appearance IDs
  durably; exhausted catalog must not silently repeat. Staff last work season is 60-70,
  guaranteed last at 70; exact probabilities are not yet approved.
- The r1 PNG small-part faces were rejected for visual quality on 2026-10-11.
  See docs/portrait-feasibility.md. Preserve them as experiments; do not mass-produce
  or claim accepted art from rendering tests. Whole portraits are the chosen approach;
  individual new illustrations still need quality review. Preserve aspect ratio and clothing masks.
- Staff age and retire; retired players may enter qualified staff pools with the same
  person/history/appearance. See docs/staff-lifecycle.md; do not assume unsettled probabilities.
- Slight likeness between different faces is acceptable per the user's clarification.
  Never reuse an appearance identity between people. Lifecycle variants preserve the
  same identity; see docs/portrait-lifecycle.md. Visual bands do not decide retirement.
- This repository is proprietary; do not change licensing without user instruction.
- Keep Linear tasks aligned with meaningful completed work. Do not close unimplemented
  gameplay tasks merely because scaffolding exists.
- Run ruff, mypy, relevant pytest checks and migration rendering/execution.
  User clarified on 2026-10-12: do not create new local/CI test databases.
  During development, while Goaletto has no real players, use the single
  Goaletto application database for checks (create the main database if absent);
  its development data may be reset.
  Verify the exact Goaletto target before any reset. Never reset another project.
  Once real players/data exist, this development reset authorization no longer
  applies. Never print tokens or credential-bearing database URLs.
