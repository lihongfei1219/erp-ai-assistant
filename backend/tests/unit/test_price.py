import asyncio
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.analysis.analytics import execute_analysis
from app.analysis.planning import resolve_question
from app.analysis.sales_query import QueryUnavailable
from app.capabilities.view import capability_view
from app.core.settings import ApiSettings
from app.main import create_app
from app.schemas.analytics import AnalysisPlan, AnalysisQuestion, AnalysisStep
from app.schemas.operations import OperationsSnapshot
from app.semantic.provider import SemanticPlanner
from app.semantic.schemas import SemanticRequest


def step(**values):
    return AnalysisStep(
        domain="shipping",
        kind="price",
        metric="unit_price",
        start_date="2026-08-01",
        end_date_exclusive="2026-09-01",
        **values,
    )


def result(report, **values):
    return execute_analysis(report, AnalysisPlan(steps=[step(**values)])).results[0]


def quantities(report, values):
    data = report.operations.model_dump()
    for part, items in zip(data["shipping_history"], values, strict=True):
        for doc, value in zip(part["documents"], items, strict=True):
            doc["lines"][0]["quantity"] = Decimal(value)
        part["control_quantities"] = {"盒": sum(Decimal(v) for v in items)}
    return report.model_copy(update={"operations": OperationsSnapshot.model_validate(data)})


def test_weighted_prices_are_not_average_of_line_prices(growth_report):
    report = quantities(growth_report, [("10", "10", "20"), ("10", "10"), ("10",)])
    rows = result(report).rows
    assert [(r["current_price"], r["previous_price"], r["delta"]) for r in rows] == [
        ("4.0000", "5.0000", "-1.0000"),
        ("4.0000", "20.0000", "-16.0000"),
    ]
    assert rows[0]["change_percent"] == "-20.00%"
    assert rows[0]["common_customers"] == 2
    assert "成本数据未覆盖" in rows[0]["cost_status"]


def test_customer_mix_can_change_average_without_customer_repricing(growth_report):
    report = quantities(growth_report, [("10", "4", "8"), ("6", "8"), ("20",)])
    overall = result(report, growth_basis="previous").rows[0]
    assert overall["direction"] == "上涨"
    customers = result(
        report,
        growth_basis="previous",
        dimension="buyer",
        filters=[dict(field="product", code="P")],
    ).rows
    assert {r["code"]: r["direction"] for r in customers} == {
        "A": "持平",
        "B": "持平",
        "C": "不可比",
    }
    assert next(r for r in customers if r["code"] == "C")["previous_price"] is None


@pytest.mark.parametrize("values", [("0", "10", "20"), ("0", "0", "0")])
def test_zero_quantity_is_not_zero_price(growth_report, values):
    report = quantities(growth_report, [values, ("10", "10"), ("10",)])
    r = result(report).rows[0]
    assert r["current_price"] is None and r["delta"] is None
    assert "待核实" in r["record_state"]


def test_zero_base_price_has_no_rate_but_keeps_delta(growth_report):
    data = growth_report.operations.model_dump()
    part = data["shipping_history"][1]
    part["control_amount"] = Decimal(0)
    for doc in part["documents"]:
        doc["amount"] = doc["lines"][0]["amount"] = Decimal(0)
    report = growth_report.model_copy(
        update={"operations": OperationsSnapshot.model_validate(data)}
    )
    r = result(report, growth_basis="previous").rows[0]
    assert r["previous_price"] == "0.0000" and r["delta"] == "1.0000"
    assert r["change_rate"] is None
    sorted_result = result(report, growth_basis="previous", growth_sort="rate")
    assert sorted_result.rows == [] and sorted_result.totals["groups"][0]["increase"] == 1


@pytest.mark.parametrize(
    "field,value", [("specification", "不同规格"), ("manufacturer", "不同厂家"), ("unit", "瓶")]
)
def test_changed_variant_never_compares_prices(growth_report, field, value):
    data = growth_report.operations.model_dump()
    part = data["shipping_history"][0]
    for doc in part["documents"]:
        doc["lines"][0][field] = value
    if field == "unit":
        part["control_quantities"] = {value: Decimal(160)}
    report = growth_report.model_copy(
        update={"operations": OperationsSnapshot.model_validate(data)}
    )
    rows = result(report, growth_basis="previous").rows
    assert len(rows) == 2 and all(r["delta"] is None for r in rows)


def test_filters_and_limits_preserve_summary(growth_report):
    report = quantities(growth_report, [("10", "10", "20"), ("10", "10"), ("10",)])
    r = result(
        report,
        dimension="buyer",
        growth_basis="previous",
        top_n=1,
        filters=[
            dict(field="product", code="P"),
            dict(field="buyer", code="C", operator="exclude"),
        ],
    )
    assert {r["code"] for r in r.rows} == {"A", "B"}
    assert r.totals["groups"][0]["unavailable"] == 0
    r = result(report, growth_basis="previous", growth_direction="increase")
    assert r.rows == [] and r.totals["groups"][0]["decrease"] == 1


def test_missing_coverage_and_missing_product_fail_closed(multi_report, growth_report):
    assert "price" not in capability_view(multi_report)["domains"]["shipping"]["operations"]
    with pytest.raises(QueryUnavailable, match="完整出库数据未覆盖"):
        result(multi_report)
    with pytest.raises(QueryUnavailable, match="一个品种"):
        result(growth_report, dimension="buyer")
    with pytest.raises(ValidationError):
        AnalysisStep(**dict(step().model_dump(), metric="amount"))


def test_price_api_evidence_export_and_private_followup(growth_report):
    class Provider:
        requests = []

        async def interpret(self, request):
            self.requests.append(request)
            return SemanticRequest(mode="followup", intents=[dict(growth_basis="year_over_year")])

    provider = Provider()
    client = TestClient(
        create_app(
            ApiSettings(), report=growth_report, analysis_planner=SemanticPlanner(provider=provider)
        )
    )
    query = step(
        dimension="buyer",
        top_n=1,
        filters=[
            dict(
                field="product",
                code="P",
                variant=dict(specification="合成规格", manufacturer="合成厂家", unit="盒"),
            )
        ],
    ).model_dump(mode="json")
    response = client.post("/api/v1/analysis/run", json={"steps": [query]})
    assert response.status_code == 200, response.text
    assert response.json()["results"][0]["kind"] == "price"
    exported = client.post("/api/v1/analysis/price/export", json=query)
    assert exported.status_code == 200
    assert len(exported.json()["rows"]) == 6
    evidence = client.post(
        "/api/v1/analysis/price/evidence",
        json=dict(
            step=query,
            period="current",
            product_code="P",
            specification="合成规格",
            manufacturer="合成厂家",
            unit="盒",
            buyer_code="B",
        ),
    )
    assert evidence.status_code == 200 and evidence.json()["total"] == 1
    assert evidence.json()["items"][0]["amount"] == "20"
    followup = client.post(
        "/api/v1/analysis/converse",
        json=dict(
            question="换成去年同期", conversation_token=response.json()["conversation_token"]
        ),
    )
    assert followup.status_code == 200 and followup.json()["status"] == "result", followup.text
    plan = followup.json()["result"]["plan"]["steps"][0]
    assert plan["kind"] == "price" and plan["filters"][0]["variant"]["unit"] == "盒"
    assert "合成厂家" not in str(provider.requests[0])
    assert not provider.requests[0].previous.entity_bindings


def test_cloud_price_intent_gets_correct_default_and_gap(growth_report):
    class Planner:
        async def interpret(self, request):
            return SemanticRequest(
                intents=[
                    dict(
                        domain="shipping",
                        operation="price",
                        target="product",
                        time="2026-08-01至2026-08-31",
                    )
                ]
            )

    planned = asyncio.run(
        resolve_question(
            AnalysisQuestion(question="比较八月售价变化"),
            growth_report,
            Planner(),
            date(2026, 10, 9),
        )
    )
    assert planned.plan is not None, planned.semantic.message
    assert planned.plan.steps[0].metric == "unit_price"
    from app.analysis.growth_guidance import date_gap_message

    message = date_gap_message(
        growth_report,
        SemanticRequest(intents=[dict(operation="price", time="2026-10-01至2026-10-31")]).intents[
            0
        ],
        date(2026, 10, 9),
    )
    assert "加权平均售价" in message and "出库金额变化" not in message


def test_feishu_price_shows_prices_without_fake_totals(growth_report):
    from app.integrations.feishu_analytics_layouts import details, metrics

    r = result(growth_report)
    assert metrics(r, "CNY") == []
    columns, rows = details(step(), r, "CNY", 5)
    assert "current_price" in dict(columns)
    assert rows[0]["current_price"] == "1.0000"
