import json
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from goaletto.portraits.compositor import asset_root
from goaletto.portraits.whole import (
    career_variant,
    load_whole_catalog,
    make_whole_recipe,
    plan_identity_allocation,
    recipe_for_variant,
    render_whole,
)

PILOT = asset_root() / "whole-v1"


@pytest.fixture(autouse=True)
def use_experimental_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("goaletto.portraits.whole.whole_root", lambda: PILOT)


def test_first_wave_banks_and_persisted_recipe() -> None:
    catalog = load_whole_catalog()
    assert len(catalog["banks"]["players"]) == 24
    assert len(catalog["banks"]["staff"]) == 12
    assert len(catalog["identities"]) == 36
    recipe = make_whole_recipe("person-001", "players")
    assert recipe == make_whole_recipe("person-001", "players")
    assert json.loads(recipe.to_json())["variant_id"] == recipe.variant_id


def test_career_transition_preserves_identity_and_rejects_foreign_face() -> None:
    player = recipe_for_variant("p01-player24")
    staff = career_variant(player, "p01-staff42")
    assert staff.identity_id == player.identity_id
    assert staff.variant_id != player.variant_id
    with pytest.raises(ValueError, match="identity"):
        career_variant(player, "s01-staff46")
    with pytest.raises(ValueError):
        career_variant(replace(player, identity_id="forged"), "p01-staff42")


def test_unique_allocation_and_catalog_capacity_guard() -> None:
    people = [f"person-{index:02}" for index in range(20)]
    assignments = plan_identity_allocation(people, "players", "set-1")
    assert len({recipe.identity_id for recipe in assignments.values()}) == 20
    assert assignments == plan_identity_allocation(list(reversed(people)), "players", "set-1")
    reserved = {recipe.identity_id for recipe in assignments.values()}
    remainder = plan_identity_allocation(["new"], "players", "set-1", reserved=reserved)
    assert remainder["new"].identity_id not in reserved
    with pytest.raises(ValueError, match="exhausted"):
        plan_identity_allocation([str(i) for i in range(10000)], "players", "set-1")
    with pytest.raises(ValueError, match="unique"):
        plan_identity_allocation(["same", "same"], "players", "set-1")


def test_every_illustration_preserves_face_when_kit_changes() -> None:
    catalog = load_whole_catalog()
    for variant_id, entry in catalog["variants"].items():
        recipe = recipe_for_variant(variant_id)
        a = render_whole(recipe, kit_primary="#743D42", kit_secondary="#F2E9D8")
        b = render_whole(recipe, kit_primary="#2B435C", kit_secondary="#DFA642")
        assert a.mode == "RGBA" and a.size == (256, 256)
        assert a.getchannel("A").tobytes() == b.getchannel("A").tobytes()
        # Material mask excludes the whole upper half, including eyes and hair.
        assert a.crop((0, 0, 256, 128)).tobytes() == b.crop((0, 0, 256, 128)).tobytes()
        with Image.open(PILOT / entry["garment_mask"]) as mask:
            outside = mask.point(lambda value: 255 if value == 0 else 0)
            assert Image.composite(a, b, outside).tobytes() == b.tobytes()
        assert a.tobytes() != b.tobytes(), variant_id
        assert render_whole(
            recipe, kit_primary="#743D42", kit_secondary="#F2E9D8", size=96
        ).size == (96, 96)


def test_renderer_rejects_invalid_recipe_size_color_and_asset_escape(tmp_path: Path) -> None:
    recipe = recipe_for_variant("p01-player24")
    kwargs = {"kit_primary": "#743D42", "kit_secondary": "#F2E9D8"}
    with pytest.raises(ValueError):
        render_whole(replace(recipe, catalog_version=99), **kwargs)
    with pytest.raises(ValueError):
        render_whole(replace(recipe, identity_id="different"), **kwargs)
    with pytest.raises(ValueError):
        render_whole(recipe, size=512, **kwargs)
    with pytest.raises(ValueError):
        render_whole(recipe, kit_primary="red", kit_secondary="#F2E9D8")
    catalog = load_whole_catalog()
    catalog["variants"][recipe.variant_id]["image"] = "../escape.png"
    (tmp_path / "manifest.json").write_text(json.dumps(catalog))
    with pytest.raises(ValueError, match="local PNG"):
        render_whole(recipe, root=tmp_path, **kwargs)
