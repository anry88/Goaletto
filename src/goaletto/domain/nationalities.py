"""Stable cosmetic nationality-v1, independent of portraits, names and skills."""

from hashlib import md5
from uuid import UUID

COUNTRIES = (
    "AR",
    "AU",
    "BE",
    "BR",
    "CA",
    "CH",
    "CL",
    "CM",
    "CO",
    "DE",
    "DK",
    "DZ",
    "EG",
    "ES",
    "FR",
    "GB",
    "GH",
    "HR",
    "IT",
    "JP",
    "KR",
    "MA",
    "MX",
    "NG",
    "NL",
    "NO",
    "PL",
    "PT",
    "RS",
    "SE",
    "SN",
    "TR",
)


def nationality(person_id: UUID) -> str:
    # Frozen v1 mapping is also used for deterministic SQL backfill.
    slot = int(md5(str(person_id).encode(), usedforsecurity=False).hexdigest()[:7], 16)
    return COUNTRIES[slot % len(COUNTRIES)]


def flag(code: str | None) -> str:
    if code not in COUNTRIES:
        return "🌐"
    return "".join(chr(0x1F1E6 + ord(letter) - ord("A")) for letter in code)


def flag_name(name: str, code: str | None) -> str:
    return f"{flag(code)} {name}"
