from pathlib import Path

import pytest

from app import report

DATA = Path(__file__).parent / "data" / "orders.csv"


@pytest.fixture
def orders():
    return report.clean(report.load_orders(DATA))


def test_regions_are_forward_filled(orders):
    assert orders["region"].isna().sum() == 0
    assert orders.loc[orders["order_id"] == 2, "region"].item() == "north"
    assert orders.loc[orders["order_id"] == 9, "region"].item() == "north"


def test_text_fields_are_stripped(orders):
    assert set(orders["rep"]) == {"alice", "bob", "carol"}
    assert "south" in set(orders["region"])


def test_refunds_are_zeroed(orders):
    assert orders.loc[orders["status"] == "refunded", "amount"].sum() == 0


def test_monthly_revenue(orders):
    monthly = report.monthly_revenue(orders)
    assert list(monthly.values) == [200.0, 300.0, 180.0]
    assert monthly.index[-1].day == 31


def test_revenue_by_region(orders):
    by_region = report.revenue_by_region(orders)
    assert by_region.index[0] == "north"
    assert by_region["north"] == 380.0


def test_top_rep(orders):
    rep, total = report.top_rep(orders)
    assert (rep, total) == ("bob", 340.0)


def test_weekly_orders(orders):
    weekly = report.weekly_orders(orders)
    assert weekly.sum() == 9
