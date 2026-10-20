import pytest

from goaletto.domain.club_valuation import bankrupt, facilities


def test_exact_debt_boundary_and_all_facility_levels():
    assert not bankrupt(-500_000, 500_000)
    assert bankrupt(-500_001, 500_000)
    assert not bankrupt(0, 0)
    assert facilities(5000, 1) == (300_000, 100_000)
    assert facilities(10000, 2) == (600_000, 300_000)
    assert facilities(50000, 5) == (8_400_000, 4_300_000)
    with pytest.raises(ValueError):
        facilities(9000, 1)
