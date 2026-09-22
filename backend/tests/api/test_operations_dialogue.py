from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.analysis.dialogue import converse
from app.core.settings import ApiSettings
from app.main import create_app
from app.semantic.dialogue_schemas import ConversationRequest
from app.semantic.provider import SemanticPlanner
from app.semantic.schemas import SemanticRequest

HEADERS = {}


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Provider:
    def __init__(self, intents):
        self.intents = intents
        self.requests = []

    async def interpret(self, request):
        self.requests.append(request)
        return SemanticRequest(intents=self.intents)


@pytest.mark.parametrize(
    "domain,operation,metric,time",
    [
        ("returns", "summary", "amount", "9月2号"),
        ("shipping", "list", "quantity", "9月3号"),
        ("inventory", "ranking", "stock", "9月16号"),
    ],
)
def test_natural_semantics_reach_business_executor(multi_report, domain, operation, metric, time):
    provider = Provider([dict(domain=domain, operation=operation, metric=metric, time=time)])
    client = TestClient(
        create_app(
            ApiSettings(),
            report=multi_report,
            analysis_planner=SemanticPlanner(provider=provider),
        )
    )
    response = client.post(
        "/api/v1/analysis/converse", headers=HEADERS, json={"question": time + "请分析相关业务"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "result", body
    assert body["result"]["results"][0]["domain"] == domain
    assert body["result"]["results"][0]["rows"]
    assert set(provider.requests[0].capabilities["executable_domains"]) == {
        "sales",
        "returns",
        "shipping",
        "inventory",
    }
    assert "BUYER-A" not in str(provider.requests[0].model_dump())


@pytest.mark.anyio
@pytest.mark.parametrize(
    "question_time,model_time",
    [
        ("今天", "今天"),
        ("现在", "现在"),
        ("现在", "今天"),
        ("现在", "today"),
        ("当前", "当前"),
        ("此刻", "今天"),
    ],
)
async def test_inventory_today_guides_explicit_snapshot_choice(
    multi_report, question_time, model_time
):
    provider = Provider(
        [dict(domain="inventory", operation="ranking", metric="stock", time=model_time)]
    )
    planner = SemanticPlanner(provider=provider)
    first = await converse(
        ConversationRequest(question=question_time + "库存"),
        multi_report,
        planner,
        date(2026, 9, 22),
    )
    assert first.turn.status == "data_gap"
    assert "时点" in first.turn.clarification.question
    assert first.turn.available_dates.start == date(2026, 9, 16)
    choice = next(c for c in first.turn.choices if "2026-09-16" in c.label)
    result = await converse(
        ConversationRequest(choice_id=choice.id),
        multi_report,
        planner,
        date(2026, 9, 22),
        previous=first.context,
    )
    assert result.turn.status == "result"
    assert len(provider.requests) == 1
    assert (
        result.turn.result.results[0].totals["as_of"]
        == multi_report.metadata.source_as_of.isoformat()
    )


@pytest.mark.anyio
async def test_current_request_cannot_be_replaced_with_backup_by_model(multi_report):
    result = await converse(
        ConversationRequest(question="现在库存最多的是哪些商品"),
        multi_report,
        SemanticPlanner(
            provider=Provider(
                [
                    dict(
                        domain="inventory", operation="ranking", metric="stock", time="2026-09-16"
                    ),
                ]
            )
        ),
        date(2026, 9, 22),
    )
    assert result.turn.result is None
    assert result.context.intents[0].time is None


@pytest.mark.anyio
@pytest.mark.parametrize("copied_date", [None, "2026-09-16"])
async def test_current_inventory_followup_cannot_inherit_backup(multi_report, copied_date):
    from app.schemas.analytics import AnalysisPlan
    from app.semantic.compiler import context_from_plan

    previous = context_from_plan(
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
        )
    )

    class FollowupProvider:
        async def interpret(self, request):
            return SemanticRequest(
                mode="followup",
                intents=[
                    dict(
                        id=previous.intents[0].id,
                        operation="ranking",
                        target="product",
                        time=copied_date,
                    )
                ],
            )

    result = await converse(
        ConversationRequest(question="现在库存最多的是哪些商品"),
        multi_report,
        SemanticPlanner(provider=FollowupProvider()),
        date(2026, 9, 22),
        previous=previous,
    )
    assert result.turn.result is None
    assert result.turn.status in {"needs_input", "data_gap"}


@pytest.mark.anyio
@pytest.mark.parametrize("current_inventory", [False, True])
async def test_current_word_does_not_override_explicit_historical_period(
    multi_report, current_inventory
):
    intents = [dict(domain="returns", operation="summary", time="9月2号")]
    question = "现在帮我看9月2号的退货金额"
    if current_inventory:
        intents.append(dict(domain="inventory", operation="summary", time="现在"))
        question = "看9月2号的退货金额，再看现在的库存"
    result = await converse(
        ConversationRequest(question=question),
        multi_report,
        SemanticPlanner(provider=Provider(intents)),
        date(2026, 9, 22),
    )
    assert result.turn.status == ("data_gap" if current_inventory else "result")
    assert result.context.intents[0].time == "2026-09-02至2026-09-02"
    if current_inventory:
        assert result.context.intents[1].time == "2026-09-22至2026-09-22"


@pytest.mark.anyio
async def test_three_domain_combo_executes_all_goals(multi_report):
    provider = Provider(
        [
            dict(domain=d, operation="summary", time=t)
            for d, t in [("returns", "9月2号"), ("shipping", "9月3号"), ("inventory", "9月16号")]
        ]
    )
    result = await converse(
        ConversationRequest(question="查看9月2号退货、9月3号出库、9月16号库存"),
        multi_report,
        SemanticPlanner(provider=provider),
        date(2026, 9, 22),
    )
    assert result.turn.status == "result"
    assert [r.domain for r in result.turn.result.results] == ["returns", "shipping", "inventory"]


@pytest.mark.anyio
async def test_missing_metric_and_date_guidance_stays_in_returns(multi_report):
    planner = SemanticPlanner(provider=Provider([dict(domain="returns", operation="summary")]))
    first = await converse(
        ConversationRequest(question="看看退货"), multi_report, planner, date(2026, 9, 22)
    )
    assert first.turn.status == "needs_input"
    assert first.turn.clarification.field == "time"
    choice = next(c for c in first.turn.choices if c.action.kind == "date_range")
    result = await converse(
        ConversationRequest(
            choice_id=choice.id, date_range={"start": "2026-09-02", "end_exclusive": "2026-09-03"}
        ),
        multi_report,
        planner,
        date(2026, 9, 22),
        previous=first.context,
    )
    assert result.turn.status == "result"
    assert result.turn.result.results[0].domain == "returns"


@pytest.mark.anyio
async def test_unsupported_rate_does_not_execute_partial_combo(multi_report):
    provider = Provider(
        [
            dict(domain="returns", operation="ranking", metric="return_rate", time="9月2号"),
            dict(domain="shipping", operation="summary", time="9月3号"),
        ]
    )
    result = await converse(
        ConversationRequest(question="比较这两项"),
        multi_report,
        SemanticPlanner(provider=provider),
        date(2026, 9, 22),
    )
    assert result.turn.status == "capability_gap" and result.turn.result is None
    assert "退货率" in result.turn.clarification.question
    assert all("有效订单金额" not in c.label for c in result.turn.choices)


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["summary", "trend", "existence"])
@pytest.mark.parametrize("target", ["buyer", "product"])
async def test_grouping_is_not_dropped_from_new_business_goals(multi_report, operation, target):
    planner = SemanticPlanner(
        provider=Provider(
            [dict(domain="returns", operation=operation, target=target, time="9月2号")]
        )
    )
    first = await converse(
        ConversationRequest(question="9月2号按对象分组分析"),
        multi_report,
        planner,
        date(2026, 9, 22),
    )
    assert first.turn.status == "capability_gap"
    assert first.turn.result is None
    assert first.context.intents[0].target == target
    choice = next(c for c in first.turn.choices if "排行" in c.label)
    result = await converse(
        ConversationRequest(choice_id=choice.id),
        multi_report,
        planner,
        date(2026, 9, 22),
        previous=first.context,
    )
    assert result.turn.status == "result"
    assert result.turn.result.plan.steps[0].kind == target + "_ranking"


@pytest.mark.anyio
async def test_customer_return_list_preserves_requested_dimension(multi_report):
    planner = SemanticPlanner(
        provider=Provider([dict(domain="returns", operation="list", target="buyer", time="9月2号")])
    )
    result = await converse(
        ConversationRequest(question="9月2号哪些客户有退货"),
        multi_report,
        planner,
        date(2026, 9, 22),
    )
    assert result.turn.status == "result"
    output = result.turn.result.results[0]
    assert output.rows[0]["code"] == "BUYER-A"
    assert output.rows[0]["document_count"] == 2
    assert len(output.rows) == 1


@pytest.mark.anyio
async def test_model_date_suggestions_use_the_affected_domain_coverage(multi_report):
    class DateProvider:
        async def interpret(self, request):
            return SemanticRequest(
                intents=[
                    dict(id="sale", domain="sales", operation="summary", time="9月2号"),
                    dict(id="stock", domain="inventory", operation="summary"),
                ],
                issues=[
                    dict(
                        intent_id="stock",
                        field="time",
                        kind="missing",
                        question="库存想查看哪个时点？",
                        choices=[
                            dict(label="库存快照", value="9月16号"),
                            dict(label="销售日期", value="9月2号"),
                        ],
                    )
                ],
            )

    planner = SemanticPlanner(provider=DateProvider())
    first = await converse(
        ConversationRequest(question="查看9月2号销售，再看看库存"),
        multi_report,
        planner,
        date(2026, 9, 22),
    )
    assert first.turn.status == "needs_input"
    assert first.turn.clarification.intent_id == first.context.intents[1].id
    assert first.turn.available_dates.start == date(2026, 9, 16)
    assert not any("2026-09-02" in c.label for c in first.turn.choices)
    choice = next(c for c in first.turn.choices if "2026-09-16" in c.label)
    result = await converse(
        ConversationRequest(choice_id=choice.id),
        multi_report,
        planner,
        date(2026, 9, 22),
        previous=first.context,
    )
    assert result.turn.status == "result"
    assert [s.start_date for s in result.turn.result.plan.steps] == [
        date(2026, 9, 2),
        date(2026, 9, 16),
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("limit", [None, 3])
@pytest.mark.parametrize(
    "domain,metric,target,operation",
    [
        ("returns", "amount", "buyer", "comparison"),
        ("returns", "orders", "buyer", "comparison"),
        ("shipping", "amount", "product", "comparison"),
        ("shipping", "orders", "buyer", "comparison"),
        ("inventory", "stock", None, "trend"),
    ],
)
async def test_unsupported_operation_choices_advance_only_affected_goal(
    multi_report,
    domain,
    metric,
    target,
    operation,
    limit,
):
    time = "9月16号" if domain == "inventory" else "9月2号"
    intent = dict(
        domain=domain, operation=operation, metric=metric, target=target, time=time, limit=limit
    )
    if operation == "comparison":
        intent["comparison_time"] = "9月1号"
    provider = Provider(
        [
            dict(domain="sales", operation="summary", metric="amount", time="9月3号"),
            intent,
        ]
    )
    planner = SemanticPlanner(provider=provider)
    first = await converse(
        ConversationRequest(question=f"保留9月3号销售，另看{time}业务并与9月1号比较"),
        multi_report,
        planner,
        date(2026, 9, 22),
    )
    assert first.turn.status == "capability_gap"
    assert first.turn.clarification.intent_id == first.context.intents[1].id
    assert first.turn.clarification.field == "operation"
    if domain != "inventory":
        assert "库存" not in first.turn.clarification.question
    assert all("有效订单" not in c.label for c in first.turn.choices)
    assert len(first.turn.choices) == 3
    for choice in first.turn.choices:
        result = await converse(
            ConversationRequest(choice_id=choice.id),
            multi_report,
            planner,
            date(2026, 9, 22),
            # Each alternative is a separate imported fixture conversation.
            previous=first.context.model_copy(update={"runtime_ref": None}),
        )
        assert result.turn.status == "result", (choice.label, result.turn.clarification)
        assert result.context.intents[0] == first.context.intents[0]
        steps = result.turn.result.plan.steps
        assert len(steps) == 2 and steps[0].domain == "sales"
        assert steps[1].domain == domain and steps[1].metric == metric
        assert result.context.intents[1].comparison_time is None
        if steps[1].kind == "summary":
            assert result.context.intents[1].target is None
            assert result.context.intents[1].limit is None
            if limit:
                assert f"前{limit}项" in choice.label
            if target:
                assert "整体" in choice.label and "分组" in choice.label
        elif target:
            assert result.context.intents[1].target == target
        if steps[1].kind != "summary" and limit:
            assert steps[1].top_n == limit
        if operation == "comparison":
            assert "不再比较" in choice.label
    assert len(provider.requests) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("order", [None, "ascending"])
@pytest.mark.parametrize("issue_kind", [None, "missing", "ambiguous", "dependency"])
async def test_operation_recovery_clears_only_obsolete_conditions(multi_report, order, issue_kind):
    class ComparisonProvider:
        async def interpret(self, request):
            return SemanticRequest(
                intents=[
                    dict(
                        id="sale",
                        domain="sales",
                        operation="summary",
                        metric="amount",
                        time="9月3号",
                    ),
                    dict(
                        id="ret",
                        domain="returns",
                        operation="comparison",
                        metric="orders",
                        target="buyer",
                        time="9月2号",
                        order=order,
                    ),
                ],
                issues=[
                    dict(
                        intent_id="ret",
                        field="comparison_time",
                        kind=issue_kind,
                        question="还需要明确比较期与关联条件",
                    )
                ]
                if issue_kind
                else [],
            )

    planner = SemanticPlanner(provider=ComparisonProvider())
    first = await converse(
        ConversationRequest(question="9月3号销售；9月2号退货单数比较，按客户从低到高"),
        multi_report,
        planner,
        date(2026, 9, 22),
    )
    assert first.turn.status == "capability_gap"
    for choice in first.turn.choices:
        result = await converse(
            ConversationRequest(choice_id=choice.id),
            multi_report,
            planner,
            date(2026, 9, 22),
            # Each alternative is a separate imported fixture conversation.
            previous=first.context.model_copy(update={"runtime_ref": None}),
        )
        if issue_kind == "dependency":
            assert result.turn.result is None
            assert any(i.kind == "dependency" for i in result.context.issues)
            continue
        assert result.turn.status == "result", (choice.label, result.turn.clarification)
        assert not result.context.issues
        assert result.context.intents[0] == first.context.intents[0]
        result_step = result.turn.result.plan.steps[1]
        if order == "ascending":
            if result_step.kind.endswith("ranking"):
                assert result_step.order == "ascending"
            else:
                assert "不再排序" in choice.label
                assert result_step.order == "descending"  # Schema default; non-ranking ignores it.


@pytest.mark.anyio
@pytest.mark.parametrize("issue_target", [None, "sale"])
async def test_cancelling_return_comparison_preserves_other_or_unscoped_issue(
    multi_report, issue_target
):
    class ScopedProvider:
        async def interpret(self, request):
            return SemanticRequest(
                intents=[
                    dict(id="sale", domain="sales", operation="comparison", time="9月3号"),
                    dict(id="ret", domain="returns", operation="comparison", time="9月2号"),
                ],
                issues=[
                    dict(
                        intent_id=issue_target,
                        field="comparison_time",
                        kind="missing",
                        question="其他比较仍需补充比较期",
                    )
                ],
            )

    planner = SemanticPlanner(provider=ScopedProvider())
    first = await converse(
        ConversationRequest(question="比较9月3号销售和9月2号退货各自的变化"),
        multi_report,
        planner,
        date(2026, 9, 22),
    )
    original_issue = first.context.issues[0]
    for choice in first.turn.choices:
        result = await converse(
            ConversationRequest(choice_id=choice.id),
            multi_report,
            planner,
            date(2026, 9, 22),
            # Each alternative is a separate imported fixture conversation.
            previous=first.context.model_copy(update={"runtime_ref": None}),
        )
        assert result.turn.result is None
        assert original_issue in result.context.issues


def test_four_domain_signed_dialogue_changes_only_selected_goal(multi_report):
    class EditingProvider:
        def __init__(self):
            self.requests = []

        async def interpret(self, request):
            self.requests.append(request)
            if request.previous is None:
                return SemanticRequest(
                    intents=[
                        dict(domain="sales", operation="summary", time="2026-09-02"),
                        dict(domain="returns", operation="summary", time="2026-09-02"),
                        dict(
                            domain="shipping",
                            operation="list",
                            metric="quantity",
                            target="product",
                            time="2026-09-03",
                        ),
                        dict(
                            domain="inventory",
                            operation="ranking",
                            target="product",
                            time="2026-09-22",
                        ),
                    ]
                )
            returns = next(i for i in request.previous.intents if i.domain == "returns")
            return SemanticRequest(
                mode="followup",
                intents=[dict(id=returns.id, operation="ranking", target="buyer", metric="orders")],
            )

    provider = EditingProvider()
    client = TestClient(
        create_app(
            ApiSettings(),
            report=multi_report,
            analysis_planner=SemanticPlanner(provider=provider),
        )
    )

    def post(payload):
        return client.post("/api/v1/analysis/converse", headers=HEADERS, json=payload)

    first_response = post({"question": "分别查9月2号销售和退货、9月3号出库、9月22号库存"})
    assert first_response.status_code == 200
    first = first_response.json()
    assert first["status"] == "data_gap" and first["result"] is None
    assert len(first["draft"]["intents"]) == 4
    choice = next(c for c in first["choices"] if "2026-09-16" in c["label"])
    selected_response = post(
        {
            "choice_id": choice["id"],
            "conversation_token": first["conversation_token"],
        }
    )
    assert selected_response.status_code == 200
    selected = selected_response.json()
    assert selected["status"] == "result"
    assert len(provider.requests) == 1  # A signed suggestion does not call the cloud model.
    steps = {s["domain"]: s for s in selected["result"]["plan"]["steps"]}
    assert {d: s["start_date"] for d, s in steps.items()} == {
        "sales": "2026-09-02",
        "returns": "2026-09-02",
        "shipping": "2026-09-03",
        "inventory": "2026-09-16",
    }
    results = {r["domain"]: r for r in selected["result"]["results"]}
    assert results["returns"]["totals"]["amount"] == "35.0000"
    assert results["shipping"]["totals"]["document_count"] == 2
    assert results["inventory"]["totals"]["as_of"] == multi_report.metadata.source_as_of.isoformat()

    changed_response = post(
        {
            "question": "只把退货改成客户退货单数排行，其他三项不动",
            "conversation_token": selected["conversation_token"],
        }
    )
    assert changed_response.status_code == 200
    changed = changed_response.json()
    assert changed["status"] == "result"
    assert len(provider.requests) == 2
    changed_steps = {s["domain"]: s for s in changed["result"]["plan"]["steps"]}
    assert len(changed_steps) == 4
    for domain in ("sales", "shipping", "inventory"):
        assert changed_steps[domain] == steps[domain]
    assert changed_steps["returns"]["kind"] == "buyer_ranking"
    assert changed_steps["returns"]["metric"] == "orders"
    assert changed_steps["returns"]["start_date"] == "2026-09-02"
    before_ids = {i["fields"]["domain"]: i["id"] for i in selected["draft"]["intents"]}
    after_ids = {i["fields"]["domain"]: i["id"] for i in changed["draft"]["intents"]}
    assert before_ids == after_ids
    stale = post({"choice_id": choice["id"], "conversation_token": changed["conversation_token"]})
    assert stale.status_code == 409
    assert len(provider.requests) == 2
