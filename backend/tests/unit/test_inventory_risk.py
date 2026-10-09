from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.analysis.analytics import execute_analysis
from app.analysis.sales_query import QueryUnavailable
from app.core.settings import ApiSettings
from app.main import create_app
from app.schemas.analytics import AnalysisPlan, AnalysisStep
from app.schemas.operations import OperationsSnapshot
from app.semantic.compiler import context_from_plan
from app.semantic.provider import SemanticPlanner
from app.semantic.schemas import SemanticRequest


def step(**values):
    return AnalysisStep(
        domain="inventory",
        kind="inventory_risk",
        metric="stock",
        start_date="2026-09-16",
        end_date_exclusive="2026-09-17",
        **values,
    )


def result(report, **values):
    return execute_analysis(report, AnalysisPlan(steps=[step(**values)])).results[0]


def test_fefo_accumulates_stock_and_excludes_expired(risk_report):
    r = result(risk_report)
    rows = {row["batch_code"]: row for row in r.rows}
    assert rows["B1"]["daily_quantity"] == "1.0000"
    assert rows["B1"]["estimated_clear_days"] == "10.0000"
    assert rows["B2"]["estimated_clear_days"] == "30.0000"
    assert rows["B3"]["expired"] is True and rows["B3"]["estimated_clear_days"] is None
    assert rows["B1"]["expiry_mismatch"] and rows["B2"]["expiry_mismatch"]
    assert r.totals["occupied_amount"] == "70.0000" and r.totals["quantities"] == {"盒": "35.0000"}
    assert r.rows[0]["batch_code"] == "B3"


def test_user_thresholds_change_flags_not_inventory_values(risk_report):
    r = result(risk_report, age_threshold_days=150, expiry_threshold_days=7, top_n=1)
    assert r.totals["aged"] == 0 and r.totals["near_expiry"] == 1
    assert r.totals["batch_count"] == 3 and len(r.rows) == 1
    assert r.totals["occupied_amount"] == "70.0000"
    context = context_from_plan(AnalysisPlan(steps=[step(age_threshold_days=150)]))
    assert context.intents[0].age_threshold_days == 150


def test_no_sales_does_not_forecast_zero_days(risk_report):
    data = risk_report.operations.model_dump()
    for row in data["inventory"]["records"]:
        row["product_code"] = "Q"
    report = risk_report.model_copy(update={"operations": OperationsSnapshot.model_validate(data)})
    r = result(report)
    assert all(row["no_recent_sales"] and row["estimated_clear_days"] is None for row in r.rows)


def test_variant_mismatch_is_not_no_sales(risk_report):
    data = risk_report.operations.model_dump()
    for row in data["inventory"]["records"]:
        row["specification"] = "不同规格"
    report = risk_report.model_copy(update={"operations": OperationsSnapshot.model_validate(data)})
    r = result(report)
    assert all(
        row["data_issue"] and not row["no_recent_sales"] and row["daily_quantity"] is None
        for row in r.rows
    )


def test_missing_expiry_blocks_product_forecast_and_missing_cost_not_zero(risk_report):
    data = risk_report.operations.model_dump()
    data["inventory"]["records"][0].update(
        expiry_date=None, purchase_unit_cost=None, received_date=None
    )
    data["inventory"]["control_known_cost"] = Decimal(50)
    report = risk_report.model_copy(update={"operations": OperationsSnapshot.model_validate(data)})
    r = result(report)
    assert r.totals["occupied_amount"] is None and r.totals["known_occupied_amount"] == "50.0000"
    assert all(row["estimated_clear_days"] is None for row in r.rows)
    assert next(row for row in r.rows if row["batch_code"] == "B1")["age_days"] is None


def test_scope_date_and_incomplete_velocity_are_rejected(risk_report, multi_report):
    with pytest.raises(QueryUnavailable):
        result(multi_report)
    with pytest.raises(QueryUnavailable, match="完整出库数据未覆盖"):
        result(risk_report, lookback_days=90)
    restricted = risk_report.model_copy(
        update={
            "metadata": risk_report.metadata.model_copy(
                update={"scope": dict(all_buyers=False, buyer_codes=["A"])}
            )
        }
    )
    from app.schemas.sales import DataScope

    restricted = restricted.model_copy(
        update={
            "metadata": restricted.metadata.model_copy(
                update={"scope": DataScope(all_buyers=False, buyer_codes=["A"])}
            )
        }
    )
    with pytest.raises(QueryUnavailable, match="授权"):
        result(restricted)
    with pytest.raises(ValidationError):
        step(lookback_days=0)
    bad = step().model_copy(
        update={
            "start_date": step().start_date.replace(day=15),
            "end_date_exclusive": step().end_date_exclusive.replace(day=16),
        }
    )
    with pytest.raises(QueryUnavailable, match="备份时点"):
        execute_analysis(risk_report, AnalysisPlan(steps=[bad]))


def test_export_and_batch_evidence_enforce_scope(risk_report):
    client = TestClient(create_app(ApiSettings(), report=risk_report))
    query = step(top_n=1).model_dump(mode="json")
    exported = client.post("/api/v1/analysis/inventory-risk/export", json=query)
    assert exported.status_code == 200 and len(exported.json()["rows"]) == 3
    evidence = client.post(
        "/api/v1/analysis/inventory-risk/evidence", json=dict(step=query, record_ids=[1])
    )
    assert evidence.status_code == 200 and evidence.json()["items"][0]["quantity"] == "10"
    denied = client.post(
        "/api/v1/analysis/inventory-risk/evidence", json=dict(step=query, record_ids=[99])
    )
    assert denied.status_code == 422


def test_semantic_inventory_thresholds_survive_compilation(risk_report):
    class Provider:
        async def interpret(self, request):
            return SemanticRequest(
                intents=[
                    dict(
                        domain="inventory",
                        operation="inventory_risk",
                        metric="stock",
                        target="product",
                        time="2026-09-16",
                        lookback_days=30,
                        age_threshold_days=120,
                        expiry_threshold_days=30,
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
        json=dict(
            question="检查2026年9月16日库存积压，库龄120天、效期30天", conversation_token=None
        ),
    )
    assert response.status_code == 200 and response.json()["status"] == "result", response.text
    assert response.json()["result"]["plan"]["steps"][0]["age_threshold_days"] == 120
