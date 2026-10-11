# Dugout illustrated modular pilot

Real local Python/Pillow compositor, separate male players/staff banks.
32 genuine transparent parts in components-v1/, stable IDs in manifest-v1.json.
Source atlases were generated as isolated parts by built-in Imagegen, not extracted
from finished concept portraits. Full prompts and originals are kept here.

players-atlas-v01.png is the initial source; v02 fixes opaque nose backing and
duplicated eyebrows in age overlays. The importer uses v02 plus staff-atlas-v01.png.
Each generated atlas uses alpha; the apparent color in transparent pixels is ignored.
Magenta/cyan clothing is a technical primary/secondary material mask.

Import: python scripts/pack_raster_portraits.py
Review output: python scripts/render_raster_portrait_preview.py
CLI: python -m goaletto.portraits.raster_cli --help
Documentation: ../../../docs/portrait-raster-generator.md

Status: functional illustrated prototype, not a production art catalog.
Keep the namespace/paths immutable when a new asset version is made. Do not update
this version in place with incompatible atlas geometry. Existing SVG IDs remain intact.
Runtime uses only local files, current age and club kit colors; no AI service.
