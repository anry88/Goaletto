# Dugout · whole-person age and career variants

The complete extension contains **362 source atlases / 7,240 additional variants**:
1,000 player identities with seven stages each and 80 original staff identities
with three stages each. Together with the 1,080 original portraits, the runtime
catalog has **8,320 variants**. These are stages of existing faces, not new people.
Actual import/technical audit results live in manifest.json, import-report.json
and quality-report.json; the complete status is illustrated-lifecycle-complete.

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
The last-season badge stays separate text. All current banks are male-only.

All atlases are identity-preserving edits of the original twenty complete people,
using one built-in Imagegen call per atlas. Original source atlases, appearance IDs
and historical variants remain unchanged. There is no API/CLI fallback, modular
face assembly, anatomy stretching or network call at render time. Per the user
instruction of 2026-10-13, base faces may be reused across different people; every
person keeps their assigned identity and independent history. Career stages of a
player are not new base staff identities available for unrelated hires.

Sources are retained in sources/, prompts in prompts/. Production PNGs are
256×256 RGBA with real alpha, clothing masks are grayscale, thumbnails are 96×96.
Gallery exports use burgundy/ivory for inspection. Runtime recolors only the
technical magenta/cyan garment material to match the current club.

```bash
python scripts/pack_portrait_lifecycle.py
python scripts/review_portrait_lifecycle.py
python scripts/render_whole_portrait.py --variant p0001-player18 \
  --bank staff --age 52 --primary '#315B49' --secondary '#E2BF57' \
  --size 96 --output work/p0001-staff52.png
```

Import/review run without --allow-partial for completion acceptance. Inspect
index.html and per-atlas comparisons: each row is one person across ages/careers.
Technical checks cover count, sizes, alpha, hashes, missing files, recoloring and
pixels outside the clothing mask. Similarity ranking is only a review aid; it
cannot certify visual identity. No random fallback to a different face is allowed.
Historical recipes render their pinned original variant.

## Production history

- 2026-10-12/13: 289 atlases / 5,780 variants, then a native generation quota limit.
- First batch 2026-10-22: 19 atlases / 380 variants, covering the 22 missing current
  portraits found in human-owned production squads. Total 308/362 atlases.
- Completion 2026-10-22: the remaining 54 atlases / 1,080 variants, following only
  missing saved prompts and original references. generation-completion-2026-10-22.json
  records original/prompt/generated-source SHA-256 and native output filenames.

The original base bank and all previously published lifecycle records stay intact.
The importer removes neighboring jersey fragments at atlas gutters while retaining
whole face/neck/clothing proportions. The p0487/p0489 staff regressions remain in
portrait tests. Gameplay aging, staff conversion, allocation and Telegram caching
are implemented separately; this offline extension never changes game history.
