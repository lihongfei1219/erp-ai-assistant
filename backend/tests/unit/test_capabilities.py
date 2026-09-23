"""Contracts between the registry, model view, API, guidance and real executors."""

import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.analysis.analytics import execute_analysis
from app.analysis.planning import finish_compilation, prepare_semantic_input
from app.capabilities.registry import DOMAINS, REGISTRY, registry_version
from app.capabilities.view import capability_view, date_bounds, model_capabilities
from app.core.settings import ApiSettings
from app.integrations.feishu_guidance import coverage_text
from app.main import create_app
from app.schemas.analytics import AnalysisPlan, AnalysisQuestion, AnalysisStep
from app.semantic.catalog import load_catalog
from app.semantic.compiler import compile_request
from app.semantic.context import snapshot_key
from app.semantic.schemas import SemanticRequest


@pytest.mark.parametrize("cap", list(REGISTRY.values()), ids=lambda c: c.id)
def test_every_registered_metric_runs_on_existing_executor(multi_report, cap):
    start, _ = date_bounds(multi_report, cap.domain)
    for metric in cap.metrics:
        params = dict(
            domain=cap.domain, kind=cap.kind, metric=metric,
            start_date=start, end_date_exclusive=start + timedelta(days=1),
        )
        if cap.operation == "comparison":
            params.update(
                comparison_start_date=start + timedelta(days=1),
                comparison_end_date_exclusive=start + timedelta(days=2),
            )
        for dimension in cap.dimensions:
            params["dimension"] = dimension
            if cap.ordered:
                params.update(order="ascending", top_n=1)
            plan = AnalysisPlan(steps=[params])
            result = execute_analysis(multi_report, plan).results[0]
            assert result.domain == cap.domain and result.kind == cap.kind


@pytest.mark.parametrize("variant", ["full", "sales_only", "restricted", "missing_returns"])
def test_api_model_and_feishu_share_actual_coverage(multi_report, variant):
    report = multi_report
    if variant == "sales_only":
        report = report.model_copy(update={"operations": None})
    elif variant == "missing_returns":
        report = report.model_copy(update={
            "operations": report.operations.model_copy(update={"returns": None})
        })
    elif variant == "restricted":
        report = report.model_copy(update={
            "metadata": report.metadata.model_copy(update={
                "scope": report.metadata.scope.model_copy(update={"all_buyers": False})
            }),
            "operations": report.operations.model_copy(update={"all_buyers": False}),
        })
    view = capability_view(report)
    request = prepare_semantic_input(
        AnalysisQuestion(question="了解一下经营情况"), report, date(2026, 9, 23)
    )
    assert request.capabilities["domain_capabilities"] == view["domains"]
    response = TestClient(create_app(ApiSettings(), report=report)).get("/api/v1/analysis/catalog")
    assert response.status_code == 200
    catalog = response.json()
    assert catalog["semantic_domains"] == view["domains"]
    assert catalog["capability_version"] == request.capabilities["version"] == registry_version()
    text = coverage_text(report)
    for entry in view["domains"].values():
        assert (entry["label"] + "：" in text) == entry["executable"]
        if not entry["executable"]:
            assert entry["capabilities"] == []
            assert entry["metrics"] == []
    if variant == "restricted":
        assert view["domains"]["inventory"]["reason_code"] == "permission_denied"
        assert "snapshot_as_of" not in view["domains"]["inventory"]
    if variant == "sales_only":
        assert request.capabilities["executable_domains"] == ["sales"]


def test_vocabulary_keeps_unknown_requests_without_advertising_execution():
    catalog = load_catalog()
    for domain, spec in DOMAINS.items():
        assert catalog["domains"][domain]["default_metric"] == spec.default_metric
        registered = [c for c in REGISTRY.values() if c.domain == domain]
        assert set(catalog["domains"][domain]["executable_operations"]) == {
            c.operation for c in registered
        }
    assert "profit" in catalog["domains"]["sales"]["metrics"]
    assert "profit" not in catalog["domains"]["sales"]["executable_metrics"]
    assert "trend" in catalog["domains"]["inventory"]["operations"]
    assert "trend" not in catalog["domains"]["inventory"]["executable_operations"]


@pytest.mark.parametrize("patch", [
    {"domain": "inventory", "kind": "trend", "metric": "stock"},
    {"domain": "inventory", "kind": "buyer_ranking", "metric": "stock"},
    {"domain": "sales", "kind": "product_ranking", "metric": "quantity"},
    {"domain": "returns", "kind": "comparison"},
    {"kind": "summary", "order": "ascending"},
    {"domain": "inventory", "kind": "list", "metric": "stock", "dimension": "buyer"},
])
def test_unregistered_combinations_cannot_bypass_public_plan(patch):
    with pytest.raises(ValidationError):
        AnalysisStep.model_validate({
            "kind": "summary", "start_date": "2026-09-01",
            "end_date_exclusive": "2026-09-02", **patch,
        })


def test_capability_changes_invalidate_snapshot_binding_without_leaking_rows(multi_report):
    view = model_capabilities(multi_report)
    text = json.dumps(view)
    for value in ("BUYER-A", "SKU-A", "DEMO-", "buyer_codes", "documents", "lines"):
        assert value not in text
    assert "snapshot_as_of" in text
    modified = multi_report.model_copy(update={"operations": None})
    assert snapshot_key(modified) != snapshot_key(multi_report)


def test_bad_reconciliation_and_empty_date_coverage_are_not_advertised(multi_report):
    broken = multi_report.model_copy(update={
        "quality": multi_report.quality.model_copy(update={"sql_control_totals_match": False})
    })
    assert capability_view(broken)["domains"]["sales"]["reason_code"] == "reconciliation_failed"
    recent = multi_report.model_copy(update={
        "metadata": multi_report.metadata.model_copy(update={
            "window": multi_report.metadata.window.model_copy(update={"start": date(2026, 9, 16)})
        })
    })
    assert capability_view(recent)["domains"]["sales"]["reason_code"] == "no_complete_dates"


def test_unavailable_sales_is_explained_without_losing_customer_request(multi_report):
    broken = multi_report.model_copy(update={
        "quality": multi_report.quality.model_copy(update={"sql_control_totals_match": False})
    })
    request = SemanticRequest(intents=[{
        "domain": "sales", "operation": "summary", "time": "2026-09-01",
    }])
    body = AnalysisQuestion(question="查看九月一号销售情况")
    compiled = compile_request(
        request, question=body.question, today=date(2026, 9, 23), domains=[],
    )
    outcome = finish_compilation(compiled, body, broken)
    assert outcome.plan is None
    assert "对账未通过" in outcome.semantic.message
    assert outcome.semantic.context.intents[0].domain == "sales"
