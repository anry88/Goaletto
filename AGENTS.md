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
  User update 2026-10-14: promotion/relegation happens simultaneously across
  compatible completed seasons, with two clubs each way between adjacent tiers.
  Destination leagues may belong to any set; there is no fixed 5-1 -> 4-1 ladder.
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
- User update 2026-10-13: reuse the existing portrait pool across different people.
  First set keeps 20 players per club and its existing 1,000 illustrations. Keep each
  person's assigned face/history stable; assign new people least-used eligible faces.
  No new image generation is needed. Staff last-season probabilities at ages 60..70
  are approved: 5/8/12/18/25/35/45/60/75/90/100%; certainty at 70.
- Candidate pools are global to the game, not per league/set: target 12 footballer
  offers (free agents + open available auctions), 2 staff per profession. Generate
  only a deficit; sale/hire replenishes at most one. At/above target generate none.
  Natural returns/declined former-player offers may exceed the target: preserve
  people, generate none at/above the target. Never silently qualify a footballer
  as physiotherapist without an explicit qualification.
- User update 2026-10-14 is specified in docs/gameplay-update-2026-10-14.md:
  new market footballers age 26-30, varied quality, age/quality-based prices and
  wages; some have 2-3 adjacent positions and a versatility premium. Fresh staff
  age 50-56 with varied quality; former players retain actual age/history/face.
  Retirement assigns a persisted random staff career, rare high quality, first
  offer to the former club, 20% wage discount there; rejection enters the market.
  A weak head coach starts in each club: one tactic, one instruction change and
  one mentality change per match; substitution limits are equal for all coaches.
  Specialists unlock training; modest match growth depends on minutes, age and
  staff. Scouts offer a private player age 17-21 per four-day game cycle; unsigned discoveries
  never enter the public market. Renew sponsorship after each season using final
  place, supporters and league level. Recalculate starting money and coefficients.
  These are pending gameplay tasks, not claims of implemented functionality.
- Further user update 2026-10-14: docs/calendar-cups-update-2026-10-14.md defines
  18 four-day cycles per season: cup, champions cups, training/friendlies, league.
  Wages and scouts use the four-day cycle, not seven calendar days.
  Latest clarification: managers may schedule training on any UTC day without
  a match for their club, including unused cup/champions days. Friendlies also
  exclude training that day. Training is not restricted to day 3 or once/cycle;
  plans/receipts use club+UTC date. Rebalance daily effects and seasonal growth.
  Time settings use an IANA zone and local preferred kickoff; human-vs-bot uses
  the human preference, human-vs-human the midpoint of the shortest UTC clock arc
  (23:00 and 01:00 -> 00:00), rounded to 30 minutes within the assigned UTC day.
  Against a bot preserve the chosen local time, including fractional UTC offsets.
  Materialize kickoff/jobs only ten days ahead; announced fixtures stay fixed.
  Cup uses 50 clubs with staged entry, two legs until a single neutral final,
  split ticket income, aggregate scores without away goals, extra time/penalties.
  Champions cups qualify top eight per league separately by tier. Calibrate
  durable inter-match energy so an average player/physio recovers in 24 hours;
  adjacent UTC dates do not necessarily provide 24 hours of rest.
- The r1 PNG small-part faces were rejected for visual quality on 2026-10-11.
  See docs/portrait-feasibility.md. Preserve them as experiments; do not mass-produce
  or claim accepted art from rendering tests. Whole portraits are the chosen approach;
  individual new illustrations still need quality review. Preserve aspect ratio and clothing masks.
- Staff age and retire; retired players may enter qualified staff pools with the same
  person/history/appearance. See docs/staff-lifecycle.md and docs/candidate-pools.md.
- Slight likeness between different faces is acceptable per the user's clarification.
  The catalog may be reused across people; lifecycle variants preserve the
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
