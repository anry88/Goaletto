# Whole portrait experiment v1

24 player and 12 staff portraits plus a linked p01 age 42 staff variant, generated as
individual complete illustrations with built-in Imagegen. Prompts and true-alpha
sources retained; Python exports 256px PNG and 96px thumbnails, explicit garment masks.

**Not a production unique-person library.** On the full sheet, many portraits
resemble the same style-reference person too closely. The user requires 1,000 unique
player faces for 50 clubs × 20 players, with unique staff too. This experiment demonstrates
whole anatomy, local kit recoloring and a career identity-preservation edit. It does
not satisfy the first-wave count or uniqueness, or provide full aging stages.

New whole-person atlas library is developed separately in `../whole-v2/`.
The v1 namespace and assets are preserved. Numerical rendering checks are not art approval.

Reproduce: `python scripts/pack_whole_portraits.py --require-complete`.
Review: previews/players.png, staff.png, player-to-staff.png and 96px sheets.
