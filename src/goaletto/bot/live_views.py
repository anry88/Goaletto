"""Pure native Telegram controls; callbacks pin full ownership, match and window."""

from html import escape
from uuid import UUID

from aiogram.types import InlineKeyboardButton as Button
from aiogram.types import InlineKeyboardMarkup as Keyboard

from goaletto.application.live_commands import LiveScreen
from goaletto.application.match_commentary import commentary_html
from goaletto.bot.history_callbacks import match_callback
from goaletto.bot.lineup_views import MENTALITY_LABELS, TACTIC_LABELS
from goaletto.bot.result_views import compact_id, expand_id
from goaletto.domain.match_types import Instruction, Mentality, Side, Tactic

INSTRUCTION_LABELS = {
    Instruction.LONG_SHOTS: "🎯 Бить издалека",
    Instruction.MOVE_LEFT: "↙️ Смещаться влево",
    Instruction.MOVE_RIGHT: "↗️ Смещаться вправо",
    Instruction.DROP_BACK: "⬇️ Оттягиваться назад",
    Instruction.RUNS_BEHIND: "🏃 Рывки за спину",
}


def _button(text: str, data: str) -> Button:
    if len(data.encode()) > 64:
        raise ValueError("Callback exceeds Telegram limit")
    return Button(text=text[:64], callback_data=data)


def callback(screen: LiveScreen, action: str, *args: int) -> str:
    if screen.match_id is None or screen.target is None:
        raise ValueError("No decision window")
    frame = (
        f"{compact_id(screen.ownership_id)}:{compact_id(screen.match_id)}:"
        f"{screen.target}{'h' if screen.halftime else 'n'}"
    )
    return ":".join(("lv", action, *(str(a) for a in args), frame))


def parse_callback(value: str) -> tuple[str, tuple[int, ...], UUID, UUID, int, bool]:
    if type(value) is not str or len(value.encode()) > 64:
        raise ValueError("Invalid live callback")
    parts = value.split(":")
    arities = {"p": 1, "o": 1, "u": 1, "s": 2, "t": 1, "m": 1, "i": 2, "r": 0}
    if len(parts) < 5 or parts[0] != "lv" or parts[1] not in arities:
        raise ValueError("Stale live callback")
    action = parts[1]
    if len(parts) != 5 + arities[action]:
        raise ValueError("Stale live callback")
    args_text = parts[2:-3]
    if any(not s.isascii() or not s.isdigit() or str(int(s)) != s for s in args_text):
        raise ValueError("Invalid live argument")
    suffix = parts[-1]
    if (
        len(suffix) < 2
        or suffix[-1] not in {"h", "n"}
        or not suffix[:-1].isascii()
        or not suffix[:-1].isdigit()
    ):
        raise ValueError("Invalid live window")
    target = int(suffix[:-1])
    if not 1 <= target <= 30 or str(target) != suffix[:-1] or suffix[-1] == "h" and target != 16:
        raise ValueError("Invalid live window")
    args = tuple(int(s) for s in args_text)
    if any(not 0 <= a <= 20 for a in args):
        raise ValueError("Invalid roster index")
    return action, args, expand_id(parts[-3]), expand_id(parts[-2]), target, suffix[-1] == "h"


def render(
    screen: LiveScreen, *, panel: int = 0, selected: int = 0, notice: str | None = None
) -> tuple[str, Keyboard]:
    lines = [f"⚽ <b>{escape(screen.club_name)}</b>"]
    rows = []
    if screen.state is None:
        lines.append("Матч ещё не начался.")
    else:
        state = screen.state
        lines.append(
            f"{escape(state.home.snapshot.name)} {state.home.stats.goals}:{state.away.stats.goals} "
            f"{escape(state.away.snapshot.name)} · {state.game_minute}′"
        )
        if state.phase.value == "half_time":
            lines.append("Перерыв · замены не расходуют окно замен.")
        lines.append(
            f"Удары: {state.home.stats.shots} / {state.away.stats.shots} · "
            f"В створ: {state.home.stats.on_target} / {state.away.stats.on_target}"
        )
        if screen.commentary:
            lines.extend(("\nПоследние события:", commentary_html(screen.commentary)))
        if state.phase.value in {"full_time", "abandoned"}:
            lines.append(
                "🏁 Матч завершён." if state.phase.value == "full_time" else "Матч остановлен."
            )
            rows.append([_button("📋 Отчёт о матче", match_callback(screen.fixture_id))])
        elif not screen.open:
            lines.append("Сейчас действие недоступно. Обновите экран.")
        else:
            assert screen.projected is not None and screen.deadline is not None
            team = screen.projected.home if screen.side == Side.HOME else screen.projected.away
            current = state.home if screen.side == Side.HOME else state.away
            lines.append(
                f"Сейчас: {TACTIC_LABELS[current.tactic]} · {MENTALITY_LABELS[current.mentality]}"
            )
            lines.append(
                f"С учётом принятых команд: замены {team.substitutions}/5 · "
                f"окна {len(team.substitution_windows)}/3 · "
                f"тактика {team.tactic_changes}/3 · инструкции {team.instruction_changes}/3"
            )
            players = list(team.snapshot.players)
            index_by_id = {p.id: index for index, p in enumerate(players, 1)}
            names = {p.id: p.name for p in players}
            if panel == 1:
                conditions = {c.player_id: c for c in team.conditions}
                rows = [
                    [
                        _button(
                            f"{names[s.player_id]} · {s.position} · "
                            f"{conditions[s.player_id].energy_bps // 100}%",
                            callback(screen, "o", index_by_id[s.player_id]),
                        )
                    ]
                    for s in team.lineup
                ]
            elif panel == 2:
                rows = [
                    [_button(label, callback(screen, "t", list(Tactic).index(value)))]
                    for value, label in TACTIC_LABELS.items()
                    if value != team.tactic
                ]
            elif panel == 3:
                rows = [
                    [_button(label, callback(screen, "m", list(Mentality).index(value)))]
                    for value, label in MENTALITY_LABELS.items()
                    if value != team.mentality
                ]
            elif panel == 4 and state.engine_version == "regulation-v2":
                rows = [
                    [_button(names[s.player_id], callback(screen, "u", index_by_id[s.player_id]))]
                    for s in team.lineup
                    if s.position != "GK"
                ]
            elif panel == 5:
                outgoing = players[selected - 1].id if 1 <= selected <= len(players) else None
                if outgoing not in {s.player_id for s in team.lineup}:
                    raise ValueError("Stale outgoing player")
                conditions = {c.player_id: c for c in team.conditions}
                rows = [
                    [
                        _button(
                            f"{names[p]} · {players[index_by_id[p] - 1].position} · "
                            f"форма {players[index_by_id[p] - 1].form}",
                            callback(screen, "s", selected, index_by_id[p]),
                        )
                    ]
                    for p in team.bench
                    if not conditions[p].injured and not conditions[p].dismissed
                ]
                lines.append("Выберите игрока на замену.")
            elif panel == 6:
                player = players[selected - 1] if 1 <= selected <= len(players) else None
                if player is None or player.id not in {
                    s.player_id for s in team.lineup if s.position != "GK"
                }:
                    raise ValueError("Stale instruction player")
                rows = [
                    [
                        _button(
                            label, callback(screen, "i", selected, list(Instruction).index(value))
                        )
                    ]
                    for value, label in INSTRUCTION_LABELS.items()
                ]
            else:
                rows = [
                    [
                        _button("🔄 Замены", callback(screen, "p", 1)),
                        _button("📋 Тактика", callback(screen, "p", 2)),
                    ],
                    [_button("💬 Настрой", callback(screen, "p", 3))],
                ]
                if state.engine_version == "regulation-v2":
                    rows.append([_button("🎯 Инструкция", callback(screen, "p", 4))])
            rows.append([_button("← Матч", callback(screen, "r"))])
        labels = {
            "accepted": "Принято",
            "applied": "Применено",
            "cancelled": "Отменено",
        }
        kinds = {
            "substitution": "Замена",
            "tactics": "Тактика",
            "mentality": "Настрой",
            "instruction": "Инструкция",
        }
        for receipt in screen.recent if state.phase.value not in {"full_time", "abandoned"} else ():
            lines.append(f"{kinds.get(receipt.kind, 'Команда')}: {labels[receipt.status]}")
    if notice:
        lines.append(escape(notice))
    if screen.next_fixture:
        home, away, kickoff = screen.next_fixture
        lines.append(
            f"\nСледующий матч: {escape(home)} — {escape(away)}\n"
            f"{kickoff.strftime('%d.%m.%Y %H:%M UTC')}"
        )
        lines.append("Вы играете дома." if screen.next_is_home else "Вы играете в гостях.")
        if screen.next_lineup_lock_at is not None:
            if screen.next_locked:
                lines.append("🔒 Состав на эту встречу уже зафиксирован.")
            else:
                lines.append(
                    "Сохраните состав до "
                    + screen.next_lineup_lock_at.strftime("%d.%m.%Y %H:%M UTC")
                    + "."
                )
        rows.append([_button("📋 Подготовить состав", "ln:open")])
    rows.append([_button("🔄 Обновить", "lv:open"), _button("🏟 Клуб", "vc:c")])
    return "\n".join(lines), Keyboard(inline_keyboard=rows)


def no_fixture_view() -> tuple[str, Keyboard]:
    return "Матчей в расписании пока нет. Подготовьте состав к следующей встрече.", Keyboard(
        inline_keyboard=[[_button("📋 Расстановка", "ln:open"), _button("🏟 Клуб", "vc:c")]]
    )
