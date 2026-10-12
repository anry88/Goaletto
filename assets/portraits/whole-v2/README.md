# Whole-person library v2 · generation and quality review

Imported: 1,000 male player portraits, 50 clubs × 20 players, plus 80 staff
portrait assets. The staff image count is capacity; the actual role/initial hiring
pool size is not yet an approved gameplay rule. No repeating an appearance between
different people, even after a person's retirement. A former player's staff/age
variant belongs to the same identity, not another random staff face.

Each source atlas is a generated asset containing 20 **complete coherent people**
in a 5×4 grid. Cropping whole portraits does not assemble separate eyes/noses/mouths.
Each atlas has a separate built-in Imagegen call and saved prompt; no CLI/API fallback.
Player atlas01 was generated without one face reference, then simplified by an edit.
It is the style/grid reference for subsequent atlases. `production-plan.json` holds
intended stable identities and depicted ages; it is not durable gameplay state.

Runtime/exports: 256×256 RGBA portraits + explicit technical-pigment clothing masks;
96×96 thumbnails, proportional scaling only, actual alpha preserved. Current club
colors recolor the garment; no image-generation calls happen in local rendering.
Original source depictions stay pinned. lifecycle-v1 adds age/career stages of the same people; its manifest and import-report record actual coverage. The extension is currently partial (Imagegen daily limit).

```bash
python scripts/pack_portrait_atlases.py --require-complete
python scripts/review_whole_portraits.py
```

Sources/prompts remain here, images in portraits/, masks in masks/, contact sheets
in previews/. `manifest.json` pins identity/variant/source crop/age/version. Import
checks count, alpha, sprite bounds, material mask and exact pixel duplicates. Those
checks do not prove that every face is visually distinct; review sheets and near
duplicate review are required before game assignment. `import-report.json` reports
actual imported count and missing sources. All 54 sources are present. Pixel-exact
duplicates are absent; slight likeness is accepted by the user. Similarity-ranked
pairs are an inspection aid, not a demand to redraw close silhouettes. Numerical uniqueness does not certify distinct identity.

Review [all portraits](index.html), [player sample](previews/players-sample.png),
[staff sample](previews/staff-sample.png), [kit colors](previews/kit-colors.png), and
[24 nearest shape pairs](previews/closest-pairs.png). `quality-report.json` checks every
portrait in two kit colors, preserving alpha and every pixel outside its garment mask.
The importer trims atlas gutters below the broad shoulder baseline and retains the
main connected figure; it never changes face proportions. Masks keep only technical
pigment regions connected to the lower garment, bridging narrow black seams.

`plan_identity_allocation` plans without replacement and refuses an exhausted bank.
PG reservation uniqueness, locks and persistent assignment are implemented in
the game foundation/world initializer (AND-45/46); this offline import does not run them.
On retry, look up existing person assignments first; plan only newly unassigned people.
Retired identities remain reserved permanently. A second league set and academy
replacements need more portraits: the first 1,000 cover exactly the starting players.
No real clubs/players, retirement/pools or Telegram file cache are created by this import.

Age/career expansion: [lifecycle-v1](lifecycle-v1/README.md),
[comparison gallery](lifecycle-v1/index.html). 5,780 additional variants are saved:
780 players with seven stages, 20 with four football stages, and all 80 initial
staff with three staff stages. 73 source atlases / 1,460 variants remain after the
built-in tool reached its usage limit on 2026-10-12. The tool reported a reset at
2026-10-13 11:57:51 UTC (13:57 Belgrade). See lifecycle-v1/generation-checkpoint.json
for the exact missing sources; no continuation has been scheduled.
