from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from uuid import uuid4

from PIL import Image

from goaletto.application.staff_cards import StaffCard
from goaletto.bot.card_images import staff_image
from goaletto.bot.player_router import caption_fits
from goaletto.bot.staff_cards import staff_card_view
from goaletto.infrastructure.models import Person, StaffCareer
from goaletto.portraits.club_kit import render_club_kit
from goaletto.portraits.whole import WholePortraitRecipe
from goaletto.presentation.i18n import using_locale


def test_staff_portrait_fallback_keeps_face_and_cache_tracks_both_colors(monkeypatch):
    recipe = WholePortraitRecipe(1, "same-face", "same-face-player")
    monkeypatch.setattr("goaletto.bot.card_images.recipe_for_variant", lambda _: recipe)

    def missing(*args, **kwargs):
        raise ValueError("No staff band")

    monkeypatch.setattr("goaletto.bot.card_images.recipe_for_age", missing)
    image = staff_image("same-face", "same-face-player", 1, 38, "#315B49", "#E2BF57", 1)
    assert image.identity == recipe.identity_id and image.variant == recipe.variant_id
    assert staff_image("other-face", "same-face-player", 1, 38, "#315B49", "#E2BF57", 1) is None
    for primary, secondary, version in (
        ("#FFFFFF", "#E2BF57", 1),
        ("#315B49", "#FFFFFF", 1),
        ("#315B49", "#E2BF57", 2),
    ):
        assert (
            staff_image("same-face", "same-face-player", 1, 38, primary, secondary, version).key
            != image.key
        )


def test_two_fabric_colors_are_present_and_recoloring_changes_the_image():
    primary, secondary = "#315B49", "#E2BF57"
    image = Image.open(BytesIO(render_club_kit(primary, secondary)))
    assert image.size == (256, 112)
    colors = {color: count for count, color in image.getcolors(256 * 112)}
    assert colors[(49, 91, 73)] > 300 and colors[(226, 191, 87)] > 300
    assert render_club_kit(primary, secondary) != render_club_kit(secondary, primary)


def test_maximal_former_player_card_fits_caption_in_ru_and_en_and_quotes_before_money():
    person = Person(id=uuid4(), name="<" * 120, age=38)
    at = datetime(2026, 10, 15, tzinfo=UTC)
    career = StaffCareer(person_id=person.id, status="pending", expires_at=at + timedelta(days=8))
    card = StaffCard(
        uuid4(),
        person,
        "head_coach",
        {
            "skill": 20,
            "wage": 16000,
            "normal_wage": 20000,
            "fee": 80000,
            "loyal": 1,
            "status": "last_season",
        },
        False,
        True,
        career,
        "&" * 120,
        "MC",
        1,
        "#315B49",
        "#E2BF57",
        1,
    )
    for locale in ("ru", "en"):
        with using_locale(locale):
            text, keyboard = staff_card_view(card, 2)
            assert "&lt;" in text and "&amp;" in text and "−20%" in text
            assert caption_fits(text), (locale, len(text))
            callbacks = [b.callback_data for row in keyboard.inline_keyboard for b in row]
            assert any(c.startswith("cg:q:") for c in callbacks)
            assert not any(c.startswith(("cg:x:", "cg:b:")) for c in callbacks)
            assert "cg:h:2" in callbacks and all(len(c.encode()) <= 64 for c in callbacks)


def test_head_coach_card_has_no_dismissal_but_specialist_card_does():
    person = Person(id=uuid4(), name="Coach", age=55)
    card = StaffCard(
        uuid4(),
        person,
        "head_coach",
        {"skill": 1, "wage": 7600, "status": "active"},
        True,
        False,
        None,
        None,
        None,
        1,
        "#315B49",
        "#E2BF57",
        1,
    )
    text, keyboard = staff_card_view(card)
    assert "только заменить" in text
    assert not any(
        (b.callback_data or "").startswith("cg:f:") for row in keyboard.inline_keyboard for b in row
    )
    _, keyboard = staff_card_view(replace(card, role="scout"))
    assert any(
        (b.callback_data or "").startswith("cg:f:") for row in keyboard.inline_keyboard for b in row
    )
