"""Full receive/plan/calculate/reply flow with synthetic data and no external sends."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.analysis.sales import analyze_sales
from app.core.business_rules import load_business_rules
from app.integrations.feishu_sales import SalesBot
from app.schemas.analytics import PlanDecision
from tests.unit.test_feishu_app import event
from tests.unit.test_feishu_sales import config


class AnalyticsStore:
    def __init__(self):
        self.rows = []

    def enqueue(self, identity, payload):
        if any(row["message_id"] == identity["message_id"] for row in self.rows):
            return "duplicate"
        self.rows.append(
            dict(
                identity,
                payload=payload,
                job_id=str(len(self.rows)),
                request_id=str(len(self.rows)),
                status="queued",
                created_at=datetime.now(timezone.utc),
                lease_token="lease",
            )
        )
        return "queued"

    def claim(self):
        for row in self.rows:
            if row["status"] == "queued":
                row["status"] = "analyzing"
                return row.copy()

    def replace_payload(self, job, payload):
        self.rows[int(job["job_id"])]["payload"] = payload.copy()
        return True

    def finish_analysis(self, job, result, text):
        row = self.rows[int(job["job_id"])]
        row["payload"].pop("question", None)
        row.update(result=result, reply_text=text, status="ready")
        return True

    def next_reply(self):
        for row in self.rows:
            if row["status"] == "ready":
                row["status"] = "sending"
                return row.copy()

    def mark(self, job_id, status, error_code=None):
        row = self.rows[int(job_id)]
        row["payload"].pop("question", None)
        row.update(status=status, updated_at=datetime.now(timezone.utc))

    def latest_analysis_context(self, job):
        for row in reversed(self.rows):
            if (
                row["status"] == "sent"
                and row["created_at"] <= job["created_at"]
                and row["job_id"] != job["job_id"]
                and row["created_at"] > job["created_at"] - timedelta(minutes=30)
                and all(
                    row[key] == job[key]
                    for key in ("app_id", "tenant_key", "chat_id", "user_open_id")
                )
                and row["payload"]["kind"] in {"analysis", "clear_context"}
            ):
                return row["payload"] if row["payload"]["kind"] == "analysis" else None


class Planner:
    enabled = True

    def __init__(self, steps, store):
        self.steps, self.store, self.calls = steps, store, []

    async def plan(self, body, context):
        assert self.store.rows[-1]["payload"]["kind"] == "analysis_planning"
        assert "question" not in self.store.rows[-1]["payload"]
        assert set(context) == {"today", "available_start", "available_end_exclusive", "timezone"}
        assert "BUYER-A" not in str(context)
        self.calls.append(body)
        return PlanDecision(action="run", plan={"steps": self.steps})


def step(kind="product_ranking", **extra):
    return dict(kind=kind, start_date="2026-09-01", end_date_exclusive="2026-09-03", **extra)


@pytest.fixture
def setup(extract, window, scope, source_as_of):
    report = analyze_sales(
        extract,
        window,
        scope,
        source_as_of=source_as_of,
        rules=load_business_rules(),
        synthetic=True,
    )
    store = AnalyticsStore()
    planner = Planner([step()], store)
    return report, store, planner


def make_bot(setup, **kwargs):
    report, store, planner = setup
    return SalesBot(config, "ou_bot", store, lambda: report, analysis_planner=planner, **kwargs)


def ask(bot, store, text, **identity):
    number = len(store.rows)
    message = event(text=text, message_id=f"om_{number}", event_id=f"evt_{number}", **identity)
    assert bot.accept(message) == "queued"
    replies = []
    assert (
        bot.process_pending(
            lambda *args: pytest.fail("expected card"),
            card_sender=lambda mid, card, key: replies.append(card),
        )
        == 1
    )
    return store.rows[-1], json.dumps(replies[0], ensure_ascii=False)


def test_natural_drug_question_uses_shared_plan_and_scope_note(setup):
    _, store, planner = setup
    bot = make_bot(setup)
    row, card = ask(bot, store, "分析2026年9月1日至2号的销售数据，哪些药品卖的好")
    assert len(planner.calls) == 1
    assert row["payload"]["kind"] == "analysis" and "question" not in row["payload"]
    assert row["result"]["results"][0]["rows"][0]["amount"] == "260.0000"
    assert "未按药品类别筛选" in card and "260.00" in card and "前 10 名" in card
    assert "2026-09-02" in card and "查询编号" in card and "SKU-A" in card
    assert bot.process_pending(lambda *args: pytest.fail("duplicate reply")) == 0


@pytest.mark.parametrize(
    "kind,topic",
    [
        ("summary", "销售概览"),
        ("trend", "每日销售趋势"),
        ("buyer_ranking", "客户销售额排行"),
        ("product_ranking", "商品销售额排行"),
        ("anomalies", "日波动线索"),
    ],
)
def test_all_analysis_types_return_actual_rows(setup, kind, topic):
    _, store, planner = setup
    planner.steps = [step(kind)]
    row, card = ask(make_bot(setup), store, "分析2026年9月1日至2号的" + topic)
    assert row["result"]["results"][0]["kind"] == kind
    assert "300.00" in card
    assert row["result"]["results"][0]["rows"]


def test_comparison_and_combined_results(setup):
    _, store, planner = setup
    planner.steps = [
        dict(
            kind="comparison",
            start_date="2026-09-02",
            end_date_exclusive="2026-09-03",
            comparison_start_date="2026-09-01",
            comparison_end_date_exclusive="2026-09-02",
        )
    ]
    row, card = ask(
        make_bot(setup),
        store,
        "比较2026年9月2日至2日与2026年9月1日至1日的销售额，按商品拆解变化贡献",
    )
    assert row["result"]["results"][0]["totals"]["delta"] == "100.0000"
    assert "不代表因果" in card and "比较期" in card
    planner.steps = [step("trend"), step("product_ranking")]
    row, card = ask(make_bot(setup), store, "2026年9月1日至2日每日销售趋势和商品排行")
    assert len(row["result"]["results"]) == 2
    assert "每日销售趋势" in card and "商品排行" in card


def test_followup_survives_bot_restart_and_clear_resets_it(setup):
    _, store, planner = setup
    ask(make_bot(setup), store, "2026年9月1日至2日哪些药品卖的好")
    planner.steps = [step(metric="orders", top_n=5)]
    row, card = ask(make_bot(setup), store, "换成按订单数排，取前5名")
    assert planner.calls[-1].previous_plan.steps[0].kind == "product_ranking"
    assert "订单数" in card and row["result"]["results"][0]["rows"][0]["order_count"] == 2
    assert "未按药品类别筛选" in card
    ask(make_bot(setup), store, "清除追问上下文")
    row, card = ask(make_bot(setup), store, "换成按订单数排，取前5名")
    assert planner.calls[-1].previous_plan is None
    assert "results" not in row["result"]


def test_model_cannot_drop_filters_and_uncertain_attempt_is_not_retried(setup):
    _, store, planner = setup
    row, _ = ask(make_bot(setup), store, "2026年9月1日至2日只看药品排行")
    assert row["result"]["unavailable"] and "results" not in row["result"]
    bot = make_bot(setup)
    bot.accept(
        event(text="2026年9月1日至2日哪些商品卖的好", message_id="om_new", event_id="evt_new")
    )
    store.rows[-1]["payload"] = {"kind": "analysis_planning"}
    calls = len(planner.calls)
    replies = []
    bot.process_pending(lambda mid, text, key: replies.append(text))
    assert len(planner.calls) == calls and "重新提问" in replies[0]


def test_unauthorized_or_revoked_request_cannot_use_new_planner(setup):
    report, store, planner = setup
    current = config()
    bot = SalesBot(lambda: current, "ou_bot", store, lambda: report, analysis_planner=planner)
    assert bot.accept(event(user="intruder", text="2026年9月1日至2日药品排行")) == "ignored"
    bot.accept(event(text="2026年9月1日至2日药品热销排行"))
    current = current.model_copy(update={"allowed_chat_ids": ("another",)})
    assert bot.process_pending(lambda *args: pytest.fail("revoked")) == 0
    assert not planner.calls


def test_failed_durable_marker_prevents_any_model_call(setup):
    _, store, planner = setup
    store.replace_payload = lambda *args: False
    row, _ = ask(make_bot(setup), store, "2026年9月1日至2日哪些药品卖得好")
    assert not planner.calls and row["result"]["unavailable"]


def test_changed_snapshot_drops_previous_context(setup):
    _, store, planner = setup
    ask(make_bot(setup), store, "2026年9月1日至2日哪些药品卖得好")
    store.rows[0]["payload"]["snapshot_key"] = "old-snapshot"
    row, _ = ask(make_bot(setup), store, "再看商品排行")
    assert planner.calls[-1].previous_plan is None and row["result"]["unavailable"]


def test_model_disabled_keeps_fixed_queries_available(setup):
    _, store, planner = setup
    planner.enabled = False
    row, _ = ask(make_bot(setup), store, "2026-09-01至2026-09-02 商品销售额排行 前10")
    assert row["result"]["results"][0]["rows"][0]["amount"] == "260.0000"
    assert not planner.calls
    row, card = ask(make_bot(setup), store, "2026年9月1日至2日哪些药品卖得好")
    assert row["result"]["unavailable"] and "固定日期查询" in card


def test_relative_dates_use_event_time_not_worker_time(setup):
    _, store, planner = setup
    original = planner.plan

    async def check(body, context):
        assert context["today"] == "2026-09-03"
        return await original(body, context)

    planner.plan = check
    bot = make_bot(setup)
    data = event(text="过去2天哪些药品卖得好")
    data.event.message.create_time = str(
        int(datetime.fromisoformat("2026-09-02T16:30:00+00:00").timestamp() * 1000)
    )
    bot.accept(data)
    bot.process_pending(lambda *args: None)
    assert store.rows[0]["result"]["results"][0]["rows"][0]["amount"] == "260.0000"


def test_card_budget_and_untrusted_labels_remain_plain_text(setup):
    from app.analysis.analytics import execute_analysis
    from app.integrations.feishu_analytics_cards import MAX_CARD_BYTES, render_analysis
    from app.schemas.analytics import AnalysisPlan
    from tests.unit.test_feishu_cards import walk

    report, _, _ = setup
    response = execute_analysis(report, AnalysisPlan(steps=[step()]))
    result = response.results[0]
    hostile = "[链接](https://example.invalid) <at id=all>所有人</at>"
    rows = [
        dict(result.rows[0], name=hostile + "长名字" * 70, code=str(index)) for index in range(50)
    ]
    response = response.model_copy(
        update={
            "results": [result.model_copy(update={"rows": rows})] * 6,
            "plan": AnalysisPlan(steps=[step()] * 6),
            "interpretation": ["按当前商品范围分析"],
        }
    )
    card, text = render_analysis(response)
    assert len(json.dumps(card, ensure_ascii=False).encode()) <= MAX_CARD_BYTES
    assert "卡片节选" in text and "查询编号" in text
    assert all(
        "example.invalid" not in node.get("content", "")
        for node in walk(card)
        if node.get("tag") == "lark_md"
    )
    assert any(
        "example.invalid" in node.get("content", "")
        for node in walk(card)
        if node.get("tag") == "plain_text"
    )
    assert len(response.results[0].rows) == 50  # display truncation does not change facts
