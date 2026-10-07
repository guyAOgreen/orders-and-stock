"""Response schema for the daily report."""

import datetime as dt

from pydantic import BaseModel, Field


class UnitsSold(BaseModel):
    sku: str
    units: int


class StockLevel(BaseModel):
    sku: str
    name: str
    stock: int


class DailyReportResponse(BaseModel):
    date: dt.date = Field(
        description="The UTC calendar day reported, by acceptance time"
    )
    total_orders: int
    revenue_cents: int = Field(
        description="Sum of the accepted orders' totals, at the prices in effect "
        "when each was accepted"
    )
    units_sold: list[UnitsSold] = Field(
        description="Units per SKU across that day's orders; only SKUs with sales"
    )
    current_stock: list[StockLevel] = Field(
        description="Every product's stock level in the same database snapshot as "
        "the other figures, not a per-day snapshot; may still lag accepted orders "
        "the worker has not applied"
    )
    generated_at: dt.datetime = Field(
        description="Start time of the report's database transaction, which "
        "read all of the above from one snapshot"
    )
