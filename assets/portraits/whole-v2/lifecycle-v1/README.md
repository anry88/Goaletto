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

Current saved batch (2026-10-12): 289 atlases / 5,780 age-career variants.
This session added 202 atlases / 4,040 variants to the previous 87 atlases.
780 player identities (p0001..p0780) have all seven stages; another 20
(p0781..p0800) have all four football stages but still lack their staff stages.
All 80 initial staff identities have all three staff stages. The other 200 player
identities retain their base portraits, with lifecycle stages still missing.
Built-in Imagegen reached usage_limit_reached. 73 atlases / 1,460 variants remain;
generation-checkpoint.json lists them. Resume with players-40-staff-early, then
players-40-staff-middle/senior, then all seven stages for players-41..50.
Reset reported by tool: 2026-10-13 11:57:51 UTC (13:57 Belgrade).
No scheduled continuation or API generation has been enabled.

Review: all 44 available atlas comparison pages have been inspected (40 player
atlases and four staff atlases). No obvious identity swaps were found; similarity
ranking remains a review aid, not proof of same-person continuity. The technical
audit passed for all 5,780 available variants: zero exact pixel duplicates, valid
alpha/masks, preserved pixels outside clothing, and no neighbor garment/baseline
fragments above hair. These checks do not certify the missing part of the plan.

The importer now removes a sloping neighboring jersey hem that previously escaped
the gutter boundary check. The p0487-staff-early-l1 and p0489-staff-senior-l1 crops from this session
are regression fixtures for this case. Original source atlases, base portraits
and appearance identities remain unchanged; lifecycle exports were reimported.
Ruff check/format and mypy passed; 213 non-integration tests passed, two integration
tests were deselected. The final expanded catalog also passed all 24 portrait
lifecycle tests. No database was used for these checks.

## Дополнение 2026-10-22

Встроенным Imagegen продолжены сохранённые identity-preserving prompts исходных
листов. Добавлены 19 листов / 380 вариантов: все четыре футбольных возраста
players-41, adult/mature для42, youth для43, adult для44, adult/veteran/youth
для45, youth для46, adult/veteran для47, adult/mature/veteran для48, veteran
для49 и adult для50. Это приоритетный набор по read-only аудиту HDC:22 действующих
футболиста в клубах менеджеров не имели иллюстрации своего текущего диапазона.
Лица, person IDs и исторические рецепты не переназначаются.

Итог:308 из362 листов /6160 из7240 дополнительных возрастных и карьерных
вариантов. Каталог остаётся `partial-generation`;54 листа ещё отсутствуют
(21 футбольный и33 кадровых). Точный остаток — `generation-checkpoint.json`.
Историческая ошибка квоты от13 октября больше не описывает состояние генерации.
Исходники и исходные prompts сохранены; runtime использует локальные PNG256px,
маски материала формы и миниатюры96px. Новые иллюстрации просмотрены рядом с
исходными двадцатью лицами каждого листа; технический аудит проверяется отдельно.
