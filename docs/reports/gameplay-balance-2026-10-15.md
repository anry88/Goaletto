# Gameplay balance audit (offline, AND-71)

Reproducible offline audit of current formulas and scenarios. No balance
changes, no database, no Telegram, no network. Cup runtime mechanics are
not connected; cup income below is a flagged hypothetical.

- Tool: `and71-balance-audit-v2`, content hash: `78ca508def338e90ff4dd35ad43167c7972e85c1125de583e24826c85042b8a2`
- Commit: `92843f4b6d6d31514841878a72e6387ae0b15edd`
- Policies: economy=economy-cycle-v2, scouting=scouting-v1, training_session=training-specialists-v2, training_schema=training-v1, development_budget=development-budget-v1, match_development=match-development-v1, recovery=recovery-v1, season_sponsor=season-sponsor-v1
- Seeds: [7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38]

## 1. Full-turnover budget (20 cycles / 80 days)

Tier-5 club, 2500 supporters, fixed seasonal sponsor, prize with cycle 18,
expenses before income. `min_pre` is the lowest pre-income cash (liquidity
trough); `shortfall` is its first cycle, if any.

| Scenario | Staff/cycle | Upfront | Min pre-income | Ending (min–max) | Shortfall |
|---|---:|---:|---:|---|---:|
| Спокойный старт | 8,000 | 0 | 547,815 | 758,800–786,660 | — |
| Профильный специалист Q2 | 16,000 | 35,200 | 454,240 | 563,600–591,460 | — |
| Бездействующий скаут Q2 | 9,200 | 5,280 | 541,335 | 729,520–757,380 | — |
| Покупка взрослого Q10/26 | 8,000 | 300,000 | 243,708 | 425,520–453,380 | — |
| Дорогой штаб (главный Q3 + защита Q2) | 34,000 | 149,600 | 35,700 | 89,200–117,060 | — |
| Расширение стадиона до 10K + тренер Q2 | 14,000 | 361,600 | 137,840 | 257,200–285,060 | — |
| Неблагоприятное место/посещаемость | 8,000 | 0 | 547,815 | 646,300–674,160 | — |
| Повышение в tier 4 (место 2) | 8,000 | 0 | 547,815 | 834,800–862,660 | — |
| Гипотетический финал кубка +12.5K (hypothetical) | 8,000 | 0 | 547,815 | 771,300–799,160 | — |

## 2. Scout rejuvenation (skill 5 shown; full cells in JSON)

One active mission; 4/8/12/16 days yield 1/2/3/4 candidates at the end,
no weekly intermediates. The mission fee is paid once in full; each
signing adds its own separate fee and four-day wage.

| Scout | 4d: mission + median signing | 16d: mission + best of 4 | Best quality 16d |
|---|---:|---:|---:|
| 1 | 284,830 | 766,217 | 10.27 |
| 5 | 327,060 | 874,807 | 10.99 |
| 10 | 397,012 | 1,044,567 | 11.77 |
| 15 | 514,637 | 1,226,214 | 12.96 |
| 20 | 585,020 | 1,404,117 | 13.82 |

Median upfront full cost (mission + signing, lower bound): 327,060. Youths vs 600K upfront-only: 1. Optimistic turnover estimate including wages/coach/scout hire/scout upkeep and income: 2 (squad cap 25 fits at most 5 without sales; windows and cash-flow timing still apply). These are estimates,
not proof that rapid rejuvenation is impossible. Four audit seeds are a
small sample; a wider run is required before changing coefficients.

## 3. Development speed (physical/medium; runs in JSON)

Free calendar (~60 sessions) vs busy cup calendar (~28 sessions); only 18
league appearances grant match growth in CURRENT code. This is an implementation
gap: target rules give form/development to all matches, with reduced friendly
development. Busy totals below are not an acceptable target balance verdict.
Values are fractional points; friendly coefficient remains uncalibrated.

| Age | Staff | Free total | Busy total | Match (both) |
|---|---:|---:|---:|---:|
| 18 | 2 | 1.303 (0/0 whole) | 0.807 | 0.373 |
| 18 | 10 | 2.175 (0/0 whole) | 1.346 | 0.621 |
| 18 | 18 | 2.869 (1/0 whole) | 1.883 | 0.869 |
| 21 | 2 | 1.142 (0/0 whole) | 0.707 | 0.326 |
| 21 | 10 | 1.900 (0/0 whole) | 1.176 | 0.544 |
| 21 | 18 | 2.663 (0/0 whole) | 1.649 | 0.761 |
| 26 | 2 | 0.797 (0/0 whole) | 0.493 | 0.227 |
| 26 | 10 | 1.328 (0/0 whole) | 0.822 | 0.380 |
| 26 | 18 | 1.857 (0/0 whole) | 1.150 | 0.531 |
| 30 | 2 | 0.454 (0/0 whole) | 0.281 | 0.130 |
| 30 | 10 | 0.756 (0/0 whole) | 0.468 | 0.216 |
| 30 | 18 | 1.058 (0/0 whole) | 0.655 | 0.302 |
| 34 | 2 | 0.269 (0/0 whole) | 0.167 | 0.077 |
| 34 | 10 | 0.454 (0/0 whole) | 0.281 | 0.130 |
| 34 | 18 | 0.630 (0/0 whole) | 0.390 | 0.180 |

## Limits of this audit

No DB throughput/p95, no worker/ledger races, no HDC infrastructure, no
connected cup mechanics, no real match results (pinned calendar income).
See `assumptions` in the JSON.
