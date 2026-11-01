"""The same progress units and whole-point transitions on every report."""

from goaletto.presentation.i18n import locale


def percent(bps: int) -> str:
    value = f"{bps / 100:.2f}".rstrip("0").rstrip(".")
    return value.replace(".", ",") if locale() == "ru" else value


def progress_line(
    label: str,
    fraction: int | None,
    gain: int,
    before: int | None = None,
    after: int | None = None,
) -> str:
    amount = percent(fraction) if fraction is not None else "—"
    line = f"{label}: {amount}/100 (+{percent(gain)}%)"
    if before is not None and after is not None and after > before:
        line += f" · {before} → {after}"
    return line


def experience_line(label: str, before: int | None, after: int | None, gain: int) -> str:
    return progress_line(
        label,
        after % 10000 if after is not None else None,
        gain,
        before // 10000 if before is not None else None,
        after // 10000 if after is not None else None,
    )


def position_percent(value: int) -> str:
    text = f"{value / 10000:.4f}".rstrip("0").rstrip(".")
    return text.replace(".", ",") if locale() == "ru" else text


def position_percent_line(label: str, before: int, after: int) -> str:
    change = after - before
    sign = "+" if change >= 0 else ""
    return f"{label}: {position_percent(after)}% ({sign}{position_percent(change)}%)"
