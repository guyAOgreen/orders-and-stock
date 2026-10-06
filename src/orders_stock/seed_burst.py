"""Seed a few products and submit a short burst of orders with duplicates.

The "tiny script or command" the brief asks for. Seeding writes the products
table directly, since there is no products API, and commits before the first
request. The burst goes over HTTP to a running API so the demonstration uses
the real surface. ``DATABASE_URL`` must point at the database that API uses.

Order references are deterministic, so re-running a batch submits the same
refs and every response is a duplicate; ``--batch`` picks a fresh set.
"""

import argparse
import re
import sys
from dataclasses import dataclass
from typing import TextIO

import httpx2
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from orders_stock.config import get_settings
from orders_stock.db import make_engine, make_session_factory
from orders_stock.models import Product
from orders_stock.orders.schemas import IDENTIFIER_MAX_LENGTH, ORDER_REF_PATTERN

DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_BATCH = "web"
REQUEST_TIMEOUT_SECONDS = 10.0

# Stock covers the documented burst and the demonstration comfortably; it is
# not replenished for unlimited batches, and re-seeding preserves it.
PRODUCTS: list[dict[str, str | int]] = [
    {"sku": "BAN-001", "name": "Bananas 1kg", "price_cents": 199, "stock": 500},
    {"sku": "MLK-002", "name": "Milk 2L", "price_cents": 2599, "stock": 300},
    {"sku": "APL-003", "name": "Apples 1kg", "price_cents": 349, "stock": 500},
    {"sku": "BRD-004", "name": "Bread 700g", "price_cents": 1899, "stock": 200},
]

# (ref suffix, customer, items). Suffixes 100045 and 100046 are repeated:
# one immediately after its original, one later in the burst.
BURST: list[tuple[str, str, list[dict[str, str | int]]]] = [
    ("100045", "cust-42", [{"sku": "BAN-001", "qty": 2}, {"sku": "APL-003", "qty": 1}]),
    ("100046", "cust-7", [{"sku": "MLK-002", "qty": 1}]),
    ("100045", "cust-42", [{"sku": "BAN-001", "qty": 2}, {"sku": "APL-003", "qty": 1}]),
    (
        "100047",
        "cust-13",
        [
            {"sku": "BAN-001", "qty": 1},
            {"sku": "MLK-002", "qty": 2},
            {"sku": "APL-003", "qty": 3},
        ],
    ),
    ("100048", "cust-7", [{"sku": "BRD-004", "qty": 2}]),
    ("100049", "cust-99", [{"sku": "APL-003", "qty": 4}, {"sku": "BRD-004", "qty": 1}]),
    ("100046", "cust-7", [{"sku": "MLK-002", "qty": 1}]),
    ("100050", "cust-42", [{"sku": "BAN-001", "qty": 6}]),
]


class BurstError(RuntimeError):
    """The API answered a burst request with an unexpected status."""


@dataclass
class BurstSummary:
    submitted: int = 0
    new: int = 0
    duplicate: int = 0


def seed_products(session: Session) -> int:
    """Insert the catalogue, skipping SKUs that exist. Returns the number inserted."""
    inserted = session.execute(
        insert(Product)
        .on_conflict_do_nothing(index_elements=[Product.sku])
        .returning(Product.sku),
        PRODUCTS,
    ).all()
    session.commit()
    return len(inserted)


def burst_refs(batch: str) -> list[str]:
    """The order refs a batch submits, in order; raises if any would be rejected."""
    refs = [f"{batch}-{suffix}" for suffix, _, _ in BURST]
    for ref in refs:
        if not re.fullmatch(ORDER_REF_PATTERN, ref) or len(ref) > IDENTIFIER_MAX_LENGTH:
            raise ValueError(
                f"{ref!r} is not a valid order_ref: letters, digits, '.', '_', "
                f"'~' and '-' only, at most {IDENTIFIER_MAX_LENGTH} characters"
            )
    return refs


def burst_orders(client: httpx2.Client, batch: str, out: TextIO) -> BurstSummary:
    """Submit the burst through ``client``, printing each outcome and a summary.

    Raises ``BurstError`` on a status other than 201 or 200; transport errors
    propagate as ``httpx2.RequestError``.
    """
    summary = BurstSummary()
    for ref, (_, customer_id, items) in zip(burst_refs(batch), BURST, strict=True):
        body = {"order_ref": ref, "customer_id": customer_id, "items": items}
        response = client.post("/orders", json=body)
        summary.submitted += 1
        if response.status_code == 201:
            summary.new += 1
            outcome = "new"
        elif response.status_code == 200:
            summary.duplicate += 1
            outcome = "duplicate"
        else:
            raise BurstError(
                f"{ref}: unexpected {response.status_code} {response.reason_phrase}: "
                f"{response.text}"
            )
        print(f"{ref} -> {response.status_code} {outcome}", file=out)
    print(
        f"Submitted {summary.submitted} orders: {summary.new} new, "
        f"{summary.duplicate} duplicates",
        file=out,
    )
    return summary


def _batch_label(value: str) -> str:
    try:
        burst_refs(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None
    return value


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="orders-stock-seed-burst",
        description="Seed a few products, then submit a short burst of orders "
        "that intentionally repeats some order_ref values.",
    )
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help=f"base URL of the running Orders API (default {DEFAULT_API_URL})",
    )
    parser.add_argument(
        "--batch",
        type=_batch_label,
        default=DEFAULT_BATCH,
        help="prefix for the order refs; change it to submit a fresh set "
        f"(default {DEFAULT_BATCH!r})",
    )
    phase = parser.add_mutually_exclusive_group()
    phase.add_argument("--seed-only", action="store_true", help="seed, do not burst")
    phase.add_argument("--burst-only", action="store_true", help="burst, do not seed")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out = sys.stdout

    if not args.burst_only:
        engine = make_engine(get_settings().database_url)
        try:
            with make_session_factory(engine)() as session:
                inserted = seed_products(session)
        finally:
            engine.dispose()
        existing = len(PRODUCTS) - inserted
        print(
            f"Seeded {len(PRODUCTS)} products ({inserted} new, {existing} existing)",
            file=out,
        )

    if not args.seed_only:
        try:
            with httpx2.Client(
                base_url=args.api_url, timeout=REQUEST_TIMEOUT_SECONDS
            ) as client:
                burst_orders(client, batch=args.batch, out=out)
        except httpx2.RequestError as exc:
            print(
                f"error: could not reach the API at {args.api_url}: {exc!r}. "
                "Is it running? Re-running the same batch is safe: accepted "
                "orders come back as duplicates.",
                file=sys.stderr,
            )
            return 1
        except BurstError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
