"""The seed and burst command, against PostgreSQL and the API.

The burst function takes an httpx client, so the tests drive it through
FastAPI's ``TestClient`` (an httpx client) and the command drives it over a
real connection to ``--api-url``.
"""

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from orders_stock.models import Order, Product, StockWork
from orders_stock.seed_burst import (
    PRODUCTS,
    BurstError,
    burst_orders,
    main,
    seed_products,
)


def _counts(session_factory: sessionmaker[Session]) -> tuple[int, int, int]:
    """(products, orders, stock_work) row counts."""

    def count(table: type[Product | Order | StockWork]) -> int:
        return session.execute(select(func.count()).select_from(table)).scalar_one()

    with session_factory() as session:
        return count(Product), count(Order), count(StockWork)


def _stock(session_factory: sessionmaker[Session], sku: str) -> int:
    with session_factory() as session:
        return session.execute(
            select(Product.stock).where(Product.sku == sku)
        ).scalar_one()


# --- Seeding --------------------------------------------------------------


def test_seed_inserts_the_catalogue_once(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        inserted = seed_products(session)

    assert inserted == len(PRODUCTS)
    assert _counts(session_factory)[0] == len(PRODUCTS)
    assert {p["sku"] for p in PRODUCTS} >= {"BAN-001", "APL-003"}


def test_reseeding_neither_duplicates_products_nor_resets_stock(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        seed_products(session)
        session.execute(update(Product).where(Product.sku == "BAN-001").values(stock=7))
        session.commit()

    with session_factory() as session:
        inserted = seed_products(session)

    assert inserted == 0
    assert _counts(session_factory)[0] == len(PRODUCTS)
    assert _stock(session_factory, "BAN-001") == 7


# --- The burst ------------------------------------------------------------


@pytest.fixture
def seeded(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        seed_products(session)


def test_burst_creates_one_order_per_distinct_ref(
    client: TestClient, seeded: None, session_factory: sessionmaker[Session]
) -> None:
    out = io.StringIO()

    summary = burst_orders(client, batch="web", out=out)

    assert (summary.submitted, summary.new, summary.duplicate) == (8, 6, 2)
    assert _counts(session_factory)[1:] == (6, 6)
    lines = out.getvalue().splitlines()
    assert sum("201 new" in line for line in lines) == 6
    assert sum("200 duplicate" in line for line in lines) == 2
    assert lines[-1] == "Submitted 8 orders: 6 new, 2 duplicates"


def test_rerunning_the_same_batch_is_all_duplicates_and_writes_nothing(
    client: TestClient, seeded: None, session_factory: sessionmaker[Session]
) -> None:
    burst_orders(client, batch="web", out=io.StringIO())
    before = _counts(session_factory)

    summary = burst_orders(client, batch="web", out=io.StringIO())

    assert (summary.submitted, summary.new, summary.duplicate) == (8, 0, 8)
    assert _counts(session_factory) == before


def test_a_different_batch_creates_a_fresh_set_of_orders(
    client: TestClient, seeded: None, session_factory: sessionmaker[Session]
) -> None:
    burst_orders(client, batch="web", out=io.StringIO())

    summary = burst_orders(client, batch="demo2", out=io.StringIO())

    assert (summary.submitted, summary.new, summary.duplicate) == (8, 6, 2)
    assert _counts(session_factory)[1:] == (12, 12)


def test_seeded_stock_covers_the_burst(
    client: TestClient, seeded: None, session_factory: sessionmaker[Session]
) -> None:
    burst_orders(client, batch="web", out=io.StringIO())

    with session_factory() as session:
        # Units per SKU across the burst, read from the work payloads.
        ordered: dict[str, int] = {}
        for row in session.execute(select(StockWork.items)).scalars():
            for entry in row:
                ordered[entry["sku"]] = ordered.get(entry["sku"], 0) + entry["qty"]
        stock = dict(session.execute(select(Product.sku, Product.stock)).all())

    assert ordered, "the burst ordered nothing"
    for sku, units in ordered.items():
        assert stock[sku] > units, f"{sku}: stock {stock[sku]} for {units} units"


def test_an_unexpected_response_is_an_error(client: TestClient) -> None:
    # Nothing seeded, so the API rejects the first order with 422.
    with pytest.raises(BurstError, match="422"):
        burst_orders(client, batch="web", out=io.StringIO())


# --- The command ----------------------------------------------------------


def test_invalid_batch_label_is_rejected_before_any_work(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--batch", "bad/label", "--api-url", "http://127.0.0.1:1"])

    assert exc.value.code == 2
    assert "batch" in capsys.readouterr().err


def test_seed_only_and_burst_only_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--seed-only", "--burst-only"])

    assert exc.value.code == 2


def test_unreachable_api_exits_non_zero_with_a_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["--burst-only", "--api-url", "http://127.0.0.1:1"])

    assert exit_code == 1
    assert "127.0.0.1:1" in capsys.readouterr().err
