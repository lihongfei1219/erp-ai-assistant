"""Deterministic sales summaries and daily trends from validated snapshot evidence."""

from datetime import date, datetime, timedelta
from decimal import Decimal

from app.analysis.sales_query import QueryRequest, _local_date, _validate_report
from app.schemas.sales import DailyPoint, OrderEvidence, SalesReport, StrictModel


class _SalesTotals(StrictModel):
    request: QueryRequest
    total_amount: Decimal
    order_count: int
    buyer_count: int
    average_order_amount: Decimal | None
    product_count: int
    source_as_of: datetime
    source_kind: str
    currency: str
    business_timezone: str
    included_statuses: tuple[str, ...]
    metric_version: str
    policy_id: str
    policy_fingerprint: str


class SalesSummaryResult(_SalesTotals):
    excluded_order_count: int


class SalesTrendResult(_SalesTotals):
    daily: list[DailyPoint]


def _result_values(
    report: SalesReport, request: QueryRequest, orders: list[OrderEvidence],
) -> dict:
    policy = report.operating.policy
    total = sum((order.amount for order in orders), Decimal("0.0000"))
    return {
        "request": request,
        "total_amount": total,
        "order_count": len(orders),
        "buyer_count": len({order.buyer_code for order in orders}),
        "average_order_amount": total / len(orders) if orders else None,
        "product_count": len({line.product_code for order in orders for line in order.lines}),
        "source_as_of": report.metadata.source_as_of,
        "source_kind": report.metadata.source_kind,
        "currency": policy.currency,
        "business_timezone": policy.business_timezone,
        "included_statuses": policy.included_statuses,
        "metric_version": report.metadata.metric_version,
        "policy_id": policy.policy_id,
        "policy_fingerprint": report.operating.policy_fingerprint,
    }


def sales_summary(report: SalesReport, request: QueryRequest) -> SalesSummaryResult:
    """Aggregate the requested complete interval using the snapshot's status policy."""
    tz = _validate_report(report, request)
    period_orders = [
        order for order in report.evidence
        if request.start_date <= _local_date(order.created_at, tz) < request.end_date_exclusive
    ]
    orders = [
        order for order in period_orders
        if order.status in report.operating.policy.included_statuses
    ]
    return SalesSummaryResult(
        **_result_values(report, request, orders),
        excluded_order_count=len(period_orders) - len(orders),
    )


def sales_trend(report: SalesReport, request: QueryRequest) -> SalesTrendResult:
    """Produce every validated complete day; deduplicate buyers within each day."""
    tz = _validate_report(report, request)
    days: dict[date, list[OrderEvidence]] = {
        request.start_date + timedelta(days=offset): []
        for offset in range((request.end_date_exclusive - request.start_date).days)
    }
    orders = []
    for order in report.evidence:
        day = _local_date(order.created_at, tz)
        if day in days and order.status in report.operating.policy.included_statuses:
            orders.append(order)
            days[day].append(order)
    daily = [
        DailyPoint(
            day=day,
            order_amount=sum((order.amount for order in day_orders), Decimal("0.0000")),
            order_count=len(day_orders),
            buyer_count=len({order.buyer_code for order in day_orders}),
        )
        for day, day_orders in days.items()
    ]
    return SalesTrendResult(**_result_values(report, request, orders), daily=daily)
