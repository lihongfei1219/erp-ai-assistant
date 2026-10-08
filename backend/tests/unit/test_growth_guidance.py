"""A known date with missing facts is not an intent clarification."""

import json
from datetime import date

import pytest

from app.analysis.growth import validate_growth
from app.analysis.growth_guidance import comparable_months, date_gap_message
from app.integrations.feishu_guidance import render_guidance
from app.integrations.feishu_sales import SalesBot
from app.schemas.analytics import AnalysisStep
from app.schemas.operations import OperationsSnapshot
from app.semantic.provider import SemanticPlanner
from app.semantic.schemas import SemanticIntent
from tests.api.test_guided_dialogue import Provider
from tests.growth_fixtures import part
from tests.unit.test_feishu_analytics import AnalyticsStore, ask
from tests.unit.test_feishu_sales import config


@pytest.fixture
def months_report(growth_report):
    data = growth_report.operations.model_dump()
    data["shipping_history"].extend(
        [
            part("2026-06-01", "2026-07-01", [], 400),
            part("2026-05-01", "2026-06-01", [], 500),
            part("2025-07-01", "2025-08-01", [], 600),
            part("2025-06-01", "2025-07-01", [], 700),
        ]
    )
    return growth_report.model_copy(update={"operations": OperationsSnapshot.model_validate(data)})


def intent(**updates):
    return SemanticIntent(
        domain="shipping",
        operation="growth",
        target="product",
        metric="amount",
        time="2026年10月",
        growth_basis="both",
        **updates,
    )


def test_screenshot_intent_stays_understood_and_explains_both_missing_periods(months_report):
    text = date_gap_message(months_report, intent(), date(2026, 10, 8))
    assert "已理解：按品种比较出库金额变化" in text
    assert "本期：2026年10月；上期：2026年9月；去年同期：2025年10月" in text
    assert "本期（2026年10月）" in text
    assert "上期（2026年9月）" in text
    assert "生成" not in text and "分区" not in text


def test_month_choices_validate_all_periods_and_offer_more_than_one(months_report):
    choices = comparable_months(months_report, intent())
    assert [start for start, _, _ in choices] == [
        date(2026, 8, 1),
        date(2026, 7, 1),
        date(2026, 6, 1),
    ]
    for start, end, label in choices:
        assert "环比" in label and "同比" in label
        validate_growth(
            months_report,
            AnalysisStep(
                domain="shipping", kind="growth", start_date=start, end_date_exclusive=end
            ),
        )


def test_gap_inside_baseline_removes_month_choice(months_report):
    data = months_report.operations.model_dump()
    data["shipping_history"] = [
        p for p in data["shipping_history"] if p["start"] != date(2025, 7, 1)
    ]
    report = months_report.model_copy(
        update={"operations": OperationsSnapshot.model_validate(data)}
    )
    assert date(2026, 7, 1) not in [s for s, _, _ in comparable_months(report, intent())]


def test_previous_only_does_not_require_year_over_year(growth_report):
    item = intent().model_copy(update={"growth_basis": "previous"})
    assert "同比" not in comparable_months(growth_report, item)[0][2]
    message = date_gap_message(growth_report, item, date(2026, 10, 8))
    assert "去年同期" not in message


def test_feishu_first_card_explains_understanding_and_number_selection_preserves_conditions(
    months_report,
):
    item = intent().model_dump(exclude_none=True)
    item.update(
        target="buyer",
        metric="quantity",
        growth_direction="decrease",
        filters=[dict(field="product", value="P")],
    )
    provider = Provider({"intents": [item]})
    store = AnalyticsStore()
    bot = SalesBot(
        config,
        "ou_bot",
        store,
        lambda: months_report,
        analysis_planner=SemanticPlanner(provider=provider),
    )
    _, text = ask(bot, store, "比较2026年10月商品P各客户出库数量，环比和同比，只看下降")
    card = json.loads(text)
    assert card["header"]["title"]["content"] == "所选日期暂无完整数据"
    assert "已理解：按指定品种内的客户比较出库数量变化" in text
    assert "本期：2026年10月" in text and "去年同期：2025年10月" in text
    assert "可用完整日期" not in text
    assert "1. 查2026年08月" in text and "2. 查2026年07月" in text and "3. 查2026年06月" in text
    assert "确认一下你的想法" not in json.dumps(card, ensure_ascii=False)
    ask(bot, store, "2")
    assert len(provider.requests) == 1
    plan = store.rows[-1]["payload"]["plan"]["steps"][0]
    assert plan["start_date"] == "2026-07-01"
    assert plan["growth_basis"] == "both" and plan["growth_direction"] == "decrease"
    assert plan["metric"] == "quantity" and plan["dimension"] == "buyer"
    assert plan["filters"][0]["code"] == "P"


def test_no_comparable_month_does_not_invent_options(multi_report):
    assert comparable_months(multi_report, intent()) == []
    from app.analysis.dialogue import _dates_for

    start, end = _dates_for(multi_report, intent())
    assert start == end
    assert "没有满足这些比较条件" in date_gap_message(multi_report, intent(), date(2026, 10, 8))


def test_growth_long_question_is_not_replaced_by_generic_date_question(months_report):
    from tests.unit.test_feishu_guidance import payload

    source = payload("shipping", status="data_gap")
    source["status"] = "data_gap"
    turn = source["dialogue_turn"]
    turn["draft"]["intents"][1]["fields"]["operation"] = "growth"
    question = date_gap_message(months_report, intent(), date(2026, 10, 8))
    turn["clarification"].update(kind="data_gap", question=question)
    _, text = render_guidance(source)
    assert question in text
    assert "要改查哪个时间" not in text
