from dataclasses import replace

import pytest
from PIL import Image

from goaletto.portraits.raster import (
    age_stage,
    load_catalog,
    make_raster_recipe,
    recipe_from_json,
    render_raster,
    tint_kit,
)


def draw(seed: str, bank: str = "players", age: int = 24) -> Image.Image:
    return render_raster(
        make_raster_recipe(seed, bank),
        age=age,
        kit_primary="#743D42",
        kit_secondary="#F2E9D8",
    )


def test_saved_identity_roundtrip_and_repeatable_pixels() -> None:
    recipe = make_raster_recipe("footballer-01", "players")
    assert recipe == make_raster_recipe("footballer-01", "players")
    assert recipe == recipe_from_json(recipe.to_json())
    assert draw("footballer-01").tobytes() == draw("footballer-01").tobytes()
    assert recipe != make_raster_recipe("footballer-02", "players")


def test_kit_color_change_leaves_identity_and_face_unchanged() -> None:
    recipe = make_raster_recipe("footballer-01", "players")
    before_recipe = recipe.to_json()
    before = draw("footballer-01")
    after = render_raster(recipe, age=24, kit_primary="#6B755D", kit_secondary="#2B435C")
    assert recipe.to_json() == before_recipe
    assert before.crop((0, 0, 512, 386)).tobytes() == after.crop((0, 0, 512, 386)).tobytes()
    assert before.crop((0, 386, 512, 512)).tobytes() != after.crop((0, 386, 512, 512)).tobytes()


def test_tint_materials_preserves_alpha_and_neutral_ink() -> None:
    sprite = Image.new("RGBA", (4, 1))
    sprite.putdata([(255, 0, 255, 254), (0, 255, 255, 100), (20, 20, 20, 255), (0, 0, 0, 0)])
    result = tint_kit(sprite, "#743D42", "#6B755D")
    assert list(result.get_flattened_data()) == [
        (116, 61, 66, 254),
        (107, 117, 93, 100),
        (20, 20, 20, 255),
        (0, 0, 0, 0),
    ]


def test_age_layers_change_only_age_region_without_new_identity() -> None:
    young, mature = draw("footballer-01", age=20), draw("footballer-01", age=35)
    assert young.tobytes() != mature.tobytes()
    assert young.crop((0, 347, 512, 512)).tobytes() == mature.crop((0, 347, 512, 512)).tobytes()
    assert age_stage("players", 27) == "none"
    assert age_stage("players", 28) == "subtle"
    assert age_stage("players", 32) == "mature"
    assert age_stage("staff", 40) == "none"
    assert age_stage("staff", 60) == "mature"


def test_near_transparent_rgb_noise_is_not_recolored_as_kit() -> None:
    sprite = Image.new("RGBA", (2, 1))
    pixels = [(145, 72, 109, 7), (0, 255, 255, 255)]
    sprite.putdata(pixels)
    result = tint_kit(sprite, "#743D42", "#6B755D")
    assert result.getpixel((0, 0)) == pixels[0]
    assert result.getpixel((1, 0)) == (107, 117, 93, 255)


def test_semantic_material_mask_protects_non_clothing_with_same_color() -> None:
    sprite = Image.new("RGBA", (1, 2), (0, 255, 255, 255))
    mask = Image.new("L", sprite.size)
    mask.putpixel((0, 1), 255)
    result = tint_kit(sprite, "#743D42", "#6B755D", material_mask=mask)
    assert result.getpixel((0, 0)) == (0, 255, 255, 255)
    assert result.getpixel((0, 1)) == (107, 117, 93, 255)


@pytest.mark.parametrize("bank,age", [("players", 24), ("staff", 55)])
def test_every_component_can_be_assembled_in_its_own_bank(bank: str, age: int) -> None:
    catalog = load_catalog()
    recipe = make_raster_recipe("coverage", bank)
    # Exercise every part, including every head against each eye/nose/mouth/hair option.
    for head in catalog["banks"][bank]["head"]:
        for layer in recipe.components:
            for component in catalog["banks"][bank][layer]:
                parts = {**recipe.components, "head": head, layer: component}
                image = render_raster(
                    replace(recipe, components=parts),
                    age=age,
                    kit_primary="#743D42",
                    kit_secondary="#E8DECB",
                    size=96,
                    background=None,
                )
                assert image.size == (96, 96)
                assert image.getchannel("A").getextrema()[0] == 0
                assert image.getchannel("A").getextrema()[1] > 200


def test_unknown_component_and_cross_bank_recipe_are_rejected() -> None:
    recipe = make_raster_recipe("bad", "players")
    for head in ("../../secrets.png", make_raster_recipe("staff", "staff").components["head"]):
        with pytest.raises(ValueError, match="Unknown component"):
            render_raster(
                replace(recipe, components={**recipe.components, "head": head}),
                age=24,
                kit_primary="#743D42",
                kit_secondary="#E8DECB",
            )


@pytest.mark.parametrize("size", [96, 256, 512])
def test_supported_export_sizes(size: int) -> None:
    assert render_raster(
        make_raster_recipe("sizes", "players"),
        age=24,
        kit_primary="#743D42",
        kit_secondary="#E8DECB",
        size=size,
    ).size == (size, size)


def test_invalid_color_age_version_and_recipe_are_rejected() -> None:
    recipe = make_raster_recipe("bad", "players")
    with pytest.raises(ValueError, match="hex"):
        render_raster(recipe, age=24, kit_primary="red", kit_secondary="#E8DECB")
    with pytest.raises(ValueError, match="38"):
        draw("bad", age=39)
    with pytest.raises(ValueError, match="Unsupported"):
        render_raster(
            replace(recipe, asset_version=999),
            age=24,
            kit_primary="#743D42",
            kit_secondary="#E8DECB",
        )
    with pytest.raises(ValueError, match="Invalid"):
        recipe_from_json('{"asset_version": "1"}')
