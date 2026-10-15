"""Explicit-calendar auction rules. No clocks, persistence or random draws."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from goaletto.domain.finances import MAX_MONEY, money

MIN_PRICE = 100_000
BID_STEP = 5_000
AUCTION_HOURS = 48
POLICY = "auction-v1"


@dataclass(frozen=True)
class MarketWindow:
    opens_at: datetime
    closes_at: datetime
    kind: str

    def contains(self, at: datetime) -> bool:
        return self.opens_at <= at < self.closes_at


def utc(at: datetime) -> None:
    if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() != timedelta(0):
        raise ValueError("Market timestamps must be UTC")


def market_window(first: datetime, end: datetime, at: datetime) -> MarketWindow:
    for value in (first, end, at):
        utc(value)
    if end <= first:
        raise ValueError("Invalid season calendar")
    from goaletto.domain.game_cycle import midnight, season_epoch

    opening = midnight(season_epoch(first))
    if at < opening:
        return MarketWindow(opening - timedelta(days=8), opening, "offseason")
    if at >= end:
        return MarketWindow(end, end + timedelta(days=8), "offseason")
    middle = MarketWindow(opening + timedelta(days=32), opening + timedelta(days=36), "midseason")
    if at < middle.closes_at:
        return middle
    return MarketWindow(end, end + timedelta(days=8), "offseason")


def minimum_bid(initial: int, current: int | None) -> int:
    money(initial)
    if initial < MIN_PRICE:
        raise ValueError("Minimum listing price is 100K")
    return initial if current is None else money(current + BID_STEP)


def extended_deadline(ends: datetime, at: datetime) -> datetime:
    utc(ends)
    utc(at)
    if at >= ends:
        raise ValueError("Auction already closed")
    return ends + timedelta(minutes=30) if ends - at < timedelta(hours=2) else ends


def encode_price(value: int) -> str:
    money(value)
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while value:
        value, remainder = divmod(value, 36)
        out = digits[remainder] + out
    return out or "0"


def decode_price(value: str) -> int:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 13
        or not value.isascii()
        or any(c not in "0123456789abcdefghijklmnopqrstuvwxyz" for c in value)
    ):
        raise ValueError("Invalid price encoding")
    parsed = int(value, 36)
    if parsed > MAX_MONEY or encode_price(parsed) != value:
        raise ValueError("Invalid price encoding")
    return parsed
