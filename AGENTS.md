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
  staff. Scouts can find a private player age 17-21 via an explicit paid assignment,
  with duration-based final reports; unsigned discoveries
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
  User clarification 2026-10-15: day 3 is an optional free window, not a required
  friendly or training. Managers may rest without penalty. No automatic friendly
  matchmaking, invitations or match jobs without an explicit manager request.
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
- Match practice integration 2026-10-15: new league locks pin match-practice-v2,
  persisted terms/staff/seed/participants and atomic form/skills/experience receipts.
  V2 permits bounded -1/0/+1 form; V1 calculations and legacy appearance receipts
  retain their behavior. See docs/match-practice-runtime.md. Cup/champions/friendly
  admission still requires their real runtime; relabeling a league plan grants none.
- Run ruff, mypy, relevant pytest checks and migration rendering/execution.
  User clarified on 2026-10-12: do not create new local/CI test databases.
  During development, while Goaletto has no real players, use the single
  Goaletto application database for checks (create the main database if absent);
  its development data may be reset.
  User reiterated 2026-10-18: during development, freely reset/recreate the real
  Goaletto application DB when useful. Backing up/restoring disposable test data
  is optional, never a prerequisite or an approval gate. Do not add compatibility
  code just to preserve this world. Older agent/check docs requiring a saved world
  describe an obsolete workflow; this instruction takes precedence.
  Verify the exact Goaletto target before any reset. Never reset another project.
  Once real players/data exist, this development reset authorization no longer
  applies. Never print tokens or credential-bearing database URLs.

- User update 2026-10-15: offseason is 8 days / 2 four-day cycles; the full
  turnover is 18 + 2 = 20 game weeks / 80 days. The player-free development world
  may be reset and regenerated; do not build legacy calendar compatibility.
- Scouts must be cheap while idle; explicitly sending one on a mission costs
  substantial money, and signing discoveries also costs substantial money. Balance
  combined costs to prevent cheap rapid squad rejuvenation. No automatic costly
  assignments merely because a scout is employed. Track in AND-105/116/61.

- AND-127 implements durable player_recovery and recovery-v1. Physio skill is
  frozen in SalaryContract.recovery_skill; use employment intervals for recovery,
  never current staff for past time. Freeze participants at lineup lock through
  actual final; finalization, training load and lazy updates are transactional.
  See docs/player-recovery.md. New cup runtimes must settle energy too.
- AND-122 provides /calendar; preserve UTC phase dates and local kickoff display.
  Conditional FixturePlan.possible_club_ids must be cleared for eliminated clubs
  by future tournament logic; unknown opponents never acquire invented times.

- AND-105/107 implement economy-cycle-v2 and market-players-v2: fresh public
  footballers age 26-30, varied quality/caps; saved quotes pin fee, four-day wage
  and policy through settlement. Existing contracts and ledger keep original terms.
  Starting capital remains 600K after the 20-cycle calculation. Scout findings vary
  in price: at age 19, q6/q10/q18 plus a Q2 scout mission cost 225K/400K/1,016K.
  Mission flow is implemented in AND-116; do not treat every discovery as an elite-price player.
  See docs/economy-calculation.md. AND-117 implements season-sponsor-v1;
  preserve fixed contract terms/history and interval-based accrual. See docs/season-sponsorship.md.

- Latest scout clarification 2026-10-15 supersedes automatic weekly findings:
  manager selects target position and 4/8/12/16 elapsed days, pays the search fee,
  then receives one final shortlist at completion. No intermediate weekly reports.
  Longer search gives more choices; initial design is 1/2/3/4 candidates and one
  active search per club. Do not enforce one candidate in the report's cycle
  against a multi-cycle batch. Charge each chosen player's signing separately.
  Findings use individual skills/potential/prices, not three fixed categories.
  docs/scouting.md and AND-116 are authoritative; gameplay flow is implemented.

- AND-108 implements player-positions-v1 for new market/scout people; preserve
  old positions/snapshots and fixed bot youth baselines. Adjacent skills use the
  existing 85% match factor; unfamiliar outfield positions use 60%. See docs/player-positions.md.
- AND-116 implements /scout, one paid active mission per club, final 1/2/3/4
  candidates after 4/8/12/16 days, immutable terms and private ScoutDiscovery rows.
  Always exclude unsigned ScoutDiscovery people from public supply/adoption and
  former-player staff conversion, even after decline/expiry. Signing does not
  replenish the public pool. Report lasts >=8 days and through the next window.
  Future staff dismissal/replacement should call scouting.cancel_invalid; active
  missions stop on lost scout/ownership without refund or extra dismissal fee.

- AND-111/112 implement training-specialists-v2 and staff-session-v1. See docs/training.md.
  AND-129 adds ten-day UTC date selection including conditional tournament plans.
  All future fixture creators/movers must call reserve_match_day under the shared
  allocation lock in the same transaction as their fixture plan: official matches
  cancel pending training with an outbox notice; friendlies reject scheduled/applied training.
  Profile focuses require their specialist at selection and application. Staff snapshots
  pin person/skill/salary contract; rehire never revives an old selected profile.
  Physical gain is split across stamina/pace; head coach does not multiply every
  specialist bonus. Physio owns natural recovery, fitness owns physical drills.
- Shared development-budget-v1 follows PlayerDevelopment.growth_age, never club/owner.
  Use application.development.growth_budget/save_budget and domain.development.grant_growth
  for AND-113 match grants: 30000 total bps, 20000 training, 10000 match,
  15000 per skill per age-season. Preserve fractions/caps on reset; transfers do not reset.
  Leadership has its own fraction and 5000 new bps per age-season. Morale/leadership
  mentoring applies once to attended chosen sessions, not automated bot training.

- AND-113 implements match-development-v1: pin MatchDevelopment at lineup lock;
  apply actual minutes of both teams atomically with finalization and the shared
  age-season budget. Staff is frozen outside the immutable match state. Only
  announced official league plans qualify currently; friendlies, offline/operator
  fixtures, abandoned and old matches without pinned terms do not grant growth.
- User clarification 2026-10-15: all real gameplay matches (league, cup,
  champions, friendlies) must affect form and development by actual participation.
  Friendlies give reduced development versus official games; the multiplier
  is not yet agreed. Shared growth caps/potential still apply. Current league-only
  growth and missing match-form updates are implementation gaps, not design rules.
  Audits must flag these gaps, never approve no-growth cup/friendly balance.
  Track the complete implementation in AND-130 under AND-64; external pure
  practice policy alone does not complete its runtime/persistence/UI acceptance.
- AND-114 assigns retired-staff-v1 once at football retirement, independently of
  candidate deficits. Preserve StaffCareer origin/role/skill and the same face/history.
  First refusal lasts 8 days; decline/timeout/unowned releases to global supply,
  preserving surplus. Origin club wage is floor(normal * 0.8) on every rehire;
  fee is unchanged. Use quoted_wage for club-specific offers and salary receipts.
- AND-115/84 add staff photo cards and club kit images. Reuse existing identities;
  a missing staff age variant falls back only to the person's known base variant.
  card_images.photo_response sends outside transactions and reuses the existing
  durable file cache; kit graphics use the club:<UUID>/club-kit-v3 namespace.
  External pure-module briefs are in docs/agent-prompts; shared runtime/schema
  integration stays with Codex and DB reset checks must never run concurrently.

- User update 2026-10-15: head coaches cannot be dismissed without a replacement.
  Keep the atomic hire-and-replace flow; reject legacy dismissal confirmations too.

- Accepted pure modules for AND-119/123/125: domain.league_allocation.allocate,
  domain.cup_bracket.build_bracket/progression, domain.neutral_final.settle_neutral_final.
  See docs/tournament-domain.md for contracts and remaining durable integration.
  Cup winner mappings must include decided ancestors; all readers validate them.
  These calculators do not activate cross-set rollover, cup jobs or ledger payouts.
  Keep parents open until persisted runtime/DB/UI acceptance is complete.
- Accepted external AND-124/126/71 subpackages: domain.knockout,
  domain.champions_groups and scripts/audit_gameplay_balance.py.
  See docs/agent-wave6-review.md; shared runtime/DB wiring remains unfinished.
- Accepted AND-130 match_practice (proposal coefficients) and AND-126
  champions_bracket; see docs/agent-wave7-review.md. Do not confuse accepted pure
  modules with completed game wiring. Preserve match-development-v1 receipts;
  zero-minute practice must preserve every fraction, including capped skills.

- AND-123/124/125 now wire the ordinary cup end to end; see docs/tournament-runtime.md.
  New worlds/seasons persist frozen entries, 49 ties and 97 plans alongside 450 league
  plans. Legacy seasons without a bracket stay untouched. Rollover with a bracket
  requires all cup results and decided ties; league standings remain league-only.
  V4 alone permits real ET ticks 31..40 and one durable job per penalty. Preserve
  V1/V2/V3 replay hashes, original coach/substitution limits and recovery windows.
  Neutral finals pin an existing third-club arena and split tickets atomically;
  no home advantage or arena-owner income. League and cup practice use actual
  0..120 minutes once; penalties add no minutes/goals. Champions/friendly runtime
  and completion of AND-130 remain separate work.

- Agent intake 2026-10-17 accepts AND-62/128 and champions runtime AND-126;
  see docs/agent-wave8-review.md, docs/academy-runtime.md, docs/friendly-runtime.md
  and docs/champions-runtime.md. Academy intakes/signing are offseason-only;
  freeze level/limit once per club+season, hide numeric caps, graduates sign free.
  Unsigned academy graduates enter public supply once at season.open. Upkeep is
  prorated using historical upgrade ledger timestamps. Bot retirement youth stays separate.
  Friendlies require an explicit accepted booking; no automatic creation. Pin both
  ownership periods on invitations; expiry is 00:00 UTC of the assigned match day.
  Friendlies use 50% skill/experience as a prototype coefficient, normal form,
  injury/recovery and home ticket rules. Preserve immutable history and league FK.
- Cross-set rollover now atomically transitions every compatible season;
  one allocation receipt, destination person calendars, sponsorship and tournament
  qualification commit together. Champions eligibility uses past tier/season;
  guest SeasonContestant rows do not replace true SeasonClub membership.
  Shared tournament hosts are visible to guest calendars and block every cohort
  member's rollover until all results finish. AND-119 now waits for every latest
  compatible season (equal UTC epoch/end), including delayed and preparing sets;
  only incompatible operator/dev calendars stay separate.

- AND-47/119 align new sets with the current common calendar and season number.
  Before admission, durable world.catch_up batches execute actual past bot jobs;
  PostgreSQL forbids ownership of a pending set and replay of a previously owned set.
  Never replay an existing human world or invent previous seasons/results.
  User update 2026-10-18: keep a ready tier-5 reserve, refill at <=10 to up to 20
  through world.reserve; startup queues it when the game is enabled. Entry/claim
  only enqueue preparation, never generate/simulate synchronously. Finish reserve
  preparation before traffic; see docs/world-reserve.md. Use chronological season
  reads after cross-set moves. entry_opened receipts dedupe /start independently of analytics.

- AND-75/76/79 launch measurement is wired; see docs/launch-measurement.md.
  Direct help/offer confirmations use operation/effect 31001 and a bounded target,
  only after Telegram accepts IO. Rejections and delivery never renew ownership
  or sessions. Saved-lineup Update retries are not stale-button errors.
  Recovery requires claim/manual save within 30 minutes and the same ownership.
  SQL opportunity reports use full league plans, actual times, mature windows,
  historical coverage, and reduced second denominator; do not infer churn from
  calendar waiting. HDC deployment remains separate;
  these reports do not launch polling or outreach.

- AND-78 automatic feedback is internal-only and off by default. Durable
  feedback.scan pins coverage, checks incomplete entry at 24h and mature missed
  league opportunities, and enqueues secondary offers below game-message priority.
  Recheck before send, suppress stale/expired/opted-out/unavailable cases, never
  resend an unknown automatic send. /feedback resumes its existing response.
  Internal first-entry time and action fixture scopes are operational facts,
  not public analytics events. Preserve manager/campaign dedupe and >=7d cooldown.
  See docs/feedback.md; migration 0042 forbids dropping used trigger/scope history.
- AND-82 local cleanup/export/feedback gate is documented in docs/analytics/launch-gate.md.
  analytics_cleanup_cli is preview-only unless --apply and uses persisted retention.
  Grafana parity and actual HDC role permissions remain open; a local READ ONLY
  transaction or PUBLIC ACL check does not certify deployment or external outreach.

- AND-77 records operational RuntimeRequest attempts for match/live/history,
  with actual observed commit, render and Telegram intent/outcome. Retry gets a
  new attempt of the same operation/command. Unknown sends/commits stay unknown;
  observation failure is bounded and never cancels gameplay. Outbox enqueue and
  per-attempt availability/render timestamps have no historical backfill.
  Read-only runtime_observation_cli reports p50/p95; tick waiting and historical
  world.catch_up are excluded from technical live latency. See docs/runtime-observation.md.
- AND-69 adds periodic runtime.reconcile (50 live fixtures per 30-second batch).
  Only restore a missing immediate continuation proven by immutable tick/stage
  hash and matching projections/clock. Normal processing restores a late decision
  window before any tick. Never reset failed/done/held jobs, replay an admitted
  human world, mutate a checkpoint, or infer money/results. Skip paused/inactive/
  preparing worlds. HDC reboot/DB-disconnect drill remains open under AND-69/72.

- AND-70 worker health uses migration 0043 and per-boot worker_heartbeats for
  jobs/sender separately. Pulse only after committed iterations or intentional
  sender backoff waits; never maintain health with an independent timer while
  processing is stuck. Health CLI reads this process's private boot file and DB,
  never another replica's success. Monitoring writes are bounded and do not undo
  game commits. See docs/worker-health.md. Docker unhealthy alone does not restart
  a live container; HDC alerts/backup/reboot acceptance remain separate.
- AND-71 bounded load profile is tests/integration/test_launch_load.py;
  docs/launch-load.md records 50/75-match real-clock first-tick measurements.
  Synthetic past jobs are held, not invented as results. Separate committed-tick
  latency from deferred attempt percentiles, and fake Telegram time from real IO.
  Use the single real player-free application DB, serially; reset/recreate freely.
  Local timings do not establish HDC capacity or full-season balance.

- AND-80/82 operator dashboards use migration 0044 aggregate-only
  goaletto_operator.report and deploy/grafana provisioning. Frozen report SQL
  changes require a new migration and parity checks against CLI. Never grant
  the datasource raw tables/comments or the game login. Use a separate private
  Grafana organization: folder ACL alone does not isolate an OSS datasource.
  Journey filters precede aggregation; tier/set refer to first-claim historical
  membership and exclude unclaimed managers. Operational panels are unfiltered;
  absent coverage/observations never become successful zeroes. See
  docs/analytics/grafana.md. Local real-login/Grafana acceptance does not certify
  HDC organization membership, actual production grants or external outreach.
