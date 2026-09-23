import json
from datetime import timedelta

import pytest

from app.integrations.feishu_sales import SalesBot
from app.semantic.provider import SemanticPlanner
from app.semantic.schemas import SemanticRequest
from tests.unit.test_feishu_analytics import ask
from tests.unit.test_feishu_analytics import setup as _setup
from tests.unit.test_feishu_sales import config

setup = _setup


class Provider:
    def __init__(self, store):
        self.store, self.inputs = store, []

    async def interpret(self, request):
        assert self.store.rows[-1]["payload"] == {"kind": "analysis_planning"}
        self.inputs.append(request)
        if request.question == "九月一号":
            return SemanticRequest(mode="answer", intents=[dict(time="九月一号")])
        return SemanticRequest(intents=[dict(domain="sales", operation="summary")])


def test_pending_feishu_context_survives_worker_restart(setup):
    report, store, _ = setup
    provider = Provider(store)

    def bot():
        return SalesBot(
            config,
            "ou_bot",
            store,
            lambda: report,
            analysis_planner=SemanticPlanner(provider=provider),
        )

    pending, text = ask(bot(), store, "这几天卖了多少钱")
    assert pending["payload"]["kind"] == "semantic_notice"
    assert pending["payload"]["dialogue_turn"]["clarification"]["field"] == "time"
    assert "question" not in pending["payload"]
    result, text = ask(bot(), store, "九月一号")
    assert result["result"]["results"][0]["rows"][0]["amount"] == "100.0000"
    assert provider.inputs[-1].previous.pending == ["time"]


def test_guidance_is_a_normal_card_with_durable_choices(setup):
    report, store, _ = setup
    provider = Provider(store)
    bot = SalesBot(
        config, "ou_bot", store, lambda: report, analysis_planner=SemanticPlanner(provider=provider)
    )
    row, text = ask(bot, store, "这几天卖了多少钱")
    assert row["result"]["reply_card"]["header"]["template"] == "blue"
    assert not row["result"].get("unavailable")
    turn = row["payload"]["dialogue_turn"]
    assert turn["status"] == "needs_input" and turn["understood_summary"]
    assert turn["choices"] and "1." in text and "文字" in text
    assert "question" not in row["payload"]


def test_recovered_guidance_uses_saved_decision_without_model_call(setup):
    from app.integrations.feishu_analytics import answer_analysis

    report, store, _ = setup
    provider = Provider(store)
    planner = SemanticPlanner(provider=provider)
    bot = SalesBot(config, "ou_bot", store, lambda: report, analysis_planner=planner)
    row, _ = ask(bot, store, "这几天卖了多少钱")
    calls = len(provider.inputs)
    stored, text = answer_analysis(row, store, lambda: report, planner)
    assert not stored.get("unavailable")
    assert stored["reply_card"] == row["result"]["reply_card"]
    assert row["payload"]["dialogue_turn"]["clarification"]["question"] in json.dumps(
        stored["reply_card"], ensure_ascii=False
    )
    assert len(provider.inputs) == calls


def test_unique_number_selects_displayed_choice_without_another_model_call(setup):
    report, store, _ = setup
    provider = Provider(store)

    def bot():
        return SalesBot(
            config, "ou_bot", store, lambda: report,
            analysis_planner=SemanticPlanner(provider=provider),
        )

    row, _ = ask(bot(), store, "这几天卖了多少钱")
    assert row["payload"]["dialogue_turn"]["choices"]
    calls = len(provider.inputs)
    selected, _ = ask(bot(), store, "1")
    assert selected["result"]["results"][0]["kind"] == "summary"
    assert len(provider.inputs) == calls
    assert selected["payload"]["semantic_context"]["intents"][0]["domain"] == "sales"


def test_numbered_direction_then_date_survives_restart_without_reinterpreting(setup):
    report, store, _ = setup

    class UnclearGoal(Provider):
        async def interpret(self, request):
            self.inputs.append(request)
            return SemanticRequest(intents=[dict(domain="sales", operation="unknown")])

    provider = UnclearGoal(store)

    def bot():
        return SalesBot(
            config, "ou_bot", store, lambda: report,
            analysis_planner=SemanticPlanner(provider=provider),
        )

    first, first_text = ask(bot(), store, "我想了解销售情况")
    assert first["payload"]["dialogue_turn"]["clarification"]["field"] == "operation"
    assert "1." in first_text
    second, second_text = ask(bot(), store, "1")
    assert second["payload"]["dialogue_turn"]["clarification"]["field"] == "time"
    assert (
        second["payload"]["answered_dialogue_id"]
        == first["payload"]["semantic_context"]["dialogue_id"]
    )
    assert "1." in second_text
    third, _ = ask(bot(), store, "1")
    assert third["result"]["results"][0]["kind"] == "summary"
    assert len(provider.inputs) == 1


def test_unclear_domain_includes_inventory_in_numbered_options(multi_report):
    from tests.unit.test_feishu_analytics import AnalyticsStore

    store = AnalyticsStore()

    class UnknownDomain(Provider):
        async def interpret(self, request):
            self.inputs.append(request)
            return SemanticRequest(intents=[dict(domain="unknown")])

    provider = UnknownDomain(store)
    bot = SalesBot(
        config, "ou_bot", store, lambda: multi_report,
        analysis_planner=SemanticPlanner(provider=provider),
    )
    first, text = ask(bot, store, "想了解一下")
    from app.integrations.feishu_guidance import numbered_choices
    from app.semantic.dialogue_schemas import DialogueTurn

    choices = numbered_choices(DialogueTurn.model_validate(first["payload"]["dialogue_turn"]))
    inventory_index = next(i for i, c in enumerate(choices, 1) if "库存" in c.label)
    assert len(choices) == 4 and "库存" in text
    selected, _ = ask(bot, store, str(inventory_index))
    assert selected["payload"]["semantic_context"]["intents"][0]["domain"] == "inventory"
    assert len(provider.inputs) == 1


def test_number_after_two_guidance_rounds_selects_latest_without_model_call(setup):
    report, store, _ = setup
    provider = Provider(store)
    bot = SalesBot(
        config, "ou_bot", store, lambda: report, analysis_planner=SemanticPlanner(provider=provider)
    )
    ask(bot, store, "这几天卖了多少钱")
    second, _ = ask(bot, store, "我还想了解销售情况")
    calls = len(provider.inputs)
    row, text = ask(bot, store, "1")
    assert len(provider.inputs) == calls
    assert row["result"]["results"][0]["kind"] == "summary"
    assert row["payload"]["answered_dialogue_id"] == (
        second["payload"]["semantic_context"]["dialogue_id"]
    )


def test_return_metric_number_two_after_old_card_executes_return_count(multi_report):
    from tests.unit.test_feishu_analytics import AnalyticsStore

    store = AnalyticsStore()

    class ReturnsProvider(Provider):
        async def interpret(self, request):
            self.inputs.append(request)
            if len(self.inputs) == 1:
                return SemanticRequest(intents=[dict(domain="sales", operation="summary")])
            return SemanticRequest(
                intents=[dict(id="returns-goal", domain="returns", operation="summary",
                              time="2026-09-01")],
                issues=[dict(
                    intent_id="returns-goal", field="metric", kind="ambiguous",
                    question="你想问退货金额、退货单数还是退货商品数量？",
                    choices=[dict(label="金额", value="amount"),
                             dict(label="单数", value="orders"),
                             dict(label="数量", value="quantity")],
                )],
            )

    provider = ReturnsProvider(store)

    def bot():
        return SalesBot(config, "ou_bot", store, lambda: multi_report,
                        analysis_planner=SemanticPlanner(provider=provider))

    ask(bot(), store, "销售情况")  # An older unanswered card must not block this question.
    pending, text = ask(bot(), store, "九月一号有多少退货")
    assert "1. 退货单据金额" in text and "2. 退货单据数" in text
    assert "3. 退货商品数量" in text and "最新一张" in text
    assert "有效订单金额" not in text
    result, _ = ask(bot(), store, "2")  # Recreate worker to exercise persisted state.
    assert len(provider.inputs) == 2
    step = result["result"]["plan"]["steps"][0]
    assert step["domain"] == "returns" and step["metric"] == "orders"
    assert step["start_date"] == "2026-09-01"
    assert result["payload"]["answered_dialogue_id"] == (
        pending["payload"]["semantic_context"]["dialogue_id"]
    )


def test_number_without_current_choices_does_not_invent_intent(setup):
    report, store, _ = setup
    provider = Provider(store)
    bot = SalesBot(
        config, "ou_bot", store, lambda: report, analysis_planner=SemanticPlanner(provider=provider)
    )
    row, text = ask(bot, store, "1")
    assert not provider.inputs and "results" not in row["result"]
    assert "文字" in text and row["result"]["reply_card"]["header"]["template"] == "blue"


def test_guidance_labels_and_conditions_are_plain_text(setup):
    from tests.unit.test_feishu_cards import walk

    report, store, _ = setup
    hostile = "[链接](https://example.invalid) <at id=all>所有人</at>"

    class FilterProvider(Provider):
        async def interpret(self, request):
            self.inputs.append(request)
            return SemanticRequest(intents=[{
                "domain": "sales", "operation": "summary", "time": "2026-09-01",
                "filters": [{"field": "product", "value": hostile}],
            }])

    provider = FilterProvider(store)
    bot = SalesBot(
        config, "ou_bot", store, lambda: report, analysis_planner=SemanticPlanner(provider=provider)
    )
    row, _ = ask(bot, store, "九月一号只看这个商品")
    card = row["result"]["reply_card"]
    assert "results" not in row["result"]
    assert any(
        hostile in node.get("content", "")
        for node in walk(card) if node.get("tag") == "plain_text"
    )
    assert all(
        hostile not in node.get("content", "")
        for node in walk(card) if node.get("tag") == "lark_md"
    )


def test_failed_guidance_persistence_does_not_send_or_retry_the_decision(setup):
    from app.analysis.sales_query import QueryUnavailable
    from app.integrations.feishu_analytics import answer_analysis

    report, store, _ = setup
    provider = Provider(store)
    planner = SemanticPlanner(provider=provider)
    save = store.replace_payload

    def only_allow_marker(job, payload):
        return save(job, payload) if payload["kind"] == "analysis_planning" else False

    store.replace_payload = only_allow_marker
    bot = SalesBot(config, "ou_bot", store, lambda: report, analysis_planner=planner)
    row, _ = ask(bot, store, "这几天卖了多少钱")
    assert row["payload"] == {"kind": "analysis_planning"}
    assert row["result"]["unavailable"] and len(provider.inputs) == 1
    with pytest.raises(QueryUnavailable):
        answer_analysis(row, store, lambda: report, planner)
    assert len(provider.inputs) == 1


@pytest.mark.parametrize("barrier", ["unknown", "expired", "clear", "snapshot"])
def test_ineligible_pending_context_is_not_inherited(setup, barrier):
    report, store, _ = setup
    provider = Provider(store)
    bot = SalesBot(
        config, "ou_bot", store, lambda: report, analysis_planner=SemanticPlanner(provider=provider)
    )
    pending, _ = ask(bot, store, "这几天卖了多少钱")
    if barrier == "unknown":
        pending["status"] = "unknown"
    elif barrier == "expired":
        pending["created_at"] -= timedelta(minutes=31)
        pending["updated_at"] -= timedelta(minutes=31)
    elif barrier == "clear":
        ask(bot, store, "清除追问上下文")
    else:
        pending["payload"]["snapshot_key"] = "different-snapshot"
    result, _ = ask(bot, store, "九月一号")
    assert provider.inputs[-1].previous is None
    assert "results" not in result["result"]
