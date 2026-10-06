"""Applying pending stock work. See "Applying stock" in SOLUTION.md.

``process_one`` owns one transaction per work row: claim, decrement, mark
processed, commit. ``run_worker`` polls it until told to stop. Both read
``stock_work`` and write ``products`` only.
"""

import logging
import threading

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from orders_stock.models import Product, StockWork

log = logging.getLogger("orders_stock.worker")


class StockWorkError(RuntimeError):
    """A work row could not be applied; the transaction was rolled back."""


def process_one(session: Session) -> bool:
    """Apply the oldest pending work row in one transaction.

    Returns whether a row was processed. The transaction is ended on every
    path: committed on success, rolled back on failure or when no row is
    pending. A failure is logged with the row's ids and re-raised.
    """
    work_id = order_id = None
    try:
        claimed = session.execute(
            select(StockWork.id, StockWork.order_id, StockWork.items)
            .where(StockWork.status == "pending")
            .order_by(StockWork.created_at, StockWork.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).one_or_none()
        if claimed is None:
            session.rollback()
            return False
        work_id, order_id, items = claimed
        for sku, qty in _quantities(items):
            _decrement(session, sku, qty)
        session.execute(
            update(StockWork)
            .where(StockWork.id == work_id)
            .values(status="processed", processed_at=func.now())
        )
        session.commit()
    except BaseException:
        session.rollback()
        log.exception(
            "failed to apply stock work id=%s order_id=%s; rolled back",
            work_id,
            order_id,
        )
        raise
    log.info("applied stock for order_id=%s: %s", order_id, items)
    return True


def run_worker(
    session_factory: sessionmaker[Session],
    poll_interval: float,
    stop: threading.Event,
) -> None:
    """Process work rows until ``stop`` is set.

    Rows are processed back to back while any are pending, one session and
    one transaction each. When the queue is empty, or an attempt fails, the
    loop waits ``poll_interval`` seconds (or until stopped) and tries again.
    A failed row stays pending and is simply retried; see "Stock worker" in
    SOLUTION.md for the limitation that implies.
    """
    while not stop.is_set():
        try:
            with session_factory() as session:
                processed = process_one(session)
        except Exception:
            # Already logged with the row's ids by process_one.
            processed = False
        if not processed:
            stop.wait(poll_interval)


def _quantities(items: object) -> list[tuple[str, int]]:
    """The payload's ``{sku, qty}`` entries, sorted by SKU so that workers
    applying overlapping orders take product row locks in the same order."""
    if not isinstance(items, list):
        raise StockWorkError(f"work payload is not a list: {items!r}")
    quantities: list[tuple[str, int]] = []
    for entry in items:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("sku"), str)
            or not isinstance(entry.get("qty"), int)
            or entry["qty"] <= 0
        ):
            raise StockWorkError(f"malformed work payload entry: {entry!r}")
        quantities.append((entry["sku"], entry["qty"]))
    return sorted(quantities)


def _decrement(session: Session, sku: str, qty: int) -> None:
    # Atomic in PostgreSQL: concurrent decrements of one SKU serialise on the
    # row lock and each re-reads the current value, so none is lost.
    updated = session.execute(
        update(Product)
        .where(Product.sku == sku)
        .values(stock=Product.stock - qty)
        .returning(Product.sku)
    ).one_or_none()
    if updated is None:
        raise StockWorkError(f"no product row for SKU {sku!r}")
