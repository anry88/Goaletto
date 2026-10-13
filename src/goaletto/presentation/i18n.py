"""Explicit templates, independent of names and durable simulation documents."""

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import TypeVar

LOCALE: ContextVar[str] = ContextVar("goaletto_locale", default="ru")
ENGLISH: dict[str, str] = json.loads(Path(__file__).with_name("en.json").read_text())
K = TypeVar("K")


def locale() -> str:
    return LOCALE.get()


@contextmanager
def using_locale(value: str) -> Iterator[None]:
    if value not in {"ru", "en"}:
        raise ValueError("Unsupported locale")
    token = LOCALE.set(value)
    try:
        yield
    finally:
        LOCALE.reset(token)


def tr(template: str, **parameters: object) -> str:
    # Parameters are opaque data. Never translate names, match data or callbacks.
    selected = ENGLISH[template] if locale() == "en" else template
    return selected.format(**parameters) if parameters else selected


class LabelMap(Mapping[K, str]):
    """Translate labels at access time, rather than freezing the import locale."""

    def __init__(self, values: dict[K, str]) -> None:
        self._labels = values

    def __getitem__(self, key: K) -> str:
        return tr(self._labels[key])

    def __iter__(self) -> Iterator[K]:
        return iter(self._labels)

    def __len__(self) -> int:
        return len(self._labels)
