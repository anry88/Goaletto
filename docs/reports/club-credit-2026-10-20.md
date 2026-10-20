# Club credit stress · 2026-10-20

Policy: club-assets-v1. 61800 scenarios, 934496 cycle checks.

Baseline: {'clubs_and_attendance_cases': 100, 'bankrupt': 0, 'minimum_cash': 526679, 'ending_cash_min': 533740}

| Opening | Scenarios | Insolvent | Lowest cash |
|---|---:|---:|---:|
| 0; postseason_player_value_drop=0% | 7500 | 0 | -2486260 |
| 0; postseason_player_value_drop=25% | 7500 | 0 | -2486260 |
| 100; postseason_player_value_drop=0% | 7500 | 7053 | -20364409 |
| 100; postseason_player_value_drop=25% | 7500 | 7500 | -20364409 |
| 50; postseason_player_value_drop=0% | 7500 | 0 | -12571510 |
| 50; postseason_player_value_drop=25% | 7500 | 0 | -12571510 |
| 90; postseason_player_value_drop=0% | 7500 | 2307 | -20301580 |
| 90; postseason_player_value_drop=25% | 7500 | 7490 | -20300671 |
| capital; postseason_player_value_drop=0% | 900 | 0 | -1578260 |
| capital; postseason_player_value_drop=25% | 900 | 0 | -1578260 |

- 50 baseline clubs; 5 stadium levels; 5 academy levels; coach skills 1/10/20; 0/2500 attendance; starting capital or 0/50/90/100% debt.
- Home dates come from the real pure league calendar. No fabricated persisted matches, results or receipts.
- Fixed players/skills/ages and tenth-place prize; no transfer/scout/training growth or cup income. Baseline includes academy upkeep.
- Optional 25% player-asset drop at cycle 19 is an explicit collateral stress, not a simulated retirement result.
- Purchases/bids require free cash. Capital scenarios exclude upgrades above 600K available cash. Debt stress assumes already-owned assets and debt from wages/upkeep, not purchases financed by collateral.
- Insolvency in aggressive scenarios is expected. This report checks arithmetic and baseline survival, not final gameplay balance or HDC capacity.

Content SHA256: 1cc171f98be057cee9dd56193980a9c7de6ce8beae9502b684495c86f42a4495
