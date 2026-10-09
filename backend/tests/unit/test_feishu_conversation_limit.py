"""Five delivered analysis turns, including successful followups, bound Feishu memory."""

import json
from datetime import timedelta

import pytest

from app.integrations.feishu_analytics import answer_analysis
from app.integrations.feishu_conversation import conversation_closed
from app.integrations.feishu_sales import SalesBot
from app.semantic.provider import SemanticPlanner
from app.semantic.schemas import SemanticRequest
from tests.unit.test_feishu_analytics import ask
from tests.unit.test_feishu_analytics import setup as _setup
from tests.unit.test_feishu_sales import config

setup = _setup


class Provider:
    def __init__(self, *, pending=False):
        self.inputs = []
        self.pending = pending

    async def interpret(self, request):
        self.inputs.append(request)
        fields = {"domain": "sales", "operation": "summary", "metric": "amount"}
        if not self.pending:
            fields["time"] = "2026-09-01"
        return SemanticRequest(
            mode="answer" if request.previous else "new", intents=[fields]
        )


def make_bot(report, store, provider):
    return SalesBot(
        config, "ou_bot", store, lambda: report,
        analysis_planner=SemanticPlanner(provider=provider),
    )


def test_first_question_and_successful_followups_count_and_sixth_starts_fresh(setup):
    report, store, _ = setup
    provider = Provider()
    for number in range(1, 7):
        # A recreated worker must read the durable count instead of resetting it.
        row, card = ask(make_bot(report, store, provider), store, "请分析2026年9月1日的经营情况")
        expected = number if number <= 5 else 1
        assert row["payload"]["conversation_round"] == expected
        assert row["result"].get("results"), row["reply_text"]
        assert row["result"]["results"][0]["kind"] == "summary"
        assert f"第 {expected}/5 轮" in card
        assert (provider.inputs[-1].previous is None) == (number in {1, 6})
        assert "conversation_round" not in provider.inputs[-1].model_dump_json()
        if number == 5:
            assert "已结束" in card and "30分钟内可" not in card
            assert "已结束" in row["reply_text"] and "30分钟内可" not in row["reply_text"]


def test_fifth_unresolved_turn_closes_without_choices_and_old_numbers_never_run(setup):
    report, store, _ = setup
    provider = Provider(pending=True)
    bot = make_bot(report, store, provider)
    for number in range(1, 6):
        row, card = ask(bot, store, "想看看经营情况")
        assert row["payload"]["conversation_round"] == number
    assert "本次对话已结束" in card
    assert "回复编号" not in card and "再次 @我，用文字补充即可" not in card
    assert "下一条完整问题" in card
    assert "本次已理解的需求" in card
    calls = len(provider.inputs)
    for _ in range(2):
        closed, text = ask(bot, store, "1")
        assert "旧编号不再有效" in text
        assert closed["payload"]["conversation_round"] == 5
        assert not closed["result"].get("run_id")
        assert len(provider.inputs) == calls
    fresh, _ = ask(bot, store, "分析另外一段经营情况")
    assert fresh["payload"]["conversation_round"] == 1
    assert provider.inputs[-1].previous is None


def test_fifth_numbered_reply_can_finish_without_model_and_recovery_does_not_increment(setup):
    report, store, _ = setup
    provider = Provider(pending=True)
    bot = make_bot(report, store, provider)
    for _ in range(4):
        ask(bot, store, "想看看经营情况")
    row, card = ask(bot, store, "1")
    assert row["payload"]["conversation_round"] == 5
    assert row["result"]["run_id"] and "已结束" in card
    assert len(provider.inputs) == 4
    result, text = answer_analysis(row, store, lambda: report, bot.analysis_planner)
    assert result["run_id"] and "第 5/5 轮" in text
    assert row["payload"]["conversation_round"] == 5
    assert len(provider.inputs) == 4
    closed, text = ask(bot, store, "1")
    assert "旧编号不再有效" in text
    assert "当前条件尚未形成" not in text
    assert not closed["result"].get("run_id")


def test_guidance_recovery_uses_frozen_count_and_help_does_not_consume_rounds(setup):
    report, store, _ = setup
    provider = Provider(pending=True)
    bot = make_bot(report, store, provider)
    first, _ = ask(bot, store, "想看看经营情况")
    stored, _ = answer_analysis(first, store, lambda: report, bot.analysis_planner)
    assert stored["reply_card"] == first["result"]["reply_card"]
    assert first["payload"]["conversation_round"] == 1
    for command in ("帮助", "数据范围", "连接测试"):
        ask(bot, store, command)
    next_row, _ = ask(bot, store, "继续了解")
    assert next_row["payload"]["conversation_round"] == 2
    assert len(provider.inputs) == 2
    ask(bot, store, "重新开始")
    fresh, _ = ask(bot, store, "想看看另一件事")
    assert fresh["payload"]["conversation_round"] == 1
    assert provider.inputs[-1].previous is None


def test_invalid_number_counts_as_guidance_but_cannot_exceed_limit(setup):
    report, store, _ = setup
    provider = Provider(pending=True)
    bot = make_bot(report, store, provider)
    ask(bot, store, "想看看经营情况")
    for number in range(2, 6):
        row, card = ask(bot, store, "9")
        assert row["payload"]["conversation_round"] == number
    assert "本次对话已结束" in card
    assert len(provider.inputs) == 1


@pytest.mark.parametrize("barrier", ["expired", "snapshot", "user"])
def test_existing_isolation_boundaries_reset_counter(setup, barrier):
    report, store, _ = setup
    provider = Provider(pending=True)
    bot = make_bot(report, store, provider)
    row, _ = ask(bot, store, "想看看经营情况")
    if barrier == "expired":
        row["created_at"] -= timedelta(minutes=31)
        row["updated_at"] -= timedelta(minutes=31)
    elif barrier == "snapshot":
        row["payload"]["snapshot_key"] = "previous-snapshot"
    else:
        row["user_open_id"] = "another-user"
    fresh, _ = ask(bot, store, "分析新的经营情况")
    assert fresh["payload"]["conversation_round"] == 1
    assert provider.inputs[-1].previous is None


def test_fixed_commands_also_count_and_old_payload_remains_compatible(setup):
    report, store, _ = setup
    provider = Provider()
    bot = make_bot(report, store, provider)
    first, _ = ask(bot, store, "2026-09-01至2026-09-01 销售概览")
    assert first["payload"]["conversation_round"] == 1
    del first["payload"]["conversation_round"]  # A delivered pre-upgrade result.
    for number in range(2, 7):
        row, card = ask(bot, store, "2026-09-01至2026-09-01 销售概览")
        assert row["payload"]["conversation_round"] == (number if number <= 5 else 1)
        if number == 5:
            assert conversation_closed(row["payload"]) and "已结束" in card
    assert not provider.inputs


def test_closed_guidance_recovery_preserves_exact_card(setup):
    report, store, _ = setup
    provider = Provider(pending=True)
    bot = make_bot(report, store, provider)
    for _ in range(5):
        row, _ = ask(bot, store, "想看看经营情况")
    before = json.dumps(row["payload"])
    result, _ = answer_analysis(row, store, lambda: report, bot.analysis_planner)
    assert result["reply_card"] == row["result"]["reply_card"]
    assert json.dumps(row["payload"]) == before
    assert len(provider.inputs) == 5
