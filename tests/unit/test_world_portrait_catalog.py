from goaletto.portraits.compositor import asset_root
from goaletto.portraits.whole import load_whole_catalog, plan_identity_allocation, render_whole


def test_world_portrait_capacity_and_unique_allocation() -> None:
    root = asset_root() / "whole-v2"
    catalog = load_whole_catalog(root)
    assert len(catalog["banks"]["players"]) == 1000
    assert len(catalog["banks"]["staff"]) == 80
    assert len(catalog["identities"]) == 1080
    hashes = [entry["pixel_sha256"] for entry in catalog["variants"].values()]
    assert len(hashes) == len(set(hashes))
    people = [f"club-{club:02}-player-{slot:02}" for club in range(1, 51) for slot in range(1, 21)]
    allocation = plan_identity_allocation(people, "players", "world-1", root=root)
    assert len(allocation) == 1000
    assert len({recipe.identity_id for recipe in allocation.values()}) == 1000
    for recipe in list(allocation.values())[::37]:
        image = render_whole(
            recipe, kit_primary="#743D42", kit_secondary="#F2E9D8", size=96, root=root
        )
        assert image.mode == "RGBA" and image.size == (96, 96)
