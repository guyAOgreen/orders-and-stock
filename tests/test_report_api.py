"""The daily report endpoint, exercised over HTTP against PostgreSQL.

Orders are inserted directly with explicit acceptance times so the UTC day
boundary can be pinned exactly.
"""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, update
from sqlalchemy.orm import Session, sessionmaker

from orders_stock.models import Order, OrderItem, Product

DAY = date(2026, 10, 7)
MIDNIGHT = datetime(2026, 10, 7, tzinfo=UTC)
LAST_MICROSECOND = MIDNIGHT + timedelta(days=1) - timedelta(microseconds=1)

PRODUCTS = [
    {"sku": "APL-003", "name": "Apples 1kg", "price_cents": 349, "stock": 500},
    {"sku": "BAN-001", "name": "Bananas 1kg", "price_cents": 199, "stock": 500},
    {"sku": "MLK-002", "name": "Milk 2L", "price_cents": 2599, "stock": 300},
]


def _order(ref: str, accepted_at: datetime, items: list[tuple[str, int, int]]) -> Order:
    return Order(
        order_ref=ref,
        customer_id="cust-1",
        total_cents=sum(qty * price for _, qty, price in items),
        accepted_at=accepted_at,
        items=[
            OrderItem(sku=sku, quantity=qty, unit_price_cents=price)
            for sku, qty, price in items
        ],
    )


@pytest.fixture
def products(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        session.add_all(Product(**p) for p in PRODUCTS)
        session.commit()


@pytest.fixture
def orders_across_two_days(
    session_factory: sessionmaker[Session], products: None
) -> None:
    """Three orders on DAY, including both ends of the day, and one either side."""
    with session_factory() as session:
        session.add_all(
            [
                _order(
                    "prev-1",
                    MIDNIGHT - timedelta(microseconds=1),
                    [("BAN-001", 5, 199)],
                ),
                # Exactly midnight belongs to DAY; a multi-item order.
                _order("day-1", MIDNIGHT, [("BAN-001", 2, 199), ("MLK-002", 1, 2599)]),
                _order("day-2", MIDNIGHT + timedelta(hours=12), [("BAN-001", 3, 199)]),
                _order("day-3", LAST_MICROSECOND, [("APL-003", 1, 349)]),
                # Exactly the next midnight belongs to the next day.
                _order("next-1", MIDNIGHT + timedelta(days=1), [("BAN-001", 1, 199)]),
            ]
        )
        session.commit()


def _report(client: TestClient, day: date | str) -> dict[str, Any]:
    response = client.get("/reports/daily", params={"date": str(day)})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_report_counts_orders_revenue_and_units_for_the_utc_day(
    client: TestClient, orders_across_two_days: None
) -> None:
    report = _report(client, DAY)

    assert report["date"] == "2026-10-07"
    assert report["total_orders"] == 3
    assert report["revenue_cents"] == (2 * 199 + 2599) + 3 * 199 + 349
    assert report["units_sold"] == [
        {"sku": "APL-003", "units": 1},
        {"sku": "BAN-001", "units": 5},
        {"sku": "MLK-002", "units": 1},
    ]


def test_orders_at_the_next_midnight_count_for_the_next_day(
    client: TestClient, orders_across_two_days: None
) -> None:
    report = _report(client, DAY + timedelta(days=1))

    assert report["total_orders"] == 1
    assert report["revenue_cents"] == 199
    assert report["units_sold"] == [{"sku": "BAN-001", "units": 1}]


def test_current_stock_lists_every_product_and_generated_at_is_aware(
    client: TestClient, orders_across_two_days: None
) -> None:
    report = _report(client, DAY)

    assert report["current_stock"] == [
        {"sku": "APL-003", "name": "Apples 1kg", "stock": 500},
        {"sku": "BAN-001", "name": "Bananas 1kg", "stock": 500},
        {"sku": "MLK-002", "name": "Milk 2L", "stock": 300},
    ]
    generated_at = datetime.fromisoformat(report["generated_at"])
    assert generated_at.tzinfo is not None
    assert abs(datetime.now(UTC) - generated_at) < timedelta(minutes=1)


def test_a_day_with_no_orders_returns_zeros_and_current_stock(
    client: TestClient, orders_across_two_days: None
) -> None:
    report = _report(client, date(2020, 1, 1))

    assert report["total_orders"] == 0
    assert report["revenue_cents"] == 0
    assert report["units_sold"] == []
    assert len(report["current_stock"]) == len(PRODUCTS)


def test_sales_are_fixed_while_current_stock_is_live(
    client: TestClient,
    orders_across_two_days: None,
    session_factory: sessionmaker[Session],
) -> None:
    before = _report(client, DAY)
    with session_factory() as session:
        session.execute(
            update(Product).where(Product.sku == "BAN-001").values(stock=123)
        )
        session.commit()

    after = _report(client, DAY)

    for key in ("total_orders", "revenue_cents", "units_sold"):
        assert after[key] == before[key]
    assert after["current_stock"][1] == {
        "sku": "BAN-001",
        "name": "Bananas 1kg",
        "stock": 123,
    }


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"date": ""},
        {"date": "yesterday"},
        {"date": "2026-13-01"},
        {"date": "9999-12-31"},
    ],
)
def test_missing_invalid_or_unrepresentable_date_is_422(
    client: TestClient, products: None, params: dict[str, str]
) -> None:
    response = client.get("/reports/daily", params=params)

    assert response.status_code == 422


@pytest.fixture
def commit_an_order_between_the_report_queries(
    session_factory: sessionmaker[Session],
) -> Iterator[list[str]]:
    """After the report's first aggregate query, commit another order for DAY
    from a separate connection. Listens at the Engine class level because the
    app under test builds its own engine."""
    fired: list[str] = []

    def interleave(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        if fired or not statement.lstrip().upper().startswith("SELECT COUNT("):
            return
        fired.append(statement)
        with session_factory() as session:
            session.add(
                _order("late-1", MIDNIGHT + timedelta(hours=1), [("MLK-002", 7, 2599)])
            )
            session.commit()

    event.listen(Engine, "after_cursor_execute", interleave)
    try:
        yield fired
    finally:
        event.remove(Engine, "after_cursor_execute", interleave)


def test_report_queries_share_one_snapshot(
    client: TestClient,
    orders_across_two_days: None,
    commit_an_order_between_the_report_queries: list[str],
) -> None:
    report = _report(client, DAY)

    assert commit_an_order_between_the_report_queries, "the hook never fired"
    # The late order committed after the count was taken, so it must be
    # absent from the units as well; a Read Committed report would include it.
    assert report["total_orders"] == 3
    assert {"sku": "MLK-002", "units": 1} in report["units_sold"]
    assert report["revenue_cents"] == (2 * 199 + 2599) + 3 * 199 + 349

    # And it is there once the report runs again.
    assert _report(client, DAY)["total_orders"] == 4
