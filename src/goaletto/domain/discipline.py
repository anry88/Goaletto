"""Tournament discipline, supplied facts only; no clocks or persistence."""

from dataclasses import dataclass


@dataclass(frozen=True)
class DisciplineChange:
    yellows: int
    bans: int
    awarded: int
    served: bool


def settle_cards(
    carried: int, bans: int, *, yellows: int, dismissed: bool, serving: bool, completed: bool
) -> DisciplineChange:
    if carried not in (0, 1) or bans < 0 or yellows not in (0, 1, 2):
        raise ValueError("Invalid disciplinary balance")
    if yellows == 2 and not dismissed:
        raise ValueError("Two yellows require dismissal")
    if serving and (bans == 0 or yellows or dismissed):
        raise ValueError("Suspended player cannot participate")
    # A sending-off is one ban. Its match's yellows do not also accumulate;
    # an earlier single caution remains in the tournament balance.
    total = carried if dismissed else carried + yellows
    awarded = 1 if dismissed else total // 2
    served = serving and completed
    return DisciplineChange(total % 2, bans + awarded - int(served), awarded, served)
