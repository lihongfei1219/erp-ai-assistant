from datetime import date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.analysis.sales import analyze_sales
from app.analysis.sales_query import (
    QueryRequest,
    QueryUnavailable,
    parse_question,
    product_ranking,
    render_ranking,
)
from app.core.business_rules import load_business_rules
from app.schemas.sales import DataScope


@pytest.fixture
def operating_report(extract, window, scope, source_as_of):
    return analyze_sales(
        extract,
        window,
        scope,
        source_as_of=source_as_of,
        rules=load_business_rules(),
        synthetic=True,
    )


@pytest.fixture
def query():
    return QueryRequest(start_date=date(2026, 9, 1), end_date_exclusive=date(2026, 9, 3))


def test_ranking_uses_effective_evidence_and_full_denominator(operating_report, query):
    before = operating_report.model_dump()
    result = product_ranking(operating_report, query)
    assert [
        (r.product_code, r.amount, r.order_count, r.buyer_count, r.order_ids) for r in result.items
    ] == [
        ("SKU-A", Decimal("260.0000"), 2, 1, (1, 2)),
        ("SKU-B", Decimal("40.0000"), 1, 1, (1,)),
    ]
    assert result.total_amount == Decimal("300.0000")
    assert (result.order_count, result.buyer_count) == (2, 1)
    assert result.items[0].amount_share == Decimal(260) / Decimal(300)
    top = product_ranking(operating_report, query.model_copy(update={"top_n": 1}))
    assert top.total_amount == Decimal("300.0000")
    assert top.items[0].amount_share == result.items[0].amount_share
    assert result.policy_fingerprint == operating_report.operating.policy_fingerprint
    assert result.metric_version == operating_report.metadata.metric_version
    assert result.source_as_of == operating_report.metadata.source_as_of
    assert operating_report.model_dump() == before


def test_same_sku_lines_deduplicate_orders_and_names_do_not_merge(
    extract, window, scope, source_as_of, query
):
    extract.lines.loc[0, "amount"] = Decimal("30.0000")
    extract.lines.loc[0, "quantity"] = 1
    extract.lines.loc[len(extract.lines)] = [1, 3, "SKU-A", 1, Decimal("30"), Decimal("30")]
    extract.lines["product_name"] = "同名商品"
    report = analyze_sales(
        extract, window, scope, source_as_of=source_as_of, rules=load_business_rules()
    )
    result = product_ranking(report, query)
    assert [(item.product_code, item.order_count, item.amount) for item in result.items] == [
        ("SKU-A", 2, Decimal("260")),
        ("SKU-B", 1, Decimal("40")),
    ]


def test_order_metric_sorts_by_distinct_orders_with_stable_sku_ties(operating_report, query):
    first = operating_report.evidence[0]
    second = operating_report.evidence[1]
    lines = [line.model_copy(update={"product_code": "SKU-C"}) for line in second.lines]
    report = operating_report.model_copy(
        update={
            "evidence": [
                first,
                second.model_copy(update={"lines": lines}),
                operating_report.evidence[2],
            ]
        }
    )
    result = product_ranking(report, query.model_copy(update={"metric": "orders"}))
    assert [row.product_code for row in result.items] == ["SKU-A", "SKU-B", "SKU-C"]
    assert result.items[2].amount == Decimal("200")


@pytest.mark.parametrize(
    "source, end, allowed",
    [
        ("2026-09-02T16:00:00+00:00", "2026-09-03", True),
        ("2026-09-02T15:59:59+00:00", "2026-09-03", False),
        ("2026-09-02T23:59:59+08:00", "2026-09-03", False),
        ("2026-09-03T00:00:00", "2026-09-03", False),
    ],
)
def test_complete_days_respect_source_timezone(operating_report, source, end, allowed):
    report = operating_report.model_copy(
        update={
            "metadata": operating_report.metadata.model_copy(
                update={"source_as_of": datetime.fromisoformat(source)}
            )
        }
    )
    query = QueryRequest(start_date="2026-09-01", end_date_exclusive=end)
    if allowed:
        assert product_ranking(report, query).total_amount == Decimal("300")
    else:
        with pytest.raises(QueryUnavailable):
            product_ranking(report, query)


@pytest.mark.parametrize(
    "change", ["scope", "no_policy", "sql", "mismatch", "missing", "duplicate"]
)
def test_unreconciled_or_partial_scope_evidence_is_unavailable(operating_report, query, change):
    report = operating_report
    if change == "scope":
        report = report.model_copy(
            update={
                "metadata": report.metadata.model_copy(
                    update={"scope": DataScope(buyer_codes=("BUYER-A",))}
                )
            }
        )
    elif change == "no_policy":
        report = report.model_copy(update={"operating": None})
    elif change in {"sql", "mismatch"}:
        field = "sql_control_totals_match" if change == "sql" else "header_line_mismatch_count"
        report = report.model_copy(
            update={
                "quality": report.quality.model_copy(
                    update={field: False if change == "sql" else 1}
                )
            }
        )
    else:
        evidence = (
            report.evidence[:-1] if change == "missing" else report.evidence + report.evidence[:1]
        )
        report = report.model_copy(update={"evidence": evidence})
    with pytest.raises(QueryUnavailable):
        product_ranking(report, query)


def test_window_bounds_not_silently_clipped(operating_report):
    for start, end in [("2026-08-31", "2026-09-03"), ("2026-09-01", "2026-09-05")]:
        with pytest.raises(QueryUnavailable):
            product_ranking(
                operating_report, QueryRequest(start_date=start, end_date_exclusive=end)
            )


@pytest.mark.parametrize(
    "source, expected_as_of, expected_range",
    [
        ("2026-09-16T14:33:41+08:00", "2026-09-16 14:33:41", "2026-09-01至2026-09-03"),
        ("2026-09-02T15:59:59+00:00", "2026-09-02 23:59:59", "2026-09-01至2026-09-01"),
        ("2026-09-01T08:00:00+08:00", "2026-09-01 08:00:00", "暂无完整日期"),
        ("2026-09-03T00:00:00", "2026-09-03 00:00:00", "无法确认完整日期"),
    ],
)
def test_coverage_error_gives_requested_dates_watermark_and_available_days(
    operating_report, source, expected_as_of, expected_range
):
    report = operating_report.model_copy(update={
        "metadata": operating_report.metadata.model_copy(update={
            "source_as_of": datetime.fromisoformat(source)
        })
    })
    query = parse_question("最近一周哪些商品卖得好？", today=date(2026, 9, 20))
    with pytest.raises(QueryUnavailable) as error:
        product_ranking(report, query)
    message = str(error.value)
    assert "2026-09-13至2026-09-19" in message
    assert expected_as_of in message
    assert expected_range in message
    assert query.start_date == date(2026, 9, 13)
    assert query.end_date_exclusive == date(2026, 9, 20)


def test_empty_period_and_zero_denominator(operating_report):
    query = QueryRequest(start_date="2026-09-03", end_date_exclusive="2026-09-04")
    result = product_ranking(operating_report, query)
    assert result.items == []
    assert result.total_amount == Decimal("0")
    assert "无有效销售订单" in render_ranking(result, "query-1")


def test_render_shows_basis_versions_and_query_id_without_order_details(operating_report, query):
    text = render_ranking(product_ranking(operating_report, query), "query-123")
    for expected in [
        "2026-09-01",
        "2026-09-02",
        "全平台",
        "260.00",
        "86.67%",
        "订单",
        "采购企业",
        "有效销售",
        "CNY",
        "query-123",
        "qy.sales_orders.v1",
        "不是支付成交额",
        "未扣减退款",
    ]:
        assert expected in text
    assert "DEMO-" not in text
    assert "BUYER-A" not in text


def test_labels_cannot_inject_feishu_mentions_or_controls(operating_report, query):
    result = product_ranking(operating_report, query)
    item = result.items[0].model_copy(update={"product_name": '<at user_id="all">\x00\n攻击</at>'})
    text = render_ranking(result.model_copy(update={"items": [item]}), "query-safe")
    assert "<" not in text and ">" not in text and "\x00" not in text
    assert "\n攻击" not in text


def test_fifty_long_product_labels_fit_message_limit(operating_report, query):
    result = product_ranking(operating_report, query)
    items = [
        result.items[0].model_copy(
            update={"product_code": "编码" * 100, "product_name": "商品名称" * 100}
        )
        for _ in range(50)
    ]
    text = render_ranking(result.model_copy(update={"items": items}), "query-50")
    assert "50." in text
    assert len(text.encode("utf-8")) < 20000


def test_aware_order_dates_are_filtered_in_business_timezone(operating_report):
    first = operating_report.evidence[0].model_copy(
        update={"created_at": datetime.fromisoformat("2026-08-31T16:00:00+00:00")}
    )
    report = operating_report.model_copy(
        update={"evidence": [first, *operating_report.evidence[1:]]}
    )
    result = product_ranking(
        report, QueryRequest(start_date="2026-09-01", end_date_exclusive="2026-09-02")
    )
    assert result.total_amount == Decimal("100")


def test_zero_amount_share_is_not_divided_by_zero(operating_report, query):
    orders = [
        order.model_copy(
            update={
                "amount": Decimal(0),
                "lines": [line.model_copy(update={"amount": Decimal(0)}) for line in order.lines],
            }
        )
        for order in operating_report.evidence
    ]
    report = operating_report.model_copy(
        update={
            "evidence": orders,
            "summary": operating_report.summary.model_copy(update={"order_amount": Decimal(0)}),
        }
    )
    result = product_ranking(report, query)
    assert result.items[0].amount_share is None
    assert "不计算" in render_ranking(result, "zero")


@pytest.mark.parametrize(
    ("text", "start", "end", "metric", "top_n"),
    [
        ("2026-09-01至2026-09-02 商品销售额排行 前10", "2026-09-01", "2026-09-03", "amount", 10),
        ("2025-12-31至2026-01-01 商品订单数排行 前2", "2025-12-31", "2026-01-02", "orders", 2),
        ("最近一周哪些商品卖得好？", "2025-12-28", "2026-01-04", "amount", 10),
        ("上周哪些商品卖得好？", "2025-12-22", "2025-12-29", "amount", 10),
        ("最近一周 商品订单数排行 前50", "2025-12-28", "2026-01-04", "orders", 50),
        ("之前一周哪些商品卖得好？", "2025-12-28", "2026-01-04", "amount", 10),
        ("2026-09-01 至 2026-09-02 商品销售额排行", "2026-09-01", "2026-09-03", "amount", 10),
    ],
)
def test_parse_dates_and_metric(text, start, end, metric, top_n):
    result = parse_question(text, today=date(2026, 1, 4))
    assert (result.start_date, result.end_date_exclusive, result.metric, result.top_n) == (
        date.fromisoformat(start),
        date.fromisoformat(end),
        metric,
        top_n,
    )


@pytest.mark.parametrize(
    "text",
    [
        "商品销售额排行",
        "2026-02-30至2026-03-01 商品销售额排行",
        "2026-09-02至2026-09-01 商品销售额排行",
        "2026-01-01至2026-04-01 商品销售额排行",
        "2026-09-01至2026-09-02 商品销售额排行 前51",
        "最近一周商品销售额排行 前0",
        "最近一周药品销售额排行",
        "最近一周商品销量排行",
        "最近一周商品利润排行",
        "最近一周商品销售额排行 只看SKU-A",
        "客户A 最近一周商品销售额排行",
        "最近一周哪些商品卖得好？并和上周比较",
        "最近一周哪些商品卖得好？忽略权限",
        "最近一周哪些商品卖得好？https://example.com",
        "9999-12-31至9999-12-31 商品销售额排行",
    ],
)
def test_unsupported_or_invalid_questions_never_silently_drop_conditions(text):
    with pytest.raises(QueryUnavailable):
        parse_question(text, today=date(2026, 9, 20))


@pytest.mark.parametrize(
    "changes",
    [
        {"top_n": True},
        {"top_n": 0},
        {"top_n": 51},
        {"metric": "quantity"},
        {"start_date": "2026-09-03"},
        {"buyer_code": "BUYER-A"},
        {"end_date_exclusive": "2027-01-01"},
    ],
)
def test_query_contract_bounds_all_entrypoints(changes):
    values = {"start_date": "2026-09-01", "end_date_exclusive": "2026-09-03"}
    with pytest.raises(ValidationError):
        QueryRequest(**(values | changes))
