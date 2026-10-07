"""HTTP route for the daily report."""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Query

from orders_stock.api.deps import SessionDep
from orders_stock.reports.schemas import DailyReportResponse, StockLevel, UnitsSold
from orders_stock.reports.service import daily_report

router = APIRouter(prefix="/reports", tags=["reports"])

# The report needs day + 1 for its interval, so the last representable date
# is a validation error rather than an overflow.
LAST_REPORTABLE_DATE = dt.date.max - dt.timedelta(days=1)

ReportDate = Annotated[
    dt.date,
    Query(
        le=LAST_REPORTABLE_DATE,
        description="UTC calendar day, YYYY-MM-DD, by order acceptance time",
    ),
]


@router.get("/daily")
def read_daily_report(date: ReportDate, session: SessionDep) -> DailyReportResponse:
    report = daily_report(session, date)
    return DailyReportResponse(
        date=report.day,
        total_orders=report.total_orders,
        revenue_cents=report.revenue_cents,
        units_sold=[UnitsSold(sku=u.sku, units=u.units) for u in report.units_sold],
        current_stock=[
            StockLevel(sku=s.sku, name=s.name, stock=s.stock)
            for s in report.current_stock
        ],
        generated_at=report.generated_at,
    )
