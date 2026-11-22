# Development tools

render_portrait_preview.py generates six sample portraits and a gallery from the
small SVG component pilot. It does not edit or derive layers from the AI concept sheets.
More seed/balance tools belong here as the corresponding Linear work is implemented.

pack_raster_portraits.py imports purpose-made transparent part atlases into versioned
512px PNG layers for separate male players/staff banks. It does not slice finished faces.
render_raster_portrait_preview.py uses the working local compositor to build 12 portraits
and an identity/age/kit matrix; no network or Imagegen calls happen in these scripts.
The PNG CLI is python -m goaletto.portraits.raster_cli; see docs/portrait-raster-generator.md.

The r1 PNG pilot was rejected visually. check_portrait_feasibility.py compares it
with proportionally scaled parts and an intact master, verifies kit recoloring,
and displays offline24/35 age variants. See docs/portrait-feasibility.md for limits.

check_portrait_combinations.py extends the proportional B experiment to nine real
part combinations, with exact recipes, cumulative layer breakdown and96px contact
sheet. It uses existing atlases without network calls or new whole-face generation.
Review output: assets/portraits/combinations-v01/. Not a production renderer replacement.

Whole portraits are the chosen approach. pack_whole_portraits.py reproduces the v1
24/12 individual-image experiment (too much identity similarity from one face reference).
pack_portrait_atlases.py imports complete-person v2 atlases, checks alpha/count/exact
duplicates, makes256px portraits/masks,96px thumbnails and contact sheets.
render_whole_portrait.py renders a pinned variant locally; no image-generation calls.
plan_identity_allocation is a capacity-guarded preview planner; PG uniqueness/locks
and game assignment remain separate work. Target 50 × 20 players, no repeated identities.

review_whole_portraits.py audits all 1,080 exports in two kit colors, ranks nearby face
shape hashes for human review and builds a static offline HTML gallery (no JavaScript).
Shape similarity scores are a review aid, not proof that identities are visually distinct.

pack_portrait_lifecycle.py imports identity-preserving whole-person age/career atlases.
The default requires every planned atlas; --allow-partial is for development only.
review_portrait_lifecycle.py checks all age exports, generates per-atlas comparison
pages, clothing/alpha checks and continuity review pairs. Gallery previews use normal
Dugout colors; technical magenta/cyan remains in renderer inputs. See
docs/portrait-lifecycle.md. render_whole_portrait.py accepts --age together with --bank
to select a prepared stage of the same person; missing stages never borrow another face.

`simulate_ten_seasons.py` / `run_calibration_local.py`: десять сезонов на единственной
локальной Goaletto development DB, реальные сервисы и ускоренный движок, семь
стратегий и стресс пределов роста. Сбрасывает disposable локальный мир, не production;
не создаёт новую БД и не запускает Telegram. [Методика и команды](../docs/ten-season-calibration.md).
