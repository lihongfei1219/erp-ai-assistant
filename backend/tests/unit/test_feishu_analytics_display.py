"""Presentation contracts: facts, native component limits, and graceful reduction."""

import json
from copy import deepcopy

import pytest

from app.analysis.analytics import execute_analysis
from app.integrations.feishu_analytics_cards import MAX_CARD_BYTES, render_analysis
from app.schemas.analytics import AnalysisPlan
from tests.unit.test_feishu_analytics import setup as setup
from tests.unit.test_feishu_analytics import step
from tests.unit.test_feishu_cards import walk


def response(setup, kind="product_ranking", **kwargs):
    args = step(kind, **kwargs)
    if kind == "comparison":
        args.update(
            start_date="2026-09-02",
            comparison_start_date="2026-09-01",
            comparison_end_date_exclusive="2026-09-02",
        )
    return execute_analysis(setup[0], AnalysisPlan(steps=[args]))


@pytest.mark.parametrize(
    "kind", ["summary", "product_ranking", "buyer_ranking", "trend", "comparison", "anomalies"]
)
def test_native_components_preserve_facts_and_root_table_rule(setup, kind):
    result = response(setup, kind)
    before = deepcopy(result.model_dump())
    card, text = render_analysis(result)
    assert card["schema"] == "2.0"
    roots = card["body"]["elements"]
    tables = [node for node in walk(card) if node.get("tag") == "table"]
    assert all(table in roots and table["page_size"] <= 5 for table in tables)
    assert all(column["data_type"] == "text" for table in tables for column in table["columns"])
    assert any(node.get("tag") == "collapsible_panel" and not node["expanded"] for node in roots)
    assert result.model_dump() == before
    assert result.run_id in text and result.run_id in json.dumps(card)
    assert "300.00" in json.dumps(card) or kind == "comparison"


def test_orders_ranking_chart_uses_orders_and_unique_categories(setup):
    result = response(setup, metric="orders")
    result.results[0].rows[1]["name"] = result.results[0].rows[0]["name"]
    card, _ = render_analysis(result)
    chart = next(n for n in walk(card) if n.get("tag") == "chart")["chart_spec"]
    values = chart["data"]["values"]
    assert [v["value"] for v in values] == [2, 1]
    assert len({v["label"] for v in values}) == 2
    assert "订单数" in json.dumps(chart, ensure_ascii=False)


def test_exact_display_large_negative_values_and_zero_base(setup):
    result = response(setup, "comparison")
    result.results[0].totals.update(
        amount="9007199254740993.1234",
        previous_amount="0.0000",
        delta="9007199254740993.1234",
        change_rate=None,
    )
    result.results[0].rows[0].update(
        amount="9007199254740993.1234", previous_amount="9007199254740994.1234", delta="-1.0000"
    )
    card, _ = render_analysis(result)
    raw = json.dumps(card, ensure_ascii=False)
    assert "9,007,199,254,740,993.12" in raw
    assert "-1.00" in raw and "比较期为零" in raw


def test_many_rows_paginate_without_truncating_small_trend(setup):
    result = response(setup, "trend")
    result.results[0].rows[:] = [
        dict(result.results[0].rows[0], day=f"2026-09-{day:02}") for day in range(1, 21)
    ]
    card, text = render_analysis(result)
    table = next(n for n in walk(card) if n.get("tag") == "table")
    assert len(table["rows"]) == 20 and table["page_size"] == 5
    assert "卡片节选" not in text
    chart = next(n for n in walk(card) if n.get("tag") == "chart")["chart_spec"]
    assert len(chart["data"]["values"]) == 20


def test_combined_budget_and_chart_reduction_are_explicit(setup):
    result = response(setup)
    result.results[0].rows[:] = [
        dict(result.results[0].rows[0], name="长商品名" * 100, code=str(i)) for i in range(50)
    ]
    result.results[:] = result.results * 6
    result.plan.steps[:] = result.plan.steps * 6
    card, text = render_analysis(result)
    nodes = list(walk(card))
    assert sum(n.get("tag") == "table" for n in nodes) <= 5
    assert sum(n.get("tag") == "chart" for n in nodes) <= 2
    assert sum("tag" in n for n in nodes) <= 200
    assert len(json.dumps(card, ensure_ascii=False).encode()) <= MAX_CARD_BYTES
    assert "卡片节选" in text and "卡片节选" in json.dumps(card, ensure_ascii=False)


@pytest.mark.parametrize("kind", ["summary", "anomalies", "future_kind"])
def test_empty_and_unknown_layout_have_readable_fallback(setup, kind):
    result = response(setup)
    result.results[0] = result.results[0].model_copy(update={"kind": kind, "rows": []})
    card, _ = render_analysis(result)
    assert "没有" in json.dumps(card, ensure_ascii=False)
    assert not any(n.get("tag") == "chart" for n in walk(card))


def test_chart_unsafe_coordinates_keep_exact_table_values(setup):
    result = response(setup)
    result.results[0].rows[0]["amount"] = "9007199254740993.1234"
    card, _ = render_analysis(result)
    assert not any(n.get("tag") == "chart" for n in walk(card))
    table = next(n for n in walk(card) if n.get("tag") == "table")
    assert table["rows"][0]["amount"] == "¥ 9,007,199,254,740,993.12"


def test_reduced_trend_has_no_misleading_partial_chart(setup):
    from app.integrations.feishu_analytics_cards import _card

    result = response(setup, "trend")
    result.results[0].rows.reverse()
    card = _card(result, row_limit=1, charts=True)
    assert not any(n.get("tag") == "chart" for n in walk(card))
    table = next(n for n in walk(card) if n.get("tag") == "table")
    assert table["rows"][0]["day"] == "2026-09-01"
    assert "卡片节选前 1 行" in json.dumps(card, ensure_ascii=False)
