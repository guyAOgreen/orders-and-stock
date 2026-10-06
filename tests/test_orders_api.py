"""The Orders intake API, exercised over HTTP against PostgreSQL."""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from orders_stock.models import Order, OrderItem, Product, StockWork

BANANAS = {"sku": "BAN-001", "name": "Bananas 1kg", "price_cents": 199, "stock": 50}
MILK = {"sku": "MLK-002", "name": "Milk 2L", "price_cents": 2599, "stock": 20}


def _seed_products(session_factory: sessionmaker[Session], *products: Any) -> None:
    with session_factory() as session:
        session.add_all(Product(**p) for p in products)
        session.commit()


def _order_request(**overrides: Any) -> dict[str, Any]:
    request: dict[str, Any] = {
        "order_ref": "web-100045",
        "customer_id": "cust-42",
        "items": [{"sku": "BAN-001", "qty": 2}, {"sku": "MLK-002", "qty": 1}],
    }
    request.update(overrides)
    return request


@pytest.fixture
def products(session_factory: sessionmaker[Session]) -> None:
    _seed_products(session_factory, BANANAS, MILK)


def test_new_order_is_created_with_total_from_current_prices(
    client: TestClient, products: None
) -> None:
    response = client.post("/orders", json=_order_request())

    assert response.status_code == 201
    body = response.json()
    assert body["order_ref"] == "web-100045"
    assert body["customer_id"] == "cust-42"
    assert body["items"] == [
        {"sku": "BAN-001", "qty": 2, "unit_price_cents": 199},
        {"sku": "MLK-002", "qty": 1, "unit_price_cents": 2599},
    ]
    assert body["total_cents"] == 2 * 199 + 2599
    assert body["status"] == "accepted"
    assert body["stock_status"] == "pending"
    accepted_at = datetime.fromisoformat(body["accepted_at"])
    assert accepted_at.tzinfo is not None


def test_new_order_persists_items_and_one_pending_work_row(
    client: TestClient, products: None, session_factory: sessionmaker[Session]
) -> None:
    client.post("/orders", json=_order_request())

    with session_factory() as session:
        order = session.execute(
            select(Order).where(Order.order_ref == "web-100045")
        ).scalar_one()
        items = (
            session.execute(
                select(OrderItem)
                .where(OrderItem.order_id == order.id)
                .order_by(OrderItem.sku)
            )
            .scalars()
            .all()
        )
        work = session.execute(
            select(StockWork).where(StockWork.order_id == order.id)
        ).scalar_one()

    assert order.total_cents == 2997
    assert [(i.sku, i.quantity, i.unit_price_cents) for i in items] == [
        ("BAN-001", 2, 199),
        ("MLK-002", 1, 2599),
    ]
    assert work.status == "pending"
    assert work.items == [
        {"sku": "BAN-001", "qty": 2},
        {"sku": "MLK-002", "qty": 1},
    ]


def test_order_details_are_returned_by_order_ref(
    client: TestClient, products: None
) -> None:
    created = client.post("/orders", json=_order_request()).json()

    response = client.get("/orders/web-100045")

    assert response.status_code == 200
    assert response.json() == created


def test_later_price_change_does_not_alter_stored_total(
    client: TestClient, products: None, session_factory: sessionmaker[Session]
) -> None:
    client.post("/orders", json=_order_request())

    with session_factory() as session:
        session.execute(
            text("UPDATE products SET price_cents = 999 WHERE sku = 'BAN-001'")
        )
        session.commit()

    body = client.get("/orders/web-100045").json()
    assert body["total_cents"] == 2997
    assert body["items"][0]["unit_price_cents"] == 199


def test_repeated_order_ref_returns_existing_order_with_200(
    client: TestClient, products: None
) -> None:
    first = client.post("/orders", json=_order_request())

    second = client.post("/orders", json=_order_request())

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json() == first.json()


def test_repeated_order_ref_writes_nothing(
    client: TestClient, products: None, session_factory: sessionmaker[Session]
) -> None:
    client.post("/orders", json=_order_request())
    client.post("/orders", json=_order_request())

    with session_factory() as session:
        orders = session.execute(select(func.count()).select_from(Order)).scalar_one()
        items = session.execute(
            select(func.count()).select_from(OrderItem)
        ).scalar_one()
        work = session.execute(select(func.count()).select_from(StockWork)).scalar_one()

    assert (orders, items, work) == (1, 2, 1)


def test_repeated_order_ref_with_different_payload_returns_original_order(
    client: TestClient, products: None
) -> None:
    # Idempotency is keyed by order_ref alone: the repeat is not validated or
    # compared, even when it names a SKU that does not exist.
    original = client.post("/orders", json=_order_request()).json()

    repeat = client.post(
        "/orders",
        json=_order_request(
            customer_id="someone-else", items=[{"sku": "NOPE-000", "qty": 9}]
        ),
    )

    assert repeat.status_code == 200
    assert repeat.json() == original


def test_structurally_invalid_repeat_is_still_rejected(
    client: TestClient, products: None
) -> None:
    client.post("/orders", json=_order_request())

    repeat = client.post("/orders", json=_order_request(items=[]))

    assert repeat.status_code == 422


def test_unknown_sku_is_rejected_and_nothing_is_written(
    client: TestClient, products: None, session_factory: sessionmaker[Session]
) -> None:
    response = client.post(
        "/orders",
        json=_order_request(
            items=[{"sku": "BAN-001", "qty": 1}, {"sku": "NOPE-000", "qty": 1}]
        ),
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Unknown SKU(s): NOPE-000"}
    with session_factory() as session:
        assert (
            session.execute(select(func.count()).select_from(Order)).scalar_one() == 0
        )


def test_unknown_order_is_not_found(client: TestClient) -> None:
    response = client.get("/orders/does-not-exist")

    assert response.status_code == 404


@pytest.mark.parametrize(
    "request_body",
    [
        _order_request(items=[]),
        _order_request(items=[{"sku": "BAN-001", "qty": 0}]),
        _order_request(items=[{"sku": "BAN-001", "qty": 2_147_483_648}]),
        _order_request(items=[{"sku": "", "qty": 1}]),
        _order_request(order_ref=""),
        _order_request(order_ref="web/100045"),
        _order_request(order_ref="web 100045"),
        _order_request(order_ref="w" * 129),
        _order_request(order_ref="."),
        _order_request(order_ref=".."),
        {"customer_id": "cust-42", "items": [{"sku": "BAN-001", "qty": 1}]},
    ],
    ids=[
        "no-items",
        "zero-quantity",
        "quantity-beyond-integer",
        "blank-sku",
        "blank-order-ref",
        "slash-in-order-ref",
        "space-in-order-ref",
        "order-ref-too-long",
        "dot-order-ref",
        "dot-dot-order-ref",
        "missing-order-ref",
    ],
)
def test_structurally_invalid_requests_are_rejected(
    client: TestClient, products: None, request_body: dict[str, Any]
) -> None:
    response = client.post("/orders", json=request_body)

    assert response.status_code == 422


def test_order_ref_with_url_safe_punctuation_round_trips(
    client: TestClient, products: None
) -> None:
    order_ref = "WEB_2026.10-06~a"
    created = client.post("/orders", json=_order_request(order_ref=order_ref))

    fetched = client.get(f"/orders/{order_ref}")

    assert created.status_code == 201
    assert fetched.status_code == 200
    assert fetched.json()["order_ref"] == order_ref


def test_repeated_sku_in_one_request_is_merged_into_one_line(
    client: TestClient, products: None, session_factory: sessionmaker[Session]
) -> None:
    response = client.post(
        "/orders",
        json=_order_request(
            items=[{"sku": "BAN-001", "qty": 2}, {"sku": "BAN-001", "qty": 3}]
        ),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["items"] == [{"sku": "BAN-001", "qty": 5, "unit_price_cents": 199}]
    assert body["total_cents"] == 5 * 199
    with session_factory() as session:
        work = session.execute(select(StockWork)).scalar_one()
    assert work.items == [{"sku": "BAN-001", "qty": 5}]


def test_merged_quantity_beyond_integer_range_is_rejected(
    client: TestClient, products: None
) -> None:
    response = client.post(
        "/orders",
        json=_order_request(
            items=[
                {"sku": "BAN-001", "qty": 2_000_000_000},
                {"sku": "BAN-001", "qty": 2_000_000_000},
            ]
        ),
    )

    assert response.status_code == 422


def test_stock_status_is_applied_once_the_work_row_is_processed(
    client: TestClient, products: None, session_factory: sessionmaker[Session]
) -> None:
    client.post("/orders", json=_order_request())

    # Issue 8's worker will do this; here the row is marked by hand.
    with session_factory() as session:
        session.execute(
            text("UPDATE stock_work SET status = 'processed', processed_at = :now"),
            {"now": datetime.now(UTC)},
        )
        session.commit()

    body = client.get("/orders/web-100045").json()
    assert body["status"] == "accepted"
    assert body["stock_status"] == "applied"
