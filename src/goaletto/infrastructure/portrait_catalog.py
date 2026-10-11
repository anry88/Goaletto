"""Publish existing illustrated assets to the registry; never generate art."""

from pathlib import Path, PurePosixPath
from typing import Any, cast

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.infrastructure.locks import APPEARANCE_ALLOCATION_LOCK
from goaletto.infrastructure.models import AppearanceIdentity, AppearanceVariant
from goaletto.portraits.whole import load_whole_catalog


def catalog_records(root: Path | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    catalog = load_whole_catalog(root)
    origin_banks: dict[str, str] = {}
    for bank, base_variants in catalog["banks"].items():
        for variant_id in base_variants:
            identity = catalog["variants"][variant_id]["identity_id"]
            if identity in origin_banks:
                raise ValueError("Multiple base appearances for the same identity")
            origin_banks[identity] = bank
    identities = [
        {"id": identity, "origin_bank": bank, "catalog_version": catalog["catalog_version"]}
        for identity, bank in sorted(origin_banks.items())
    ]
    variants: list[dict[str, Any]] = []
    for variant_id, entry in sorted(catalog["variants"].items()):
        if entry["identity_id"] not in origin_banks:
            raise ValueError("Variant has no base identity")
        for key in ("image", "garment_mask"):
            path = PurePosixPath(entry[key])
            if path.is_absolute() or ".." in path.parts or "\\" in entry[key]:
                raise ValueError("Catalog paths must stay relative to the portrait root")
        variants.append(
            {
                "id": variant_id,
                "identity_id": entry["identity_id"],
                "bank": entry["bank"],
                "stage": entry.get("stage"),
                "asset_path": entry["image"],
                "mask_path": entry["garment_mask"],
            }
        )
    return identities, variants


async def register_catalog(session: AsyncSession, *, root: Path | None = None) -> tuple[int, int]:
    """Idempotent publication in caller's transaction, with immutable ID checks."""
    identities, variants = catalog_records(root)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": APPEARANCE_ALLOCATION_LOCK}
    )
    # Batches avoid asyncpg's bind parameter limit when the asset bank expands.
    for model, records in ((AppearanceIdentity, identities), (AppearanceVariant, variants)):
        for offset in range(0, len(records), 500):
            chunk = records[offset : offset + 500]
            await session.execute(insert(model).values(chunk).on_conflict_do_nothing())
            column = AppearanceIdentity.id if model is AppearanceIdentity else AppearanceVariant.id
            stored = await session.scalars(
                select(model).where(column.in_([r["id"] for r in chunk]))
            )
            expected = {r["id"]: r for r in chunk}
            for item in stored:
                record = expected[cast(AppearanceIdentity | AppearanceVariant, item).id]
                if any(getattr(item, key) != value for key, value in record.items()):
                    raise ValueError("Published portrait ID has conflicting metadata")
    return len(identities), len(variants)
