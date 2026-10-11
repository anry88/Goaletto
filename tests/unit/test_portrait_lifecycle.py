import json
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from goaletto.portraits.compositor import asset_root
from goaletto.portraits.whole import (
    load_whole_catalog,
    plan_identity_allocation,
    recipe_for_age,
    recipe_for_variant,
    render_whole,
)

ROOT = asset_root() / "whole-v2"


@pytest.mark.parametrize(
    ("bank", "age", "stage"),
    [
        ("players", 17, "player-youth"),
        ("players", 21, "player-youth"),
        ("players", 22, "player-adult"),
        ("players", 27, "player-adult"),
        ("players", 28, "player-mature"),
        ("players", 31, "player-mature"),
        ("players", 32, "player-veteran"),
        ("players", 38, "player-veteran"),
        ("staff", 32, "staff-early"),
        ("staff", 44, "staff-early"),
        ("staff", 45, "staff-middle"),
        ("staff", 59, "staff-middle"),
        ("staff", 60, "staff-senior"),
        ("staff", 70, "staff-senior"),
    ],
)
def test_age_boundaries_and_same_person_career(bank: str, age: int, stage: str) -> None:
    original = recipe_for_variant("p0001-player18", ROOT)
    selected = recipe_for_age(original, bank=bank, age=age, root=ROOT)
    assert selected.identity_id == original.identity_id
    assert selected.variant_id == f"p0001-{stage}-l1"
    assert recipe_for_age(selected, bank=bank, age=age, root=ROOT) == selected


@pytest.mark.parametrize(
    ("bank", "age"), [("players", 16), ("players", 39), ("staff", 31), ("staff", 71)]
)
def test_missing_age_never_changes_to_another_face(bank: str, age: int) -> None:
    original = recipe_for_variant("p0001-player18", ROOT)
    with pytest.raises(ValueError, match="Missing"):
        recipe_for_age(original, bank=bank, age=age, root=ROOT)


def test_age_selection_rejects_bool_unknown_bank_and_forged_identity() -> None:
    original = recipe_for_variant("p0001-player18", ROOT)
    with pytest.raises(ValueError, match="integer"):
        recipe_for_age(original, bank="players", age=True, root=ROOT)
    with pytest.raises(ValueError, match="bank"):
        recipe_for_age(original, bank="coach", age=52, root=ROOT)
    with pytest.raises(ValueError, match="identity"):
        recipe_for_age(replace(original, identity_id="forged"), bank="staff", age=40, root=ROOT)


def test_base_recipes_remain_historical_and_renderable() -> None:
    original = recipe_for_variant("p0001-player18", ROOT)
    before = render_whole(original, kit_primary="#743D42", kit_secondary="#F2E9D8", root=ROOT)
    older = recipe_for_age(original, bank="staff", age=52, root=ROOT)
    after = render_whole(original, kit_primary="#743D42", kit_secondary="#F2E9D8", root=ROOT)
    assert before.tobytes() == after.tobytes()
    assert older.variant_id != original.variant_id
    assert render_whole(
        older, kit_primary="#315B49", kit_secondary="#E2BF57", size=96, root=ROOT
    ).size == (96, 96)


def test_imported_age_assets_exist_without_creating_more_identity_capacity() -> None:
    catalog = load_whole_catalog(ROOT)
    extension = json.loads((ROOT / "lifecycle-v1/manifest.json").read_text())
    report = json.loads((ROOT / "lifecycle-v1/import-report.json").read_text())
    assert len(catalog["identities"]) == 1080
    assert len(catalog["variants"]) == 1080 + len(extension["variants"])
    assert report["variants"] == len(extension["variants"])
    assert report["identities"] == len(extension["identities"])
    if extension["status"] == "illustrated-lifecycle-complete":
        assert len(catalog["variants"]) == 8320
        assert len(catalog["lifecycle"]) == 1080
        assert not report["missing_atlases"]
    else:
        assert extension["status"] == "partial-generation"
        assert len(extension["variants"]) < report["target_variants"]
        assert report["missing_atlases"]
    assert len(catalog["banks"]["players"]) == 1000
    assert len(catalog["banks"]["staff"]) == 80
    for identity, stages in catalog["lifecycle"].items():
        player = identity.rsplit("/", 1)[-1].startswith("p")
        expected = {s["id"] for s in catalog["age_stages"] if player or s["bank"] == "staff"}
        assert set(stages) <= expected
        if extension["status"] == "illustrated-lifecycle-complete":
            assert set(stages) == expected
        for stage, variant in stages.items():
            entry = catalog["variants"][variant]
            assert entry["identity_id"] == identity and entry["stage"] == stage
            assert (ROOT / entry["image"]).is_file()
            assert (ROOT / entry["garment_mask"]).is_file()
    with pytest.raises(ValueError, match="exhausted"):
        plan_identity_allocation([str(i) for i in range(1001)], "players", "set", root=ROOT)


def test_missing_prepared_stage_is_an_error_not_a_different_person(tmp_path: Path) -> None:
    original = recipe_for_variant("p0001-player18", ROOT)
    base = json.loads((ROOT / "manifest.json").read_text())
    extension = json.loads((ROOT / "lifecycle-v1/manifest.json").read_text())
    del extension["identities"][original.identity_id]["staff-middle"]
    (tmp_path / "manifest.json").write_text(json.dumps(base))
    (tmp_path / "lifecycle-v1").mkdir()
    (tmp_path / "lifecycle-v1/manifest.json").write_text(json.dumps(extension))
    with pytest.raises(ValueError, match="Missing"):
        recipe_for_age(original, bank="staff", age=52, root=tmp_path)


def test_previous_atlas_row_is_not_exported_above_hair() -> None:
    # Real crops whose previous-row torso reached 10-20px into the tile.
    variants = (
        "p0168-staff-early-l1",
        "p0147-staff-early-l1",
        "p0170-staff-early-l1",
        "p0067-staff-early-l1",
        "p0068-staff-early-l1",
        "p0070-staff-early-l1",
        "p0173-staff-early-l1",
        "p0175-staff-early-l1",
        "p0147-player-veteran-l1",
    )
    for variant in variants:
        recipe = recipe_for_variant(variant, ROOT)
        catalog = load_whole_catalog(ROOT)
        with Image.open(ROOT / catalog["variants"][recipe.variant_id]["image"]) as image:
            pigments = sum(
                a >= 192 and (min(r, b) - g > 90 or min(g, b) - r > 90)
                for r, g, b, a in image.crop((0, 0, 256, 48)).get_flattened_data()
            )
            alpha = list(image.getchannel("A").get_flattened_data())
            rows = [sum(a >= 16 for a in alpha[y * 256 : (y + 1) * 256]) for y in range(49)]
            opaque = [sum(a >= 192 for a in alpha[y * 256 : (y + 1) * 256]) for y in range(48)]
            assert not any(
                rows[y] > 64 and rows[y + 1] < rows[y] * 0.5 and opaque[y] < 12.8 for y in range(48)
            ), variant
        assert pigments <= 8, variant


def test_extension_rejects_foreign_identity_or_wrong_version(tmp_path: Path) -> None:
    base = json.loads((ROOT / "manifest.json").read_text())
    (tmp_path / "manifest.json").write_text(json.dumps(base))
    extension_path = tmp_path / "lifecycle-v1/manifest.json"
    extension_path.parent.mkdir()
    extension_path.write_text(json.dumps({"base_catalog_version": 99}))
    with pytest.raises(ValueError, match="different catalog"):
        load_whole_catalog(tmp_path)
    extension_path.write_text(
        json.dumps({"base_catalog_version": 2, "variants": {"bad": {"identity_id": "foreign"}}})
    )
    with pytest.raises(ValueError, match="foreign"):
        load_whole_catalog(tmp_path)
