"""Pure voluntary-feedback screens. No database, network or clock reads."""

from uuid import UUID

from aiogram.types import InlineKeyboardButton as Button
from aiogram.types import InlineKeyboardMarkup as Keyboard

from goaletto.application.feedback import REASON_CODES
from goaletto.bot.result_views import compact_id, expand_id
from goaletto.presentation.i18n import LabelMap, tr

REASON_LABELS = LabelMap(
    {
        "unclear_next_step": "Непонятно, что дальше",
        "difficult_lineup": "Сложно настроить состав",
        "long_wait": "Долго ждать матч",
        "inconvenient_time": "Неудобное время",
        "uninteresting_game": "Неинтересная игра",
        "technical_problem": "Техническая проблема",
        "other": "Другое",
    }
)

OPEN_CALLBACK = "fb:open"


def _button(text: str, data: str) -> Button:
    if len(data.encode()) > 64:
        raise ValueError("Callback exceeds Telegram limit")
    return Button(text=text[:64], callback_data=data)


def reason_callback(response_id: UUID, index: int) -> str:
    if not isinstance(response_id, UUID) or type(index) is not int:
        raise ValueError("Invalid feedback reason")
    if not 0 <= index < len(REASON_CODES):
        raise ValueError("Unknown feedback reason")
    return f"fb:r:{compact_id(response_id)}:{index}"


def skip_callback(response_id: UUID) -> str:
    if not isinstance(response_id, UUID):
        raise ValueError("Invalid feedback response")
    return f"fb:s:{compact_id(response_id)}"


def optout_callback(response_id: UUID) -> str:
    if not isinstance(response_id, UUID):
        raise ValueError("Invalid feedback response")
    return f"fb:o:{compact_id(response_id)}"


def comment_skip_callback(response_id: UUID) -> str:
    if not isinstance(response_id, UUID):
        raise ValueError("Invalid feedback response")
    return f"fb:k:{compact_id(response_id)}"


def parse_callback(value: str) -> tuple[str, UUID | None, int | None]:
    if type(value) is not str or not value or len(value.encode()) > 64:
        raise ValueError("Invalid feedback callback")
    if value == OPEN_CALLBACK:
        return ("open", None, None)
    parts = value.split(":")
    if len(parts) not in {3, 4} or parts[0] != "fb":
        raise ValueError("Stale feedback button")
    action = parts[1]
    if action == "r" and len(parts) == 4:
        response_id = expand_id(parts[2])
        if not parts[3].isascii() or not parts[3].isdigit() or str(int(parts[3])) != parts[3]:
            raise ValueError("Stale feedback button")
        index = int(parts[3])
        if not 0 <= index < len(REASON_CODES):
            raise ValueError("Stale feedback button")
        return (action, response_id, index)
    if action in {"s", "o", "k"} and len(parts) == 3:
        return (action, expand_id(parts[2]), None)
    raise ValueError("Stale feedback button")


def prompt_view(response_id: UUID) -> tuple[str, Keyboard]:
    if not isinstance(response_id, UUID):
        raise ValueError("Invalid feedback response")
    rows = [
        [_button(REASON_LABELS[code], reason_callback(response_id, index))]
        for index, code in enumerate(REASON_CODES)
    ]
    rows.append([_button(tr("Пропустить"), skip_callback(response_id))])
    rows.append([_button(tr("Не спрашивать больше"), optout_callback(response_id))])
    return (
        tr(
            "Поделитесь впечатлением: что помешало или что было неудобно? "
            "Ответ добровольный и ни на что не влияет."
        ),
        Keyboard(inline_keyboard=rows),
    )


def comment_view(response_id: UUID) -> tuple[str, Keyboard]:
    if not isinstance(response_id, UUID):
        raise ValueError("Invalid feedback response")
    return (
        tr(
            "Спасибо! По желанию добавьте короткий комментарий до 500 символов "
            "в течение 30 минут. Можно пропустить."
        ),
        Keyboard(
            inline_keyboard=[
                [_button(tr("Пропустить"), comment_skip_callback(response_id))],
                [_button(tr("🏟 Клуб"), "vc:c")],
            ]
        ),
    )


def thanks_view() -> tuple[str, Keyboard]:
    return (
        tr("Спасибо за отзыв! Он поможет сделать игру понятнее."),
        Keyboard(inline_keyboard=[[_button(tr("🏟 Клуб"), "vc:c")]]),
    )


def skipped_view() -> tuple[str, Keyboard]:
    return (
        tr("Хорошо, больше не спрашиваем в этот раз."),
        Keyboard(inline_keyboard=[[_button(tr("🏟 Клуб"), "vc:c")]]),
    )


def opted_out_view() -> tuple[str, Keyboard]:
    return (
        tr("Поняли: больше не будем просить отзыв."),
        Keyboard(inline_keyboard=[[_button(tr("🏟 Клуб"), "vc:c")]]),
    )


def unavailable_view() -> tuple[str, Keyboard]:
    return (
        tr("Отзыв сейчас недоступен. Откройте /start."),
        Keyboard(inline_keyboard=[[_button(tr("🏟 Клуб"), "vc:c")]]),
    )


def cooldown_view() -> tuple[str, Keyboard]:
    return (
        tr("Спасибо! Следующий отзыв можно будет оставить чуть позже."),
        Keyboard(inline_keyboard=[[_button(tr("🏟 Клуб"), "vc:c")]]),
    )


def expired_view() -> tuple[str, Keyboard]:
    return (
        tr("Время для комментария вышло. Спасибо за ответ!"),
        Keyboard(inline_keyboard=[[_button(tr("🏟 Клуб"), "vc:c")]]),
    )


def secondary_button() -> Button:
    return _button(tr("✍️ Оставить отзыв"), OPEN_CALLBACK)
