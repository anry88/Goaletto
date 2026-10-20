"""Public channels shared by the start and help replies."""

from goaletto.presentation.i18n import tr


def channel_links() -> str:
    return tr(
        '📣 Каналы Goaletto: <a href="https://t.me/goaletto_ru">Русский</a> · '
        '<a href="https://t.me/goaletto_en">English</a>'
    )
