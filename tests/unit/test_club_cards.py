"""Presenter, keyboard and callback tests. No database, no network."""

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
    per = 5
    total_pages = max(1, (total + per - 1) // per)
    return SquadPage(club(), members[(page - 1) * per : page * per], page, total_pages, total)


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


def test_squad_pagination_back_and_return_page() -> None:
    text1, kb1 = squad_view(squad_page(1))
    assert "1/2" in text1 and "Igrok &lt;1&gt;" in text1 and "Igrok &lt;6&gt;" not in text1
    text2, kb2 = squad_view(squad_page(2))
    assert "2/2" in text2 and "Igrok &lt;6&gt;" in text2
    flat1 = [b.callback_data for row in kb1.inline_keyboard for b in row]
    flat2 = [b.callback_data for row in kb2.inline_keyboard for b in row]
    assert squad_callback(2) in flat1 and squad_callback(1) in flat2
    assert club_callback() in flat1 and MENU_CALLBACK in flat2
    # Player buttons remember their squad page for Back navigation.
    first = kb1.inline_keyboard[0][0].callback_data or ""
    assert parse_view_callback(first)[2] == 1
    last = kb2.inline_keyboard[0][0].callback_data or ""
    assert parse_view_callback(last)[2] == 2


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
    callbacks = [b.callback_data for row in keyboard.inline_keyboard for b in row]
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
