"""Explicit real-model evaluation; synthetic records, captured cards, no Feishu sends."""

import os

import pytest

from app.ai.analytics_agent import AnalyticsPlanner
from app.ai.model_client import load_model_settings
from app.analysis.sales import analyze_sales
from app.core.business_rules import load_business_rules
from app.integrations.feishu_sales import SalesBot
from app.schemas.sales import AnalysisWindow
from tests.unit.test_feishu_analytics import AnalyticsStore, ask
from tests.unit.test_feishu_sales import config

pytestmark = pytest.mark.skipif(
    os.getenv("ERP_RUN_MODEL_TESTS") != "1",
    reason="Explicit synthetic model evaluation only",
)


@pytest.fixture
def live_bot(extract, scope, source_as_of):
    report = analyze_sales(
        extract,
        AnalysisWindow(start="2026-09-01", end="2026-09-16"),
        scope,
        source_as_of=source_as_of,
        rules=load_business_rules(),
        synthetic=True,
    )
    settings = load_model_settings()
    assert settings.enabled, "Model must be configured for explicit evaluation"
    store = AnalyticsStore()
    bot = SalesBot(
        config, "ou_bot", store, lambda: report, analysis_planner=AnalyticsPlanner(settings)
    )
    return bot, store


@pytest.mark.parametrize(
    "question,kinds",
    [
        ("分析2026年9月1日至5号的销售数据，哪些药品卖的好", ["product_ranking"]),
        ("统计2026年9月1日至5日销售概览", ["summary"]),
        ("2026年9月1日至5日每天的销售趋势", ["trend"]),
        ("2026年9月1日至5日客户按订单数排行前5名", ["buyer_ranking"]),
        ("比较2026年9月3日至4日与2026年9月1日至2日的销售额，按商品拆解变化贡献", ["comparison"]),
        ("2026年9月1日至5日分析日波动线索", ["anomalies"]),
        ("2026年9月1日至5日每日销售趋势，并列出商品销售额前5名", ["trend", "product_ranking"]),
    ],
)
def test_real_model_feishu_result_types(live_bot, question, kinds):
    bot, store = live_bot
    row, card = ask(bot, store, question)
    assert [result["kind"] for result in row["result"].get("results", [])] == kinds
    assert "查询编号" in card


def test_real_model_feishu_followup_and_unsupported_conditions(live_bot):
    bot, store = live_bot
    row, _ = ask(bot, store, "分析2026年9月1日至5号的销售数据，哪些药品卖的好")
    assert row["result"]["results"][0]["rows"][0]["amount"] == "260.0000"
    row, card = ask(bot, store, "换成按订单数排，取前5名")
    assert row["result"]["plan"]["steps"][0]["metric"] == "orders"
    assert row["result"]["plan"]["steps"][0]["top_n"] == 5
    assert "未按药品类别筛选" in card
    row, _ = ask(bot, store, "2026年9月1日至5日只看药品，排除器械")
    assert row["result"]["unavailable"] and "results" not in row["result"]
