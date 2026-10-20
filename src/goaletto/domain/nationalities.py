"""Cosmetic nationality policies. Stored countries never depend on later policy changes."""

from hashlib import md5, sha256
from uuid import UUID

# Frozen nationality-v1 mapping used by migration 0047 and legacy snapshots.
LEGACY_COUNTRIES = (
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
COUNTRY_LABELS = {
    "AR": ("Аргентина", "Argentina"),
    "AU": ("Австралия", "Australia"),
    "BE": ("Бельгия", "Belgium"),
    "BR": ("Бразилия", "Brazil"),
    "CA": ("Канада", "Canada"),
    "CH": ("Швейцария", "Switzerland"),
    "CL": ("Чили", "Chile"),
    "CM": ("Камерун", "Cameroon"),
    "CO": ("Колумбия", "Colombia"),
    "DE": ("Германия", "Germany"),
    "DK": ("Дания", "Denmark"),
    "DZ": ("Алжир", "Algeria"),
    "EG": ("Египет", "Egypt"),
    "ES": ("Испания", "Spain"),
    "FR": ("Франция", "France"),
    "GB": ("Великобритания", "United Kingdom"),
    "GH": ("Гана", "Ghana"),
    "HR": ("Хорватия", "Croatia"),
    "IT": ("Италия", "Italy"),
    "JP": ("Япония", "Japan"),
    "KR": ("Южная Корея", "South Korea"),
    "MA": ("Марокко", "Morocco"),
    "MX": ("Мексика", "Mexico"),
    "NG": ("Нигерия", "Nigeria"),
    "NL": ("Нидерланды", "Netherlands"),
    "NO": ("Норвегия", "Norway"),
    "PL": ("Польша", "Poland"),
    "PT": ("Португалия", "Portugal"),
    "RS": ("Сербия", "Serbia"),
    "SE": ("Швеция", "Sweden"),
    "SN": ("Сенегал", "Senegal"),
    "TR": ("Турция", "Turkey"),
    "RU": ("Россия", "Russia"),
    "UA": ("Украина", "Ukraine"),
    "BY": ("Беларусь", "Belarus"),
    "AT": ("Австрия", "Austria"),
    "CZ": ("Чехия", "Czechia"),
    "SK": ("Словакия", "Slovakia"),
    "HU": ("Венгрия", "Hungary"),
    "RO": ("Румыния", "Romania"),
    "BG": ("Болгария", "Bulgaria"),
    "GR": ("Греция", "Greece"),
    "FI": ("Финляндия", "Finland"),
    "IE": ("Ирландия", "Ireland"),
    "IS": ("Исландия", "Iceland"),
    "SI": ("Словения", "Slovenia"),
    "BA": ("Босния и Герцеговина", "Bosnia and Herzegovina"),
    "ME": ("Черногория", "Montenegro"),
    "MK": ("Северная Македония", "North Macedonia"),
    "AL": ("Албания", "Albania"),
    "CN": ("Китай", "China"),
    "IN": ("Индия", "India"),
    "BD": ("Бангладеш", "Bangladesh"),
    "TH": ("Таиланд", "Thailand"),
    "ID": ("Индонезия", "Indonesia"),
    "VN": ("Вьетнам", "Vietnam"),
    "GM": ("Гамбия", "Gambia"),
    "KE": ("Кения", "Kenya"),
    "ZA": ("ЮАР", "South Africa"),
    "UY": ("Уругвай", "Uruguay"),
    "PY": ("Парагвай", "Paraguay"),
    "PE": ("Перу", "Peru"),
    "EC": ("Эквадор", "Ecuador"),
    "US": ("США", "United States"),
    "NZ": ("Новая Зеландия", "New Zealand"),
    "TN": ("Тунис", "Tunisia"),
    "IR": ("Иран", "Iran"),
    "SA": ("Саудовская Аравия", "Saudi Arabia"),
    "KZ": ("Казахстан", "Kazakhstan"),
    "UZ": ("Узбекистан", "Uzbekistan"),
    "GE": ("Грузия", "Georgia"),
    "AM": ("Армения", "Armenia"),
    "MD": ("Молдова", "Moldova"),
    "AZ": ("Азербайджан", "Azerbaijan"),
    "LT": ("Литва", "Lithuania"),
    "LV": ("Латвия", "Latvia"),
    "EE": ("Эстония", "Estonia"),
    "CI": ("Кот-д’Ивуар", "Ivory Coast"),
    "ML": ("Мали", "Mali"),
    "BF": ("Буркина-Фасо", "Burkina Faso"),
    "GN": ("Гвинея", "Guinea"),
}
COUNTRIES = tuple(sorted(COUNTRY_LABELS))
NATIONALITY_POLICY = "nationality-v2"


def legacy_nationality(person_id: UUID) -> str:
    slot = int(md5(str(person_id).encode(), usedforsecurity=False).hexdigest()[:7], 16)
    return LEGACY_COUNTRIES[slot % len(LEGACY_COUNTRIES)]


def nationality(person_id: UUID) -> str:
    """New people only; existing people always use their persisted country_code."""
    slot = int.from_bytes(sha256(f"{NATIONALITY_POLICY}:{person_id}".encode()).digest(), "big")
    return COUNTRIES[slot % len(COUNTRIES)]


def flag(code: str | None) -> str:
    if code not in COUNTRY_LABELS:
        return "🌐"
    return "".join(chr(0x1F1E6 + ord(letter) - ord("A")) for letter in code)


def flag_name(name: str, code: str | None) -> str:
    return f"{flag(code)} {name}"


def country_label(code: str, language: str = "ru") -> str:
    return flag(code) + " " + COUNTRY_LABELS[code][language == "en"]
