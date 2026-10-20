"""Local club-name policy. Cosmetic only; no IO or clock reads."""

import re

# Deliberately bounded local vocabulary, not a claim of universal moderation.
BLOCKED = frozenset(
    {
        "fuck",
        "fucker",
        "fucking",
        "motherfucker",
        "shit",
        "bitch",
        "cunt",
        "nigger",
        "nigga",
        "faggot",
        "nazis",
        "hitler",
        "blyad",
        "blyat",
        "pizda",
        "pizdec",
        "khuy",
        "huy",
        "suka",
        "yebat",
        "ebat",
    }
)
LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t"})


class InvalidClubName(ValueError):
    pass


def validate_name(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 200
        or not re.fullmatch(r"[A-Za-z0-9 .'-]+", value)
    ):
        raise InvalidClubName("characters")
    name = " ".join(value.split(" "))
    name = re.sub(" +", " ", name).strip()
    if not 2 <= len(name) <= 30 or not re.search(r"[A-Za-z]", name):
        raise InvalidClubName("length")
    folded = name.lower().translate(LEET)
    tokens = re.findall("[a-z]+", folded)
    joined = "".join(tokens)
    if (
        any(token in BLOCKED for token in tokens)
        or joined in BLOCKED
        or any(word in joined for word in ("fuck", "nigger", "faggot", "pizda", "pizdec"))
    ):
        raise InvalidClubName("words")
    return name
