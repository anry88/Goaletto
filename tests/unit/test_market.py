from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from goaletto.bot.market_views import parse_market
from goaletto.bot.result_views import compact_id
from goaletto.domain.finances import MAX_MONEY
from goaletto.domain.market import (
    decode_price,
    encode_price,
    extended_deadline,
    market_window,
    minimum_bid,
)


def test_windows_prices_and_antisnipe_boundaries():
    first = datetime(2026, 10, 17, 18, tzinfo=UTC)
    end = first + timedelta(weeks=18)
    assert market_window(first, end, first - timedelta(days=4)).contains(first - timedelta(days=4))
    assert not market_window(first, end, first).contains(first)
    middle = market_window(first, end, first)
    assert middle.opens_at == datetime(2026, 11, 15, tzinfo=UTC)
    assert middle.contains(middle.opens_at)
    assert not middle.contains(middle.closes_at)
    assert market_window(first, end, middle.closes_at).kind == "offseason"
    assert minimum_bid(100000, None) == 100000
    assert minimum_bid(100000, 100000) == 105000
    assert extended_deadline(first, first - timedelta(hours=2)) == first
    assert extended_deadline(
        first, first - timedelta(hours=2) + timedelta(seconds=1)
    ) == first + timedelta(minutes=30)
    with pytest.raises(ValueError):
        extended_deadline(first, first)
    with pytest.raises(ValueError):
        minimum_bid(99999, None)


def test_money_callbacks_keep_both_full_identities_with_bigint():
    person, owner = uuid4(), uuid4()
    wire = f"mb:{compact_id(person)}:{compact_id(owner)}:{encode_price(MAX_MONEY)}"
    assert len(wire.encode()) <= 64
    assert parse_market(wire) == ("bid", person, owner, MAX_MONEY)
    for bad in ("00", "-1", "ZZ", "zzzzzzzzzzzzz"):
        with pytest.raises(ValueError):
            decode_price(bad)
