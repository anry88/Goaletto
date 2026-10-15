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
from goaletto.domain.kickoff import local_datetime
from goaletto.domain.match_types import Instruction, Mentality, Side, Tactic
from goaletto.domain.names import latin_name
from goaletto.presentation.i18n import LabelMap, tr

INSTRUCTION_LABELS = LabelMap(
    {
        Instruction.LONG_SHOTS: "🎯 Бить издалека",
        Instruction.MOVE_LEFT: "↙️ Смещаться влево",
        Instruction.MOVE_RIGHT: "↗️ Смещаться вправо",
        Instruction.DROP_BACK: "⬇️ Оттягиваться назад",
        Instruction.RUNS_BEHIND: "🏃 Рывки за спину",
    }
)


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
    if (
        not 1 <= target <= 40
        or str(target) != suffix[:-1]
        or suffix[-1] == "h"
        and target not in {16, 31, 36}
    ):
        raise ValueError("Invalid live window")
    args = tuple(int(s) for s in args_text)
    if any(not 0 <= a <= 20 for a in args):
        raise ValueError("Invalid roster index")
    return action, args, expand_id(parts[-3]), expand_id(parts[-2]), target, suffix[-1] == "h"


def render(
    screen: LiveScreen,
    *,
    panel: int = 0,
    selected: int = 0,
    notice: str | None = None,
    feedback_available: bool = False,
) -> tuple[str, Keyboard]:
    lines = [f"⚽ <b>{escape(latin_name(screen.club_name))}</b>"]
    rows = []
    if screen.state is None:
        lines.append(tr("Матч ещё не начался."))
    else:
        state = screen.state
        lines.append(
            f"{escape(latin_name(state.home.snapshot.name))} "
            f"{state.home.stats.goals}:{state.away.stats.goals} "
            f"{escape(latin_name(state.away.snapshot.name))} · {state.game_minute}′"
        )
        if state.phase.value in {"half_time", "extra_break", "extra_half_time"}:
            lines.append(tr("Перерыв · замены не расходуют окно замен."))
        if state.phase.value.startswith("extra_"):
            lines.append(tr("Дополнительное время"))
        if state.knockout and state.knockout.tie.tie_format == "single":
            lines.append(tr("Нейтральное поле"))
        if state.knockout and state.phase.value == "full_time" and state.knockout.segment != "leg1":
            from goaletto.domain.knockout_match import decision

            settled = decision(state)
            direct = state.home.snapshot.club_id == state.knockout.tie.club_a_id
            h, a = (
                (settled.aggregate_a, settled.aggregate_b)
                if direct
                else (settled.aggregate_b, settled.aggregate_a)
            )
            lines.append(tr("Общий счёт: {home}:{away}", home=h, away=a))
            winner = (
                state.home.snapshot.name
                if settled.winner == state.home.snapshot.club_id
                else state.away.snapshot.name
            )
            lines.append(tr("Победитель: {club}", club=escape(latin_name(winner))))
        if state.knockout and state.knockout.shootout:
            from goaletto.domain.knockout import decide_shootout

            score = decide_shootout(state.knockout.shootout)
            h, a = (
                (score.score_a, score.score_b)
                if state.home.snapshot.club_id == state.knockout.tie.club_a_id
                else (score.score_b, score.score_a)
            )
            lines.append(tr("Пенальти: {home}:{away}", home=h, away=a))
        lines.append(
            tr(
                "Удары: {p0} / {p1} · В створ: {p2} / {p3}",
                p0=state.home.stats.shots,
                p1=state.away.stats.shots,
                p2=state.home.stats.on_target,
                p3=state.away.stats.on_target,
            )
        )
        if screen.commentary:
            lines.extend((tr("\nПоследние события:"), commentary_html(screen.commentary)))
        if state.phase.value in {"full_time", "abandoned"}:
            lines.append(
                tr("🏁 Матч завершён.")
                if state.phase.value == "full_time"
                else tr("Матч остановлен.")
            )
            rows.append([_button(tr("📋 Отчёт о матче"), match_callback(screen.fixture_id))])
            from goaletto.bot.feedback_views import secondary_button

            if feedback_available:
                rows.append([secondary_button()])
        elif not screen.open:
            lines.append(tr("Сейчас действие недоступно. Обновите экран."))
        else:
            assert screen.projected is not None and screen.deadline is not None
            team = screen.projected.home if screen.side == Side.HOME else screen.projected.away
            current = state.home if screen.side == Side.HOME else state.away
            lines.append(
                tr(
                    "Сейчас: {p0} · {p1}",
                    p0=TACTIC_LABELS[current.tactic],
                    p1=MENTALITY_LABELS[current.mentality],
                )
            )
            if team.snapshot.coach is None:
                lines.append(
                    tr(
                        "С учётом принятых команд: замены {p0}/5 · окна {p1}/3 · "
                        "тактика {p2}/3 · инструкции {p3}/3",
                        p0=team.substitutions,
                        p1=len(team.substitution_windows),
                        p2=team.tactic_changes,
                        p3=team.instruction_changes,
                    )
                )
            abilities = team.snapshot.coach.abilities if team.snapshot.coach else None
            if abilities:
                lines.append(
                    tr(
                        "Замены: {s}/5 · окна: {w}/3",
                        s=team.substitutions,
                        w=len(team.substitution_windows),
                    )
                )
                lines.append(
                    tr(
                        "Лимиты тренера: тактика {t}/{tm} · инструкции {i}/{im} · настрой {m}/{mm}",
                        t=team.tactic_changes,
                        tm=abilities.tactic_changes,
                        i=team.instruction_changes,
                        im=abilities.instruction_changes,
                        m=team.mentality_changes,
                        mm=abilities.mentality_changes,
                    )
                )
            players = list(team.snapshot.players)
            index_by_id = {p.id: index for index, p in enumerate(players, 1)}
            names = {p.id: latin_name(p.name) for p in players}
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
                    and (
                        not abilities
                        or value in abilities.tactics
                        and team.tactic_changes < abilities.tactic_changes
                    )
                ]
            elif panel == 3:
                rows = [
                    [_button(label, callback(screen, "m", list(Mentality).index(value)))]
                    for value, label in MENTALITY_LABELS.items()
                    if value != team.mentality
                    and (not abilities or team.mentality_changes < abilities.mentality_changes)
                ]
            elif panel == 4 and state.engine_version != "regulation-v1":
                rows = [
                    [_button(names[s.player_id], callback(screen, "u", index_by_id[s.player_id]))]
                    for s in team.lineup
                    if s.position != "GK"
                    and (not abilities or team.instruction_changes < abilities.instruction_changes)
                ]
            elif panel == 5:
                outgoing = players[selected - 1].id if 1 <= selected <= len(players) else None
                if outgoing not in {s.player_id for s in team.lineup}:
                    raise ValueError("Stale outgoing player")
                conditions = {c.player_id: c for c in team.conditions}
                rows = [
                    [
                        _button(
                            tr(
                                "{p0} · {p1} · форма {p2}",
                                p0=names[p],
                                p1=players[index_by_id[p] - 1].position,
                                p2=players[index_by_id[p] - 1].form,
                            ),
                            callback(screen, "s", selected, index_by_id[p]),
                        )
                    ]
                    for p in team.bench
                    if not conditions[p].injured and not conditions[p].dismissed
                ]
                lines.append(tr("Выберите игрока на замену."))
            elif panel == 6:
                player = players[selected - 1] if 1 <= selected <= len(players) else None
                if player is None or player.id not in {
                    s.player_id
                    for s in team.lineup
                    if s.position != "GK"
                    and (not abilities or team.instruction_changes < abilities.instruction_changes)
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
                        _button(tr("🔄 Замены"), callback(screen, "p", 1)),
                        _button(tr("📋 Тактика"), callback(screen, "p", 2)),
                    ],
                    [_button(tr("💬 Настрой"), callback(screen, "p", 3))],
                ]
                if state.engine_version != "regulation-v1":
                    rows.append([_button(tr("🎯 Инструкция"), callback(screen, "p", 4))])
            rows.append([_button(tr("← Матч"), callback(screen, "r"))])
        kinds = {
            "substitution": tr("Замена"),
            "tactics": tr("Тактика"),
            "mentality": tr("Настрой"),
            "instruction": tr("Инструкция"),
        }
        for receipt in screen.recent if state.phase.value not in {"full_time", "abandoned"} else ():
            if receipt.status == "accepted":
                lines.append(f"{kinds.get(receipt.kind, tr('Команда'))}: {tr('Принято')}")
    if notice:
        lines.append(escape(notice))
    if screen.next_fixture:
        home, away, kickoff = screen.next_fixture
        lines.append(
            tr(
                "\nСледующий матч: {p0} — {p1}\n{p2}",
                p0=escape(latin_name(home)),
                p1=escape(latin_name(away)),
                p2=local_datetime(kickoff, screen.timezone_name),
            )
        )
        lines.append(tr("Вы играете дома.") if screen.next_is_home else tr("Вы играете в гостях."))
        if screen.next_lineup_lock_at is not None:
            if screen.next_locked:
                lines.append(tr("🔒 Состав на эту встречу уже зафиксирован."))
            else:
                lines.append(
                    tr("Сохраните состав до ")
                    + local_datetime(screen.next_lineup_lock_at, screen.timezone_name)
                    + "."
                )
        rows.append([_button(tr("📋 Подготовить состав"), "ln:open")])
    rows.append([_button(tr("🗓 Расписание"), "ca:open")])
    rows.append([_button(tr("🔄 Обновить"), "lv:open"), _button(tr("🏟 Клуб"), "vc:c")])
    return "\n".join(lines), Keyboard(inline_keyboard=rows)


def no_fixture_view() -> tuple[str, Keyboard]:
    return tr("Матчей в расписании пока нет. Подготовьте состав к следующей встрече."), Keyboard(
        inline_keyboard=[[_button(tr("📋 Расстановка"), "ln:open"), _button(tr("🏟 Клуб"), "vc:c")]]
    )
