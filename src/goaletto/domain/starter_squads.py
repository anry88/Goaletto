"""Versioned free starter profiles; unchanged draws preserve identity and earned growth."""

from random import Random

LEGACY_STARTER_POLICY = "starter-squad-v1"
STARTER_POLICY = "starter-squad-v2"

_LEGACY = {
    "GK": (14, 6, 8, 6, 6, 8, 11, 8),
    "defence": (1, 12, 9, 10, 7, 9, 11, 10),
    "midfield": (1, 9, 12, 12, 9, 10, 11, 10),
    "attack": (1, 7, 10, 10, 12, 10, 11, 12),
}
_CURRENT = {
    "GK": (7, 3, 4, 3, 3, 4, 6, 4),
    "defence": (1, 6, 5, 5, 4, 5, 6, 5),
    "midfield": (1, 5, 6, 6, 5, 5, 6, 5),
    "attack": (1, 4, 5, 5, 6, 5, 6, 6),
}


def starter_skills(position: str, rng: Random, policy: str = STARTER_POLICY) -> tuple[int, ...]:
    if policy not in (LEGACY_STARTER_POLICY, STARTER_POLICY):
        raise ValueError("Unknown starter skill policy")
    if position == "GK":
        role, primary = "GK", 0
    elif position in ("DL", "DC", "DR"):
        role, primary = "defence", 1
    elif position in ("ML", "MC", "MR"):
        role, primary = "midfield", 2 if position == "MC" else 3
    elif position == "FC":
        role, primary = "attack", 4
    else:
        raise ValueError("Unsupported starter position")
    values = list((_LEGACY if policy == LEGACY_STARTER_POLICY else _CURRENT)[role])
    left, right = rng.sample([i for i in range(1, 8) if i != primary], 2)
    values[left] += 1
    values[right] -= 1
    return (*values, rng.randint(9, 11), rng.randint(9, 11))


def rebase_skills(
    current: tuple[int, ...], old_base: tuple[int, ...], new_base: tuple[int, ...]
) -> tuple[int, ...]:
    if len(current) != 8 or len(old_base) != 8 or len(new_base) != 8:
        raise ValueError("Rebase needs eight playing skills")
    if any(not 1 <= v <= 20 for row in (current, old_base, new_base) for v in row):
        raise ValueError("Invalid skill profile")
    if any(now < old for now, old in zip(current, old_base, strict=True)):
        raise ValueError("Current skills are below the proven original baseline")
    return tuple(now - old + new for now, old, new in zip(current, old_base, new_base, strict=True))
