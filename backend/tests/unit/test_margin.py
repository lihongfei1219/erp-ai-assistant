from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.analysis.analytics import execute_analysis
from app.analysis.price import execute_price
from app.analysis.sales_query import QueryUnavailable
from app.capabilities.view import capability_view
from app.core.settings import ApiSettings
from app.main import create_app
from app.schemas.analytics import AnalysisPlan, AnalysisStep
from app.schemas.operations import DocumentFacts, OperationsSnapshot
from app.semantic.provider import SemanticPlanner
from app.semantic.schemas import SemanticRequest


def step(**values):
    return AnalysisStep(
        domain="shipping",
        kind="margin",
        metric="gross_profit",
        start_date="2026-08-01",
        end_date_exclusive="2026-09-01",
        **values,
    )


def result(report, **values):
    return execute_analysis(report, AnalysisPlan(steps=[step(**values)])).results[0]


def test_volume_growth_without_profit_growth_and_bridge(margin_report):
    r = result(margin_report, growth_basis="previous")
    row = r.rows[0]
    assert row["current_amount"] == "160.0000"
    assert row["current_cost"] == "144.0000"
    assert row["current_profit"] == "16.0000" and row["previous_profit"] == "50.0000"
    assert row["delta"] == "-34.0000"
    assert row["volume_up_profit_not_up"] is True
    assert row["volume_effect"] == "30.0000"
    assert row["price_effect"] == "0.0000"
    assert row["cost_effect"] == "-64.0000"
    assert sum(
        Decimal(row[k])
        for k in ("volume_effect", "price_effect", "cost_effect", "rounding_adjustment")
    ) == Decimal(row["delta"])
    assert r.totals["groups"][0]["attention"] == 1


def test_price_space_uses_saved_cost_not_current_inventory(margin_report):
    r = execute_price(
        margin_report, step().model_copy(update={"kind": "price", "metric": "unit_price"})
    )
    row = r.rows[0]
    assert row["current_unit_cost"] == "0.9000" and row["previous_unit_cost"] == "0.5000"
    assert row["current_spread"] == "0.1000" and row["spread_delta"] == "-0.4000"


def test_old_snapshot_never_invents_zero_cost(growth_report):
    assert "margin" not in capability_view(growth_report)["domains"]["shipping"]["operations"]
    with pytest.raises(QueryUnavailable, match="缺少已核验"):
        result(growth_report)
    r = execute_price(
        growth_report, step().model_copy(update={"kind": "price", "metric": "unit_price"})
    )
    assert r.rows[0]["current_unit_cost"] is None


@pytest.mark.parametrize(
    "field,value", [("control_purchase_cost", "1"), ("comparison_ready", False)]
)
def test_cost_reconciliation_rejects_false_controls(margin_report, field, value):
    data = margin_report.operations.shipping_history[0].model_dump()
    data[field] = value
    with pytest.raises(ValidationError, match="采购成本"):
        DocumentFacts.model_validate(data)


def test_missing_cost_line_and_negative_price_rejected(margin_report):
    data = margin_report.operations.shipping_history[0].model_dump()
    for value in (None, "-1"):
        data["documents"][0]["lines"][0]["purchase_unit_cost"] = value
        with pytest.raises(ValidationError):
            DocumentFacts.model_validate(data)


def test_new_customer_has_profit_delta_but_no_fake_price_bridge(margin_report):
    r = result(
        margin_report,
        dimension="buyer",
        filters=[dict(field="product", code="P")],
        growth_basis="previous",
        top_n=1,
    )
    row = next(r for r in r.rows if r["code"] == "C")
    assert row["previous_profit"] == "0.0000" and row["current_profit"] == "4.0000"
    assert row["volume_effect"] is None and row["change_rate"] is None
    assert r.totals["groups"][0]["delta"] == "-34.0000"


def test_negative_base_profit_is_not_normal_growth_rate(margin_report):
    from tests.growth_fixtures import with_cost

    data = margin_report.operations.model_dump()
    data["shipping_history"][1] = with_cost(margin_report.operations.shipping_history[1], "1.1")
    report = margin_report.model_copy(
        update={"operations": OperationsSnapshot.model_validate(data)}
    )
    row = result(report, growth_basis="previous").rows[0]
    assert row["previous_profit"] == "-10.0000"
    assert row["delta"] == "26.0000" and row["change_rate"] is None
    assert result(report, growth_basis="previous", growth_sort="rate").rows == []


def test_margin_api_export_evidence_followup_privacy(margin_report):
    class Provider:
        requests = []

        async def interpret(self, request):
            self.requests.append(request)
            return SemanticRequest(mode="followup", intents=[dict(growth_basis="year_over_year")])

    provider = Provider()
    client = TestClient(
        create_app(
            ApiSettings(), report=margin_report, analysis_planner=SemanticPlanner(provider=provider)
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
    run = client.post("/api/v1/analysis/run", json=dict(steps=[query]))
    assert run.status_code == 200, run.text
    exported = client.post("/api/v1/analysis/margin/export", json=query)
    assert exported.status_code == 200 and len(exported.json()["rows"]) == 6
    evidence = client.post(
        "/api/v1/analysis/margin/evidence",
        json=dict(
            step=query,
            period="current",
            product_code="P",
            specification="合成规格",
            manufacturer="合成厂家",
            unit="盒",
            buyer_code="A",
        ),
    )
    assert (
        evidence.status_code == 200 and evidence.json()["items"][0]["purchase_unit_cost"] == "0.9"
    )
    turn = client.post(
        "/api/v1/analysis/converse",
        json=dict(question="换成去年同期", conversation_token=run.json()["conversation_token"]),
    )
    assert turn.status_code == 200 and turn.json()["status"] == "result", turn.text
    assert turn.json()["result"]["plan"]["steps"][0]["kind"] == "margin"
    assert "合成厂家" not in str(provider.requests[0])
    assert "purchase_unit_cost" not in str(provider.requests[0])
