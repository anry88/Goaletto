"""Presenter, keyboard and callback tests. No database, no network."""

from dataclasses import replace
from uuid import uuid4

import pytest

from goaletto.application.club_view import ClubCard, PlayerCard, SquadMember, SquadPage
from goaletto.bot.club_cards import (
    MENU_CALLBACK,
    club_callback,
    club_view,
    format_money,
    is_view_callback,
    menu_callback,
    menu_view,
    parse_view_callback,
    player_callback,
    player_view,
    squad_callback,
    squad_view,
    stale_view,
)
from goaletto.bot.portrait_cache import PORTRAIT_UNAVAILABLE_TEXT


def club() -> ClubCard:
    return ClubCard(
        id=uuid4(),
        name="<b>Аврора&</b>",
        tier=5,
        budget=1250000,
        stadium_capacity=5000,
        supporters=2500,
        kit_primary="#743D42",
        kit_secondary="#F2E9D8",
        kit_version=1,
    )


def member(i: int, status: str = "active") -> SquadMember:
    return SquadMember(
        person_id=uuid4(),
        name=f"Игрок <{i}>&",
        age=20 + i,
        position="MC",
        form=6,
        status=status,
        slot=i,
    )


def squad_page(page: int, total: int = 6) -> SquadPage:
    members = tuple(member(i) for i in range(1, total + 1))
    return SquadPage(club(), members, page, 1, total)


def player(status: str = "active") -> PlayerCard:
    return PlayerCard(
        person_id=uuid4(),
        name='Петр "Пас" <Иванов>',
        age=34,
        position="FC",
        alternative_positions=("AMC",),
        goalkeeping=1,
        defending=7,
        playmaking=10,
        winger=10,
        attacking=12,
        set_pieces=10,
        stamina=11,
        pace=12,
        leadership=10,
        loyalty=10,
        form=6,
        experience=41,
        morale=55,
        status=status,
        injured=False,
        club_id=uuid4(),
        club_name="<Аврора>",
        appearance_identity_id="p0001",
        appearance_variant_id="p0001-player-veteran",
        catalog_version=2,
        kit_primary="#743D42",
        kit_secondary="#F2E9D8",
        kit_version=1,
    )


def test_callbacks_are_compact_and_parse() -> None:
    person = uuid4()
    for value in (menu_callback(), club_callback(), squad_callback(4), player_callback(person, 3)):
        assert len(value.encode()) <= 64
    assert parse_view_callback(MENU_CALLBACK) == ("menu", None, 1)
    assert parse_view_callback(club_callback()) == ("club", None, 1)
    assert parse_view_callback(squad_callback(2)) == ("squad", None, 2)
    assert parse_view_callback(player_callback(person, 3)) == ("player", person, 3)
    for bad in (
        "",
        "vc:",
        "vc:s:0",
        "vc:s:21",
        "vc:s:x",
        "vc:p:bad:1",
        "vc:p::1",
        "preview:squad",
        "preview:match",
        "game:club",
        "table:abc:def:1",
        "report:abc",
        "vc:zzz",
    ):
        with pytest.raises(ValueError):
            parse_view_callback(bad)
    assert not is_view_callback("preview:squad")
    assert not is_view_callback("game:club")
    assert not is_view_callback(None)
    assert is_view_callback(squad_callback(1))
    with pytest.raises(ValueError):
        squad_callback(0)
    with pytest.raises(ValueError):
        player_callback(uuid4(), 0)


def test_club_view_escapes_and_has_menu() -> None:
    text, keyboard = club_view(club())
    assert "&lt;b&gt;Avrora&amp;&lt;/b&gt;" in text
    assert "<b>Аврора&</b>" not in text  # raw markup never leaks unescaped
    assert "1" in format_money(1250000)
    callbacks = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert squad_callback(1) in callbacks and MENU_CALLBACK in callbacks
    assert not any((c or "").startswith("market:") for c in callbacks)
    assert not any((c or "").startswith("hire:") for c in callbacks)


@pytest.mark.parametrize(
    ("language", "legend"),
    (
        ("ru", "🔶 — последний сезон · 🤕 — травма"),
        ("en", "🔶 — last season · 🤕 — injured"),
    ),
)
def test_whole_squad_fits_one_message_with_only_injury_and_last_season_badges(language, legend):
    from goaletto.presentation.i18n import using_locale

    page = squad_page(1, 25)
    members = tuple(
        replace(
            m,
            name=m.name + " Long player name" * 3,
            position="DC",
            slot_position="GK" if i == 0 else "DC" if i < 11 else None,
            slot=m.slot if i < 20 else None,
            alternatives=("DR",),
            injured=i == 0,
            status="last_season" if i == 0 else "active",
        )
        for i, m in enumerate(page.members)
    )
    with using_locale(language):
        text, keyboard = squad_view(replace(page, members=members))
    assert text.endswith(legend)
    assert "Igrok &lt;1&gt;" in text and "Igrok &lt;25&gt;" in text
    assert "(GK) · DC/DR 🔶 🤕" in text and len(text.encode("utf-16-le")) // 2 < 4096
    assert all(mark not in text for mark in ("⚠️", "🟨", "⛔"))
    callbacks = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert callbacks[:25] == [player_callback(m.person_id) for m in members]
    assert callbacks[25:] == [club_callback(), MENU_CALLBACK]


def test_player_statuses_and_portrait_notice() -> None:
    text, _ = player_view(player("active"))
    assert "в составе" in text and "Последний сезон" not in text
    text, _ = player_view(player("last_season"))
    assert "⏳ Последний сезон" in text and "Трансфер недоступен" in text
    text, _ = player_view(player("retired"))
    assert "🏁 Завершил карьеру" in text
    assert "Недоступен для состава" in text
    assert "Petr" in text and "&quot;Pas&quot;" in text and '"Пас"' not in text
    assert "&lt;Ivanov&gt;" in text and "&lt;Avrora&gt;" in text
    text, _ = player_view(player("active"), portrait_ready=False)
    assert PORTRAIT_UNAVAILABLE_TEXT in text
    # Missing variant never borrows another face: no second identity leaks in.
    assert text.count("p0001") == 0
    with pytest.raises(ValueError):
        player_view(player("active"), return_page=0)


def test_menu_has_no_fake_market_or_hiring() -> None:
    text, keyboard = menu_view()
    assert "Главное меню" in text
    buttons = {b.callback_data: b.text for row in keyboard.inline_keyboard for b in row}
    assert buttons["nav:table"] == "🏆 Лига"
    assert buttons["ca:cup"] == "🏆 Кубок"
    assert buttons["ca:champions"] == "🏆 Кубок чемпионов"
    assert "ca:cups" not in buttons
    callbacks = list(buttons)
    joined = " ".join(c or "" for c in callbacks)
    assert "market" not in joined and "hire" not in joined and "transfer" not in joined
    text, keyboard = stale_view()
    assert "устарела" in text


def test_player_full_skill_names_and_caption_budget() -> None:
    from dataclasses import replace

    from goaletto.bot.player_router import caption_fits

    text, _ = player_view(player())
    for label, value in (
        ("Вратарское мастерство", 1),
        ("Защита", 7),
        ("Плеймейкинг", 10),
        ("Фланговая игра", 10),
        ("Атака", 12),
        ("Стандарты", 10),
        ("Выносливость", 11),
        ("Скорость", 12),
        ("Лидерство", 10),
        ("Лояльность", 10),
    ):
        assert f"\n{label}: {value}\n" in text
    assert caption_fits(text)
    longest = replace(
        player("last_season"),
        name="😀" * 100,
        club_name="&" * 100,
        alternative_positions=("DL", "DR", "MC", "ML", "MR"),
        injured=True,
    )
    assert caption_fits(player_view(longest)[0])
    assert caption_fits("<b>" + "&amp;" * 1024 + "</b>")
    assert not caption_fits("😀" * 513)
