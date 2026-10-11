# Dugout · whole-person age and career variants

An offline extension of whole-v2. The production plan targets 1,000 player
identities with seven illustrated stages each, plus 80 separate staff identities
with three stages each: 7,240 additional variants, not additional people.
Actual progress is recorded in import-report.json; a partial manifest explicitly
uses `partial-generation`. Do not treat the production plan as proof of completion.

| Stage ID | Visual age range | Representative depiction |
| --- | --- | --- |
| player-youth | 17–21 | 19 |
| player-adult | 22–27 | 25 |
| player-mature | 28–31 | 30 |
| player-veteran | 32–38 | 35 |
| staff-early | 32–44 | 40 |
| staff-middle | 45–59 | 52 |
| staff-senior | 60–70 | 66 |

These bounds select an illustration, not hiring eligibility or retirement outcomes.
The game adds one age year per season; a portrait changes only at a stage boundary.
The announced last-season badge is separate text, never burned into the face.

All atlases are identity-preserving edits of the original twenty complete people,
using the built-in Imagegen tool. No CLI/API fallback, modular face assembly,
anatomy stretching, or network call at render time. Original source atlases and IDs
remain unchanged. Slight likeness between different faces was accepted by the user;
different people must still receive distinct appearance identities without reuse.

Sources are retained in sources/, all prompts in prompts/. Production PNGs are
256×256 RGBA with real alpha, clothing masks are grayscale, thumbnails are 96×96.
Gallery WebP exports are already colored burgundy/ivory for inspection. Runtime
uses the technical magenta/cyan PNGs and masks to apply current club colors.

```bash
python scripts/pack_portrait_lifecycle.py
python scripts/review_portrait_lifecycle.py
python scripts/render_whole_portrait.py --variant p0001-player18 \
  --bank staff --age 52 --primary '#315B49' --secondary '#E2BF57' \
  --size 96 --output work/p0001-staff52.png
```

Default import/review refuse an incomplete set. `--allow-partial` is an explicit
development option, not completion certification. Review index.html and the
per-atlas comparison pages: each row represents one person across ages/careers.
Technical audit checks sizes, pixels, alpha, masks, recoloring and missing files;
visual same-person continuity needs review of the illustrated rows.

`recipe_for_age` preserves catalog version and identity, selecting only that
person's matching stage. Missing or out-of-range stages raise an error instead of
borrowing another face. Historical recipes still render their original version.
Career variants are not added to the available staff allocation bank: they cannot
be assigned to a new unrelated employee. Retired identities stay reserved.

This extension does not implement PostgreSQL reservations, people/club seeding,
season aging, retirement, recruitment, Telegram delivery or Telegram file caching.

Current saved batch (2026-10-11): 87 atlases / 1,740 age-career variants.
240 player identities have all seven stages; another 20 have three stages.
The other 760 player identities and all 80 initial staff identities retain their
base portraits, with lifecycle stages still missing. Built-in Imagegen reached its
daily usage limit. 275 atlases remain; generation-checkpoint.json lists them.
Reset reported by tool: 2026-10-12 10:19:38 UTC (12:19 Belgrade).
No scheduled continuation or API generation has been enabled.

Review: all 13 available atlas comparison pages and the highest-distance original/age
pairs were inspected. Previous-row garment/baseline fragments were removed by
whole-tile extraction; faces were not assembled or stretched. The technical audit
checks strong technical pigments and faint horizontal neighbor edges above hair.
These checks do not replace future visual review of newly generated batches.
