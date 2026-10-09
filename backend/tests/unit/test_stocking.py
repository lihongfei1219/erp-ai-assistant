from datetime import date

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.analysis.analytics import execute_analysis
from app.analysis.sales_query import QueryUnavailable
from app.analysis.stocking import last_year
from app.analysis.stocking_api import StockingScenario, calculate_scenario
from app.core.settings import ApiSettings
from app.main import create_app
from app.schemas.analytics import AnalysisPlan, AnalysisStep
from app.schemas.operations import OperationsSnapshot
from app.semantic.provider import SemanticPlanner
from app.semantic.schemas import SemanticRequest


def step(**values):
    data = dict(
        domain="inventory",
        kind="stocking",
        metric="stock",
        start_date="2026-09-17",
        end_date_exclusive="2026-09-24",
    )
    data.update(values)
    return AnalysisStep.model_validate(data)


def body(**values):
    data = dict(
        step=step(),
        product_code="P",
        variant=dict(specification="合成规格", manufacturer="合成厂家", unit="盒"),
        in_transit_quantity="10",
        lead_time_days=3,
        expected_arrival_date="2026-09-17",
    )
    data.update(values)
    return StockingScenario.model_validate(data)


def test_reference_and_scenario_exclude_expiry_and_preseason_consumption(risk_report):
    result = execute_analysis(risk_report, AnalysisPlan(steps=[step()])).results[0]
    row = result.rows[0]
    assert row["previous_quantity"] == "60.0000" and row["recent_quantity"] == "30.0000"
    assert row["stock_quantity"] == "35.0000" and row["eligible_stock"] == "20.0000"
    assert row["excluded_stock"] == "15.0000"
    scenario = calculate_scenario(risk_report, body())
    assert scenario["consumption_before_target"] == "1.0000"
    assert scenario["projected_start_stock"] == "19.0000"
    assert scenario["estimated_gap"] == "31.0000"
    assert scenario["latest_order_date"] == "2026-09-14" and scenario["lead_time_tight"]


def test_late_transit_and_manual_zero_demand_are_explicit(risk_report):
    result = calculate_scenario(risk_report, body(expected_arrival_date="2026-09-18"))
    assert result["counted_in_transit"] == "0.0000" and result["late_in_transit"] == "10.0000"
    assert result["estimated_gap"] == "41.0000"
    result = calculate_scenario(risk_report, body(expected_demand="0"))
    assert result["expected_demand"] == "0.0000" and result["estimated_gap"] == "0.0000"
    assert result["demand_source"] == "手动填写"


def test_missing_arrival_negative_quantities_and_variant_mismatch_rejected(risk_report):
    with pytest.raises(QueryUnavailable, match="到货日期"):
        calculate_scenario(risk_report, body(expected_arrival_date=None))
    with pytest.raises(QueryUnavailable, match="到货日期"):
        calculate_scenario(risk_report, body(expected_arrival_date="2026-09-15"))
    for patch in [
        dict(in_transit_quantity="-1"),
        dict(expected_demand="NaN"),
        dict(lead_time_days=-1),
    ]:
        with pytest.raises(ValidationError):
            body(**patch)
    with pytest.raises(QueryUnavailable, match="规格"):
        calculate_scenario(
            risk_report,
            body(variant=dict(specification="其他规格", manufacturer="合成厂家", unit="盒")),
        )


def test_zero_historical_demand_requires_manual_input(risk_report):
    query = step(start_date="2026-09-22", end_date_exclusive="2026-09-24")
    with pytest.raises(QueryUnavailable, match="手动填写目标需求"):
        calculate_scenario(risk_report, body(step=query))
    assert (
        calculate_scenario(risk_report, body(step=query, expected_demand="12"))["demand_source"]
        == "手动填写"
    )


def test_unknown_expiry_blocks_estimate_not_zero_stock(risk_report):
    data = risk_report.operations.model_dump()
    data["inventory"]["records"][0]["expiry_date"] = None
    report = risk_report.model_copy(update={"operations": OperationsSnapshot.model_validate(data)})
    with pytest.raises(QueryUnavailable, match="效期缺失"):
        calculate_scenario(report, body())


def test_target_history_scope_and_leap_day_are_bounded(risk_report, multi_report):
    assert last_year(date(2024, 2, 29)) == date(2023, 2, 28)
    for query in [
        step(start_date="2026-09-16"),
        step(start_date="2026-10-01", end_date_exclusive="2026-11-01"),
    ]:
        with pytest.raises(QueryUnavailable):
            execute_analysis(risk_report, AnalysisPlan(steps=[query]))
    with pytest.raises(QueryUnavailable):
        execute_analysis(multi_report, AnalysisPlan(steps=[step()]))
    restricted = risk_report.model_copy(
        update={
            "metadata": risk_report.metadata.model_copy(
                update={
                    "scope": risk_report.metadata.scope.model_copy(update={"all_buyers": False})
                }
            )
        }
    )
    with pytest.raises(QueryUnavailable):
        execute_analysis(restricted, AnalysisPlan(steps=[step()]))
    with pytest.raises(ValidationError):
        step(age_threshold_days=120)


def test_export_and_scenario_share_validated_plan_and_do_not_mutate(risk_report):
    before = risk_report.model_dump_json()
    client = TestClient(create_app(ApiSettings(), report=risk_report))
    response = client.post("/api/v1/analysis/stocking/export", json=step().model_dump(mode="json"))
    assert response.status_code == 200 and len(response.json()["rows"]) == 1
    response = client.post(
        "/api/v1/analysis/stocking/scenario", json=body().model_dump(mode="json")
    )
    assert response.status_code == 200 and response.json()["estimated_gap"] == "31.0000"
    response = client.post(
        "/api/v1/analysis/stocking/scenario",
        json=body(product_code="OTHER").model_dump(mode="json"),
    )
    assert response.status_code == 422
    assert risk_report.model_dump_json() == before


def test_semantic_target_period_is_not_rewritten_to_inventory_date(risk_report):
    class Provider:
        async def interpret(self, request):
            return SemanticRequest(
                intents=[
                    dict(
                        domain="inventory",
                        operation="stocking",
                        metric="stock",
                        target="product",
                        time="2026-09-17至2026-09-23",
                    )
                ]
            )

    client = TestClient(
        create_app(
            ApiSettings(), report=risk_report, analysis_planner=SemanticPlanner(provider=Provider())
        )
    )
    response = client.post(
        "/api/v1/analysis/converse",
        json=dict(question="评估2026年9月17日至23日的备货依据", conversation_token=None),
    )
    assert response.status_code == 200 and response.json()["status"] == "result", response.text
    assert response.json()["result"]["plan"]["steps"][0]["start_date"] == "2026-09-17"
