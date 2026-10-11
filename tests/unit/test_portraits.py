from dataclasses import replace
from xml.etree import ElementTree

import pytest

from goaletto.portraits.compositor import make_recipe, render_svg


def test_persisted_recipe_is_deterministic_and_svg_is_valid() -> None:
    recipe = make_recipe("player-01", "players")
    assert recipe == make_recipe("player-01", "players")
    assert recipe != make_recipe("player-02", "players")
    svg = render_svg(recipe)
    root = ElementTree.fromstring(svg)
    assert root.attrib["viewBox"] == "0 0 256 256"
    assert len(root.findall(".//{http://www.w3.org/2000/svg}g")) >= 10


def test_staff_bank_uses_separate_faces_and_hair() -> None:
    recipe = make_recipe("same-id", "staff")
    assert "/staff/" in recipe.components["face"]
    assert "/staff/" in recipe.components["hair"]
    assert "/staff/" in recipe.components["age_details"]


def test_seed_is_xml_escaped() -> None:
    svg = render_svg(make_recipe('<script>"&', "players"))
    ElementTree.fromstring(svg)
    assert "<script>" not in svg


def test_unsafe_color_and_path_are_rejected() -> None:
    recipe = make_recipe("test", "players")
    with pytest.raises(ValueError, match="hex"):
        render_svg(replace(recipe, palette={**recipe.palette, "skin": 'red"/>'}))
    with pytest.raises(ValueError, match="Unknown"):
        render_svg(replace(recipe, components={**recipe.components, "face": "../../secret.svg"}))


@pytest.mark.parametrize("bank", ["players", "staff"])
def test_many_pilot_combinations_are_renderable(bank: str) -> None:
    for number in range(30):
        ElementTree.fromstring(render_svg(make_recipe(f"sample-{number}", bank)))
