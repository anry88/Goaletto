# Goaletto portrait quality feasibility experiment

The user rejected the r1 small-parts compositor visually on 2026-10-11.
This folder compares rejected assembly A, uniformly scaled parts B, and an intact
illustrated master C. A/B share a recipe; C references the approved P01 style/identity.
It is not an identical-identity comparison across A/B/C.

Built-in Imagegen files and exact adjacent prompts:
- player-master-v01.png: initial coherent24-year-old master with material colors.
- player-master-v02.png: same portrait with shorter exposed neck, used by this trial.
- player-age35-v01.png: offline age35 variant retaining the same identity.

Magenta/cyan is technical clothing color. Python converts it to two club colors
inside the measured garment ROI; no network or AI call happens during rendering.
This specific ROI is not a universal semantic mask for future masters.

Reproduce from repo root: python scripts/check_portrait_feasibility.py
Outputs: comparison-v01.png, comparison-1/2/3.png and96px samples,
whole-kit-v01.png with3 club kits, whole-age-v01.png with24/35 comparison.
experiment.json records the prototype parameters and actual invariant checks.

Status: one identity/two ages, awaiting visual review; not a production library,
not a staff bank, no claim of unique generated faces for every new player.
Full analysis: ../../../docs/portrait-feasibility.md.
