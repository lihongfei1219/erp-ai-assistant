import json
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.analysis.sales import analyze_sales
from app.analysis.sales_insights import sales_summary, sales_trend
from app.analysis.sales_query import QueryRequest, QueryUnavailable
from app.core.business_rules import load_business_rules, policy_fingerprint
from app.schemas.sales import DataScope


@pytest.fixture
def operating_report(extract, window, scope, source_as_of):
    return analyze_sales(
        extract, window, scope, source_as_of=source_as_of,
        rules=load_business_rules(), synthetic=True,
    )


@pytest.fixture
def query():
    return QueryRequest(start_date="2026-09-01", end_date_exclusive="2026-09-04")


@pytest.mark.parametrize("analyze", [sales_summary, sales_trend])
def test_totals_use_effective_orders_and_distinct_buyers(operating_report, query, analyze):
    before = operating_report.model_dump(mode="json")
    result = analyze(operating_report, query)
    assert result.total_amount == Decimal("300.0000")
    assert result.order_count == 2
    assert result.buyer_count == 1
    assert result.average_order_amount == Decimal("150.0000")
    assert result.product_count == 2
    assert result.request == query
    assert result.source_as_of == operating_report.metadata.source_as_of
    assert result.source_kind == "synthetic"
    assert result.currency == "CNY"
    assert result.business_timezone == "Asia/Shanghai"
    assert result.included_statuses == operating_report.operating.policy.included_statuses
    assert result.metric_version == operating_report.metadata.metric_version
    assert result.policy_id == operating_report.operating.policy.policy_id
    assert result.policy_fingerprint == operating_report.operating.policy_fingerprint
    serialized = json.loads(result.model_dump_json())
    assert Decimal(serialized["total_amount"]) == Decimal("300.0000")
    assert operating_report.model_dump(mode="json") == before


def test_summary_counts_only_excluded_orders_in_requested_period(operating_report, query):
    assert sales_summary(operating_report, query).excluded_order_count == 1
    first_day = QueryRequest(start_date="2026-09-01", end_date_exclusive="2026-09-02")
    assert sales_summary(operating_report, first_day).excluded_order_count == 0


def test_trend_includes_complete_zero_days_and_deduplicates_each_day(operating_report, query):
    result = sales_trend(operating_report, query)
    assert [(p.day, p.order_amount, p.order_count, p.buyer_count) for p in result.daily] == [
        (date(2026, 9, 1), Decimal("100.0000"), 1, 1),
        (date(2026, 9, 2), Decimal("200.0000"), 1, 1),
        (date(2026, 9, 3), Decimal("0.0000"), 0, 0),
    ]
    assert result.buyer_count == 1


@pytest.mark.parametrize("analyze", [sales_summary, sales_trend])
def test_complete_empty_period_has_zero_counts_and_no_average(operating_report, analyze):
    result = analyze(
        operating_report,
        QueryRequest(start_date="2026-09-03", end_date_exclusive="2026-09-04"),
    )
    assert result.total_amount == Decimal("0.0000")
    assert (result.order_count, result.buyer_count, result.product_count) == (0, 0, 0)
    assert result.average_order_amount is None


def test_same_day_orders_and_repeated_lines_do_not_inflate_counts(operating_report, query):
    first, second, excluded = operating_report.evidence
    repeated = first.lines[0].model_copy(update={"line_id": 3, "amount": Decimal("30")})
    first = first.model_copy(update={"lines": [
        first.lines[0].model_copy(update={"amount": Decimal("30")}),
        first.lines[1], repeated,
    ]})
    second = second.model_copy(update={"created_at": first.created_at})
    report = operating_report.model_copy(update={"evidence": [first, second, excluded]})
    result = sales_trend(report, query)
    assert (result.order_count, result.buyer_count, result.product_count) == (2, 1, 2)
    assert result.daily[0].order_amount == Decimal("300")
    assert (result.daily[0].order_count, result.daily[0].buyer_count) == (2, 1)


@pytest.mark.parametrize("analyze", [sales_summary, sales_trend])
def test_uses_snapshot_status_policy(operating_report, query, analyze):
    operating = operating_report.operating
    policy = operating.policy.model_copy(update={"included_statuses": ("已退回",)})
    report = operating_report.model_copy(update={"operating": operating.model_copy(update={
        "policy": policy, "policy_fingerprint": policy_fingerprint(policy),
    })})
    result = analyze(report, query)
    assert result.total_amount == Decimal("90")
    assert (result.order_count, result.buyer_count, result.product_count) == (1, 1, 1)
    assert result.average_order_amount == Decimal("90")


@pytest.mark.parametrize("analyze", [sales_summary, sales_trend])
def test_aware_order_timestamp_uses_business_date(operating_report, analyze):
    first = operating_report.evidence[0].model_copy(update={
        "created_at": datetime.fromisoformat("2026-08-31T16:00:00+00:00"),
    })
    report = operating_report.model_copy(update={
        "evidence": [first, *operating_report.evidence[1:]],
    })
    result = analyze(
        report, QueryRequest(start_date="2026-09-01", end_date_exclusive="2026-09-02"),
    )
    assert result.total_amount == Decimal("100")
    if analyze is sales_trend:
        assert result.daily[0].day == date(2026, 9, 1)
        assert result.daily[0].order_amount == Decimal("100")


@pytest.mark.parametrize("analyze", [sales_summary, sales_trend])
@pytest.mark.parametrize("source, allowed", [
    ("2026-09-03T16:00:00+00:00", True),
    ("2026-09-03T15:59:59+00:00", False),
    ("2026-09-04T00:00:00", False),
])
def test_partial_day_is_never_filled_with_zero(operating_report, query, source, allowed, analyze):
    report = operating_report.model_copy(update={
        "metadata": operating_report.metadata.model_copy(update={
            "source_as_of": datetime.fromisoformat(source),
        }),
    })
    if allowed:
        assert analyze(report, query).total_amount == Decimal("300")
    else:
        with pytest.raises(QueryUnavailable, match="完整区间"):
            analyze(report, query)


@pytest.mark.parametrize("analyze", [sales_summary, sales_trend])
@pytest.mark.parametrize("start, end", [
    ("2026-08-31", "2026-09-04"), ("2026-09-01", "2026-09-05"),
])
def test_missing_coverage_is_rejected_instead_of_clipped(operating_report, analyze, start, end):
    with pytest.raises(QueryUnavailable, match="完整区间"):
        analyze(operating_report, QueryRequest(start_date=start, end_date_exclusive=end))


@pytest.mark.parametrize("analyze", [sales_summary, sales_trend])
@pytest.mark.parametrize("change", [
    "scope", "policy", "fingerprint", "sql", "reconciliation", "missing", "duplicate",
    "line_total", "total", "blank_buyer", "blank_product", "duplicate_line", "outside_window",
])
def test_corrupt_or_unauthorized_snapshot_is_rejected(operating_report, query, analyze, change):
    report = operating_report
    if change == "scope":
        report = report.model_copy(update={"metadata": report.metadata.model_copy(update={
            "scope": DataScope(buyer_codes=("BUYER-A",)),
        })})
    elif change == "policy":
        report = report.model_copy(update={"operating": None})
    elif change == "fingerprint":
        report = report.model_copy(update={"operating": report.operating.model_copy(update={
            "policy_fingerprint": "corrupt",
        })})
    elif change in {"sql", "reconciliation"}:
        field = "sql_control_totals_match" if change == "sql" else "reconciled_orders"
        report = report.model_copy(update={"quality": report.quality.model_copy(update={
            field: False if change == "sql" else 0,
        })})
    elif change == "total":
        report = report.model_copy(update={"summary": report.summary.model_copy(update={
            "order_amount": Decimal("1"),
        })})
    else:
        evidence = list(report.evidence)
        first = evidence[0]
        if change == "missing":
            evidence.pop()
        elif change == "duplicate":
            evidence[1] = first
        elif change == "line_total":
            evidence[0] = first.model_copy(update={"amount": Decimal("101")})
        elif change == "blank_buyer":
            evidence[0] = first.model_copy(update={"buyer_code": " "})
        elif change == "blank_product":
            evidence[0] = first.model_copy(update={"lines": [
                first.lines[0].model_copy(update={"product_code": " "}), first.lines[1],
            ]})
        elif change == "duplicate_line":
            evidence[0] = first.model_copy(update={"lines": [first.lines[0], first.lines[0]]})
        elif change == "outside_window":
            evidence[0] = first.model_copy(update={"created_at": datetime(2026, 8, 31)})
        report = report.model_copy(update={"evidence": evidence})
    with pytest.raises(QueryUnavailable):
        analyze(report, query)
