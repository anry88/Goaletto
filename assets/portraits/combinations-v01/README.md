# Nine real modular assemblies · review v01

These are actual Python/Pillow combinations of isolated head, eyes, nose, mouth,
hair and clothing parts. No complete portrait master is used. No Imagegen or network
calls happen during rendering. The source is the existing player atlas r1; its rejected
original import and stable IDs are preserved unchanged.

- `combinations-sheet.png`: 3 source heads × 3 hairstyles, with alternating eyes,
  noses, mouths and collars. All nine share the same kit colors to expose face differences.
- `assembly-steps.png`: cumulative six-layer assembly of example02.
- `telegram-96px.png`: the same nine outputs at actual96px portrait resolution.
- `player-01..09.png` and `recipes/`: individual512px outputs and exact part IDs.
- `review.json`: source, placement, limitations and review status.

Reproduce from repository root:

```bash
.venv/bin/python scripts/check_portrait_combinations.py
```

Uses the B experiment's proportional scaling, lower-neck trim and larger mouth,
with existing skin-edge adaptation for eyes/lips on darker heads. It does not replace
the production renderer. No new source illustrations were generated for this review.

Observations: all combinations render, neck/collar contact is better than original r1;
the eyes/nose/lips and underlying head contours remain quite similar. Shape and skin
tone are coupled in the three source heads, and eye/lip skin-edge coloring is heuristic.
Some seams, oversized lips and hair/temple overlaps remain visible at512px. These
are nine recipes, not proof of nine sufficiently distinct approved identities.
User review is pending; staff, aging and all possible combinations are untested.
