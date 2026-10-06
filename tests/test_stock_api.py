"""The current-stock endpoint, exercised over HTTP against PostgreSQL."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from orders_stock.models import Product
from orders_stock.stock.worker import process_one


@pytest.fixture
def products(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        session.add(
            Product(sku="BAN-001", name="Bananas 1kg", price_cents=199, stock=50)
        )
        session.add(Product(sku="A/B.1", name="Odd SKU", price_cents=1, stock=3))
        session.commit()


def test_current_stock_for_a_sku(client: TestClient, products: None) -> None:
    response = client.get("/stock", params={"sku": "BAN-001"})

    assert response.status_code == 200
    assert response.json() == {"sku": "BAN-001", "name": "Bananas 1kg", "stock": 50}


def test_sku_is_a_query_parameter_so_any_catalogue_sku_is_readable(
    client: TestClient, products: None
) -> None:
    response = client.get("/stock", params={"sku": "A/B.1"})

    assert response.status_code == 200
    assert response.json()["stock"] == 3


def test_unknown_sku_is_404(client: TestClient, products: None) -> None:
    response = client.get("/stock", params={"sku": "NOPE-000"})

    assert response.status_code == 404
    assert response.json() == {"detail": "Unknown SKU"}


@pytest.mark.parametrize(
    "params", [{}, {"sku": ""}, {"sku": "x" * 129}, {"sku": "BAN\x00001"}]
)
def test_missing_or_invalid_sku_is_422(
    client: TestClient, products: None, params: dict[str, str]
) -> None:
    response = client.get("/stock", params=params)

    assert response.status_code == 422


def test_stock_reflects_applied_orders(
    client: TestClient, products: None, session_factory: sessionmaker[Session]
) -> None:
    order = {
        "order_ref": "web-1",
        "customer_id": "cust-42",
        "items": [{"sku": "BAN-001", "qty": 2}],
    }
    assert client.post("/orders", json=order).status_code == 201
    assert client.get("/stock", params={"sku": "BAN-001"}).json()["stock"] == 50

    with session_factory() as session:
        assert process_one(session) is True

    assert client.get("/stock", params={"sku": "BAN-001"}).json()["stock"] == 48
    assert client.get("/orders/web-1").json()["stock_status"] == "applied"
