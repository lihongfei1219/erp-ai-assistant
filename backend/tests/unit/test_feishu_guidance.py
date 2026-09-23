"""Guidance is a faithful channel presentation, not a second intent parser."""

import json
from datetime import timedelta

from app.analysis.operations import domain_dates
from app.integrations.feishu_guidance import coverage_text, help_text, render_guidance
from app.integrations.feishu_sales import SalesBot
from tests.unit.test_feishu_analytics import AnalyticsStore, ask
from tests.unit.test_feishu_sales import config


def payload(domain="sales", **extra):
    return {
        "status": "needs_input",
        "dialogue_turn": {
            "status": "needs_input",
            "understood_summary": "保留商品限定和其他组合目标",
            "draft": {"intents": [
                {"id": "goal-a", "label": "销售概览", "fields": {"domain": "sales"}},
                {"id": "goal-b", "label": "待确认目标", "fields": {"domain": domain}},
            ]},
            "clarification": {
                "id": "issue-a", "intent_id": "goal-b", "field": "time",
                "kind": "missing", "question": "想查哪一天？",
            },
            "choices": [
                {"id": "picker", "label": "自己选择日期", "action": {"kind": "date_range"}},
                {"id": "bound-choice", "label": "使用备份日期", "action": {"kind": "edit"}},
            ],
            "available_dates": {"start": "2026-09-02", "end_exclusive": "2026-09-04"},
            **extra,
        },
    }


def test_dates_use_affected_goal_and_inclusive_end_without_a_fake_picker():
    source = payload()
    before = json.dumps(source)
    result, text = render_guidance(source)
    assert "2026-09-02 至 2026-09-03" in text
    assert "2026-09-04" not in text
    assert "自己选择日期" not in text
    assert "1. 使用备份日期" in text and "2." not in text
    assert "再次 @我" in text
    assert "保留商品限定和其他组合目标" in text
    assert "conversation_token" not in json.dumps(result)
    assert json.dumps(source) == before


def test_inventory_guidance_does_not_advertise_a_daily_range():
    result, text = render_guidance(payload("inventory"))
    assert "可用库存快照日期：2026-09-02" in text
    assert "不是实时或当日日末库存" in text
    assert "可用完整日期" not in text
    assert result["semantic_status"] == "needs_input"


def test_ambiguous_number_is_not_invited_again_and_preserves_plain_text():
    hostile = "<at id=all>所有人</at> [链接](https://example.invalid)"
    source = payload(repeated_clarification=True, understood_summary=hostile)
    source.update(number_reply_allowed=False, notice="请用文字说明选择")
    result, text = render_guidance(source)
    assert "回复编号" not in text and "1." not in text
    assert "• 使用备份日期" in text and "不用重写整个问题" in text
    nodes = result["reply_card"]["elements"]
    content = [node["text"] for node in nodes if node["tag"] == "div"]
    assert all(node["tag"] == "plain_text" for node in content)
    assert any(hostile in node["content"] for node in content)
    assert text.startswith("请用文字说明选择")


def test_help_and_coverage_reach_bot_without_calling_model(multi_report):
    class NoModel:
        async def interpret(self, request):
            raise AssertionError("Help must not invoke a model")

    store = AnalyticsStore()
    bot = SalesBot(config, "ou_bot", store, lambda: multi_report, analysis_planner=NoModel())
    _, text = ask(bot, store, "帮助")
    assert "不需要按固定格式" in text
    assert "模型暂不可用时，可使用固定指令：" in text
    for label in ("销售：", "退货：", "销售出库：", "库存："):
        assert label in text
    assert "尚未接入" not in text
    _, coverage = ask(bot, store, "数据范围")
    start, end = domain_dates(multi_report, "returns")
    assert f"退货：{start} 至 {end - timedelta(days=1)}" in coverage
    assert "备份时点，非实时或日末库存" in coverage


def test_help_hides_inventory_outside_authorized_platform_scope(multi_report):
    restricted = multi_report.model_copy(update={
        "operations": multi_report.operations.model_copy(update={"all_buyers": False}),
        "metadata": multi_report.metadata.model_copy(update={
            "scope": multi_report.metadata.scope.model_copy(update={"all_buyers": False})
        })
    })
    text = help_text(restricted)
    assert "库存：" not in text
    assert "当前快照或授权范围尚不可查：库存" in text


def test_sales_only_snapshot_does_not_advertise_missing_facts(multi_report):
    sales = multi_report.model_copy(update={"operations": None})
    text = coverage_text(sales)
    assert "当前快照或授权范围尚不可查：退货、销售出库、库存" in text
    assert "库存：" not in text and "退货：" not in text
