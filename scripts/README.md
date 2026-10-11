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
