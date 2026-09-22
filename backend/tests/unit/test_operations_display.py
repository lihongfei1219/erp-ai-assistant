import json

from app.analysis.analytics import execute_analysis
from app.integrations.feishu_analytics_cards import render_analysis
from app.schemas.analytics import AnalysisPlan


def test_feishu_uses_domain_titles_units_and_snapshot_time(multi_report):
    plan = AnalysisPlan(
        steps=[
            dict(
                domain="returns",
                kind="summary",
                start_date="2026-09-01",
                end_date_exclusive="2026-09-04",
            ),
            dict(
                domain="shipping",
                kind="product_ranking",
                metric="orders",
                start_date="2026-09-01",
                end_date_exclusive="2026-09-04",
            ),
            dict(
                domain="inventory",
                kind="summary",
                metric="stock",
                start_date="2026-09-16",
                end_date_exclusive="2026-09-17",
            ),
        ]
    )
    result = execute_analysis(multi_report, plan)
    card, plain = render_analysis(result)
    encoded = json.dumps(card, ensure_ascii=False)
    for label in ["退货概览", "销售出库商品排行", "库存概览", "盒", "瓶", "2026-09-16"]:
        assert label in encoded
    assert "有效销售金额" not in encoded
    from app.integrations.feishu_analytics_layouts import details

    for step, item in zip(plan.steps, result.results, strict=True):
        columns, _ = details(step, item, "CNY", 50)
        assert "销售金额" not in dict(columns).values()
    assert "有效订单" not in encoded
    assert "退货单据数" in encoded
    assert "不是成功退款" in plain


def test_stock_card_does_not_hide_unit_totals(multi_report):
    result = execute_analysis(
        multi_report,
        AnalysisPlan(
            steps=[
                dict(
                    domain="inventory",
                    kind="summary",
                    metric="stock",
                    start_date="2026-09-16",
                    end_date_exclusive="2026-09-17",
                )
            ]
        ),
    )
    card, _ = render_analysis(result)
    encoded = json.dumps(card, ensure_ascii=False)
    assert "15.0000" in encoded and "100.0000" in encoded
    assert card["header"]["title"]["content"] == "库存概览"


def test_document_details_keep_requested_amount_and_quantity(multi_report):
    from app.integrations.feishu_analytics_layouts import details

    plan = AnalysisPlan(
        steps=[
            dict(
                domain="returns",
                kind="list",
                metric="amount",
                start_date="2026-09-01",
                end_date_exclusive="2026-09-04",
            )
        ]
    )
    result = execute_analysis(multi_report, plan)
    columns, rows = details(plan.steps[0], result.results[0], "CNY", 50)
    assert {"amount", "unit", "quantity", "document_number"} <= {key for key, _ in columns}
    assert all(row["amount"] != "—" for row in rows)
