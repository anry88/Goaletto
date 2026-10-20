"""Display-only badges. Never put these strings in simulation snapshots."""


def club_label(name: str, vip: bool, *, table: bool = False) -> str:
    return name + (" 👑" if table else " 👑 VIP") if vip else name
