"""Filtering must preserve financial grain, authorization, and conversational constraints."""

import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.analysis.analytics import analysis_evidence, execute_analysis
from app.analysis.object_filters import entity_directory, resolve_entity
from app.analysis.sales_query import QueryUnavailable
from app.capabilities.registry import REGISTRY
from app.capabilities.view import date_bounds
from app.core.settings import ApiSettings
from app.main import create_app
from app.schemas.analytics import AnalysisEvidenceRequest, AnalysisPlan
from app.semantic.provider import SemanticPlanner
from tests.api.test_guided_dialogue import Provider, post
from tests.unit.test_operations import plan


def condition(code, field="product", operator="include"):
    return dict(field=field, operator=operator, code=code)


@pytest.mark.parametrize(
    "kind", ["summary", "trend", "product_ranking", "buyer_ranking", "anomalies"]
)
def test_sales_filtered_amount_is_line_amount_and_orders_are_distinct(multi_report, kind):
    filtered = plan("sales", kind, filters=[condition("SKU-A")])
    before = multi_report.model_dump_json()
    result = execute_analysis(multi_report, filtered).results[0]
    assert result.totals["amount"] == "260.0000"
    assert result.totals["order_count"] == 2
    assert before == multi_report.model_dump_json()
    if kind == "buyer_ranking":
        assert result.rows[0]["amount"] == "260.0000"
    if result.chart:
        assert "300.0" not in json.dumps(result.chart)


def test_comparison_filters_both_periods(multi_report):
    request = AnalysisPlan(
        steps=[
            dict(
                kind="comparison",
                start_date="2026-09-02",
                end_date_exclusive="2026-09-03",
                comparison_start_date="2026-09-01",
                comparison_end_date_exclusive="2026-09-02",
                filters=[condition("SKU-A")],
            )
        ]
    )
    result = execute_analysis(multi_report, request).results[0]
    assert result.totals["amount"] == "200.0000"
    assert result.totals["previous_amount"] == "60.0000"
    assert result.totals["delta"] == "140.0000"


@pytest.mark.parametrize("domain", ["returns", "shipping"])
def test_operational_filters_match_lines_and_distinct_documents(multi_report, domain):
    result = execute_analysis(multi_report, plan(domain, filters=[condition("A")])).results[0]
    assert result.totals["amount"] == "25.0000"
    assert result.totals["document_count"] == 2
    assert len(result.rows) == 1 and result.rows[0]["quantity"] == "2.5000"


def test_inventory_filter_retains_all_matching_batches(multi_report):
    result = execute_analysis(
        multi_report,
        plan("inventory", metric="stock", filters=[condition("B", operator="exclude")]),
    ).results[0]
    assert result.totals["batch_count"] == 2
    assert result.totals["product_count"] == 1
    assert result.rows[0]["quantity"] == "15.0000"


def test_union_exclusion_and_cross_field_intersection(multi_report):
    filters = [
        condition("SKU-A"),
        condition("SKU-B"),
        condition("SKU-A", operator="exclude"),
        condition("BUYER-A", field="buyer"),
    ]
    result = execute_analysis(multi_report, plan("sales", filters=filters)).results[0]
    assert result.totals == {"amount": "40.0000", "order_count": 1}
    result = execute_analysis(
        multi_report,
        plan("sales", filters=filters + [condition("BUYER-A", field="buyer", operator="exclude")]),
    ).results[0]
    assert result.totals == {"amount": "0.0000", "order_count": 0}


@pytest.mark.parametrize("cap", list(REGISTRY.values()), ids=lambda c: c.id)
def test_all_registered_analyses_accept_supported_product_filters(multi_report, cap):
    start, _ = date_bounds(multi_report, cap.domain)
    from datetime import timedelta

    values = dict(
        domain=cap.domain,
        kind=cap.kind,
        metric=cap.metrics[0],
        start_date=start,
        end_date_exclusive=start + timedelta(days=1),
        filters=[condition("SKU-A" if cap.domain == "sales" else "A")],
    )
    if cap.kind == "comparison":
        values.update(
            comparison_start_date=start + timedelta(days=1),
            comparison_end_date_exclusive=start + timedelta(days=2),
        )
    assert execute_analysis(multi_report, AnalysisPlan(steps=[values])).results


def test_unknown_and_inventory_buyer_filters_are_never_silently_ignored(multi_report):
    with pytest.raises(QueryUnavailable):
        execute_analysis(multi_report, plan("sales", filters=[condition("NOT-AUTHORIZED")]))
    with pytest.raises(ValidationError):
        plan("inventory", metric="stock", filters=[condition("BUYER-A", field="buyer")])
    restricted = multi_report.model_copy(
        update={
            "metadata": multi_report.metadata.model_copy(
                update={
                    "scope": multi_report.metadata.scope.model_copy(
                        update={
                            "all_buyers": False,
                            "buyer_codes": ("OTHER",),
                        }
                    ),
                }
            ),
        }
    )
    assert entity_directory(restricted, "sales", "product") == {}
    assert entity_directory(restricted, "inventory", "product") == {}


def test_names_codes_and_partial_matches_have_distinct_confirmation_rules():
    directory = {"A": {"同名药"}, "B": {"同名药"}, "C": {"唯一药品"}}
    assert resolve_entity(directory, "A") == ("A", [])
    assert resolve_entity(directory, "唯一药品") == ("C", [])
    assert resolve_entity(directory, "同名药") == (None, ["A", "B"])
    assert resolve_entity(directory, "唯一") == (None, ["C"])
    assert resolve_entity(directory, "不存在") == (None, [])


def test_filtered_evidence_has_only_matched_lines_and_correct_amount(multi_report):
    step = plan("sales", filters=[condition("SKU-A")]).steps[0]
    body = AnalysisEvidenceRequest(step=step, order_id=1)
    evidence = analysis_evidence(multi_report, body)
    assert evidence.amount == Decimal("60")
    assert [line.product_code for line in evidence.lines] == ["SKU-A"]
    with pytest.raises(QueryUnavailable):
        analysis_evidence(multi_report, body.model_copy(update={"order_id": 3}))
    with pytest.raises(QueryUnavailable):
        analysis_evidence(multi_report, body.model_copy(update={"product_code": "SKU-B"}))


def named_report(report):
    return report.model_copy(
        update={
            "evidence": [
                order.model_copy(
                    update={
                        "lines": [
                            line.model_copy(update={"product_name": "同名药"})
                            for line in order.lines
                        ]
                    }
                )
                for order in report.evidence
            ]
        }
    )


def intent(value="同名药", operator="include"):
    return dict(
        domain="sales",
        operation="summary",
        time="2026-09-01至2026-09-03",
        filters=[dict(field="product", operator=operator, value=value)],
    )


def test_same_name_selection_followup_privacy_and_evidence_endpoint(multi_report):
    report = named_report(multi_report)
    provider = Provider(
        {"intents": [intent()]}, {"mode": "followup", "intents": [{"operation": "trend"}]}
    )
    client = TestClient(
        create_app(
            ApiSettings(), report=report, analysis_planner=SemanticPlanner(provider=provider)
        )
    )
    first = post(client, question="2026-09-01至2026-09-03同名药卖了多少钱")
    assert first["status"] == "needs_input"
    assert first["clarification"]["kind"] == "entity_ambiguous"
    assert len(first["choices"]) == 2
    choice = next(c for c in first["choices"] if "SKU-A" in c["label"])
    selected = post(client, choice_id=choice["id"], conversation_token=first["conversation_token"])
    assert selected["status"] == "result"
    assert len(provider.requests) == 1
    assert selected["result"]["results"][0]["totals"]["amount"] == "260.0000"
    follow = post(client, question="再看趋势", conversation_token=selected["conversation_token"])
    assert follow["status"] == "result"
    assert follow["result"]["results"][0]["totals"]["amount"] == "260.0000"
    previous = provider.requests[-1].previous.model_dump_json()
    assert "SKU-A" not in previous and "SKU-B" not in previous
    step = follow["result"]["plan"]["steps"][0]
    response = client.post("/api/v1/analysis/evidence", json={"step": step, "order_id": 1})
    assert response.status_code == 200
    assert response.json()["item"]["amount"] == "60.0000"


@pytest.mark.parametrize("operator", ["include", "exclude"])
def test_missing_entity_requires_correction_even_for_exclusion(multi_report, operator):
    provider = Provider({"intents": [intent("不存在的对象", operator)]})
    client = TestClient(
        create_app(
            ApiSettings(), report=multi_report, analysis_planner=SemanticPlanner(provider=provider)
        )
    )
    result = post(client, question="2026-09-01至2026-09-03排查指定对象")
    assert result["status"] == "needs_input" and result["result"] is None
    assert result["clarification"]["kind"] == "entity_not_found"
    assert result["choices"] == []


def test_customer_name_filter_and_cross_turn_constraint_edits(multi_report):
    report = multi_report.model_copy(
        update={
            "evidence": [
                order.model_copy(update={"buyer_name": "客户甲"}) for order in multi_report.evidence
            ]
        }
    )
    initial = intent("SKU-A")
    initial["filters"].append(dict(field="buyer", value="客户甲"))
    provider = Provider({"intents": [initial]})
    client = TestClient(
        create_app(
            ApiSettings(), report=report, analysis_planner=SemanticPlanner(provider=provider)
        )
    )
    result = post(client, question="2026-09-01至2026-09-03查询客户甲的SKU-A")
    assert result["status"] == "result"
    goal = result["draft"]["intents"][0]
    product_id = goal["constraints"][0]["id"]
    provider.outputs.append(
        {
            "mode": "followup",
            "edits": [
                {
                    "intent_id": goal["id"],
                    "operation": "replace_filter",
                    "constraint_id": product_id,
                    "filter": {"field": "product", "operator": "exclude", "value": "SKU-A"},
                }
            ],
        }
    )
    result = post(
        client, question="改成排除这个商品", conversation_token=result["conversation_token"]
    )
    assert result["result"]["results"][0]["totals"]["amount"] == "40.0000"
    assert len(result["result"]["plan"]["steps"][0]["filters"]) == 2
    provider.outputs.append(
        {
            "mode": "followup",
            "edits": [
                {
                    "intent_id": goal["id"],
                    "operation": "remove_filter",
                    "constraint_id": product_id,
                }
            ],
        }
    )
    result = post(
        client, question="取消商品条件保留客户", conversation_token=result["conversation_token"]
    )
    assert result["result"]["results"][0]["totals"]["amount"] == "300.0000"
    assert result["result"]["plan"]["steps"][0]["filters"] == [
        condition("BUYER-A", "buyer", "equal")
    ]


def test_customer_same_name_is_not_merged_across_codes(multi_report):
    report = multi_report.model_copy(
        update={
            "evidence": [
                order.model_copy(update={"buyer_name": "同名客户", "buyer_code": f"C-{index}"})
                for index, order in enumerate(multi_report.evidence)
            ]
        }
    )
    initial = intent()
    initial["filters"] = [dict(field="buyer", value="同名客户")]
    provider = Provider({"intents": [initial]})
    client = TestClient(
        create_app(
            ApiSettings(), report=report, analysis_planner=SemanticPlanner(provider=provider)
        )
    )
    result = post(client, question="2026-09-01至2026-09-03同名客户的销售")
    assert result["status"] == "needs_input"
    choice = next(c for c in result["choices"] if "C-0" in c["label"])
    result = post(client, choice_id=choice["id"], conversation_token=result["conversation_token"])
    assert result["result"]["results"][0]["totals"]["amount"] == "100.0000"


def test_unknown_term_can_be_corrected_without_losing_other_filters(multi_report):
    initial = intent("错误名称")
    initial["filters"].append(dict(field="buyer", value="BUYER-A"))
    provider = Provider({"intents": [initial]})
    client = TestClient(
        create_app(
            ApiSettings(), report=multi_report, analysis_planner=SemanticPlanner(provider=provider)
        )
    )
    result = post(client, question="2026-09-01至2026-09-03查错误名称")
    goal = result["draft"]["intents"][0]
    provider.outputs.append(
        {
            "mode": "answer",
            "edits": [
                {
                    "intent_id": goal["id"],
                    "operation": "replace_filter",
                    "constraint_id": goal["constraints"][0]["id"],
                    "filter": {"field": "product", "operator": "include", "value": "SKU-B"},
                }
            ],
        }
    )
    result = post(
        client, question="商品编码是SKU-B", conversation_token=result["conversation_token"]
    )
    assert result["status"] == "result"
    assert result["result"]["results"][0]["totals"]["amount"] == "40.0000"
    assert len(result["result"]["plan"]["steps"][0]["filters"]) == 2


def test_feishu_numeric_entity_selection_and_other_user_cannot_select_it(multi_report):
    from app.integrations.feishu_sales import SalesBot
    from tests.unit.test_feishu_analytics import AnalyticsStore, ask
    from tests.unit.test_feishu_sales import config

    report = named_report(multi_report)
    provider = Provider({"intents": [intent()]})
    store = AnalyticsStore()
    settings = config().model_copy(update={"user_access_mode": "all_group_members"})

    def bot():
        return SalesBot(
            lambda: settings,
            "ou_bot",
            store,
            lambda: report,
            analysis_planner=SemanticPlanner(provider=provider),
        )

    pending, text = ask(bot(), store, "2026-09-01至2026-09-03同名药销售金额")
    assert "SKU-A" in text and "SKU-B" in text
    other, _ = ask(bot(), store, "1", user="ou_other")
    assert other["result"].get("semantic_status") != "result"
    result, _ = ask(bot(), store, "1")
    assert result["result"]["results"][0]["totals"]["amount"] == "260.0000"
    assert len(provider.requests) == 1
