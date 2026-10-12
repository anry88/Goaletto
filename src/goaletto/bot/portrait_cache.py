"""Durable Telegram file_id cache keyed by portrait rendering inputs.

The cache key covers identity/variant/catalog, both kit colors plus kit
version, pixel size and background, so a kit change re-renders the same face
instead of reusing a stale file. A missing illustrated variant is never
replaced with another person's face; callers show an explicit text notice.
"""

from __future__ import annotations

import hashlib
import re
from typing import Protocol

COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
PORTRAIT_UNAVAILABLE_TEXT = (
    "Портрет недоступен для этого возраста — показана текстовая карточка, "
    "чужое лицо не подставляется."
)
PORTRAIT_SIZES = (96, 256)


def portrait_cache_key(
    *,
    identity_id: str,
    variant_id: str,
    catalog_version: int,
    kit_primary: str,
    kit_secondary: str,
    kit_version: int,
    size: int,
    background: str = "transparent",
) -> str:
    if not identity_id or len(identity_id) > 100:
        raise ValueError("Invalid portrait identity")
    if not variant_id or len(variant_id) > 100:
        raise ValueError("Invalid portrait variant")
    if type(catalog_version) is not int or catalog_version <= 0:
        raise ValueError("Invalid portrait catalog version")
    if COLOR_RE.fullmatch(kit_primary) is None or COLOR_RE.fullmatch(kit_secondary) is None:
        raise ValueError("Kit colors must be #RRGGBB")
    if type(kit_version) is not int or kit_version <= 0:
        raise ValueError("Invalid kit version")
    if size not in PORTRAIT_SIZES:
        raise ValueError("Portrait size must be 96 or 256")
    if not background or len(background) > 32:
        raise ValueError("Invalid portrait background")
    canonical = "|".join(
        (
            "portrait-file:v1",
            identity_id,
            variant_id,
            str(catalog_version),
            kit_primary.upper(),
            kit_secondary.upper(),
            str(kit_version),
            str(size),
            background,
        )
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def validate_file_id(file_id: str) -> str:
    if (
        type(file_id) is not str
        or not 1 <= len(file_id) <= 500
        or any(c.isspace() for c in file_id)
    ):
        raise ValueError("Invalid Telegram file_id")
    return file_id


class PortraitFileStore(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def put(self, key: str, file_id: str) -> None: ...


class InMemoryPortraitFileCache:
    """Process-local stand-in; production uses the durable Postgres table."""

    def __init__(self, backing: dict[str, str] | None = None) -> None:
        self._backing: dict[str, str] = backing if backing is not None else {}

    async def get(self, key: str) -> str | None:
        return self._backing.get(key)

    async def put(self, key: str, file_id: str) -> None:
        self._backing[key] = validate_file_id(file_id)

    def snapshot(self) -> dict[str, str]:
        return dict(self._backing)
