from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.analysis.analytics import execute_analysis
from app.analysis.sales_query import QueryUnavailable
from app.capabilities.view import capability_view
from app.core.comparison_periods import growth_periods
from app.schemas.analytics import AnalysisPlan, AnalysisStep
from app.schemas.operations import OperationsSnapshot


def step(**kwargs):
    return AnalysisStep(
        domain="shipping",
        kind="growth",
        start_date="2026-08-01",
        end_date_exclusive="2026-09-01",
        **kwargs,
    )


def result(report, **kwargs):
    return execute_analysis(report, AnalysisPlan(steps=[step(**kwargs)])).results[0]


def test_two_comparisons_have_independent_directions(growth_report):
    rows = result(growth_report).rows
    assert [(r["comparison"], r["delta"], r["change_percent"]) for r in rows] == [
        ("上期", "60.0000", "60.00%"),
        ("去年同期", "-40.0000", "-20.00%"),
    ]


def test_customer_contribution_reconciles_after_top_n(growth_report):
    r = result(
        growth_report,
        dimension="buyer",
        top_n=1,
        growth_basis="previous",
        filters=[dict(field="product", code="P")],
    )
    totals = r.totals["groups"][0]
    assert totals["positive"] == "80.0000"
    assert totals["negative"] == "20.0000"
    assert totals["increase_other"] == "40.0000"
    assert sum(Decimal(row["delta"]) for row in r.rows) + Decimal(totals["increase_other"]) == 60
    assert r.rows[0]["contribution_percent"] == "50.00%"


def test_customer_selection_and_zero_base(growth_report):
    r = result(
        growth_report,
        dimension="buyer",
        growth_basis="previous",
        filters=[dict(field="product", code="P")],
    )
    c = next(row for row in r.rows if row["code"] == "C")
    assert c["change_rate"] is None
    assert c["record_state"] == "比较期无出库"
    with pytest.raises(QueryUnavailable, match="一个品种"):
        result(growth_report, dimension="buyer")


def test_missing_history_is_not_zero(multi_report):
    assert "growth" not in capability_view(multi_report)["domains"]["shipping"]["operations"]
    with pytest.raises(QueryUnavailable, match="完整出库数据未覆盖"):
        result(multi_report)


def test_history_gap_does_not_become_available(growth_report):
    q = step().model_copy(
        update={"start_date": date(2026, 6, 1), "end_date_exclusive": date(2026, 7, 1)}
    )
    with pytest.raises(QueryUnavailable, match="完整出库数据未覆盖"):
        execute_analysis(growth_report, AnalysisPlan(steps=[q]))


def test_calendar_month_and_leap_year():
    q = step().model_copy(
        update={"start_date": date(2024, 2, 1), "end_date_exclusive": date(2024, 3, 1)}
    )
    assert growth_periods(q)["previous"] == (date(2024, 1, 1), date(2024, 2, 1))
    assert growth_periods(q)["year_over_year"] == (date(2023, 2, 1), date(2023, 3, 1))


def test_history_scope_and_overlap_rejected(growth_report):
    data = growth_report.operations.model_dump()
    data["shipping_history"].append(data["shipping_history"][0])
    with pytest.raises(ValidationError, match="分区不能重叠"):
        OperationsSnapshot.model_validate(data)


def test_exclusion_changes_report_and_contribution(growth_report):
    r = result(
        growth_report,
        growth_basis="previous",
        filters=[dict(field="buyer", code="A", operator="exclude")],
    )
    assert r.rows[0]["delta"] == "20.0000"
    assert r.rows[0]["current_amount"] == "60.0000"


def test_full_export_preserves_all_customer_deltas(growth_report):
    from app.analysis.growth import execute_growth

    query = step(
        dimension="buyer",
        growth_basis="previous",
        top_n=1,
        filters=[dict(field="product", code="P")],
    )
    full = execute_growth(growth_report, query, full=True)
    assert len(full.rows) == 3
    assert sum(Decimal(row["delta"]) for row in full.rows) == 60
    assert full.totals["groups"][0]["increase_other"] == "0.0000"


def test_quantity_groups_and_counterdirection(growth_report):
    from app.schemas.sales import SalesReport

    data = growth_report.model_dump()
    for doc in data["operations"]["shipping_history"][0]["documents"]:
        doc["lines"][0]["quantity"] /= 10
    data["operations"]["shipping_history"][0]["control_quantities"]["盒"] /= 10
    checked = SalesReport.model_validate(data)
    r = result(checked, metric="quantity", growth_basis="previous")
    assert r.rows[0]["direction"] == "下降"
    assert r.rows[0]["amount_delta"] == "60.0000"
    assert r.rows[0]["quantity_delta"] == "-84.0000"


def test_growth_evidence_is_filtered_paginated_and_period_bound(growth_report):
    from app.analysis.growth_api import GrowthEvidenceRequest, evidence

    body = GrowthEvidenceRequest(
        step=step(growth_basis="previous"),
        period="current",
        product_code="P",
        specification="合成规格",
        manufacturer="合成厂家",
        unit="盒",
        limit=1,
    )
    page = evidence(growth_report, body)
    assert page["total"] == 3
    assert len(page["items"]) == 1
    filtered = body.model_copy(update={"buyer_code": "B"})
    assert evidence(growth_report, filtered)["items"][0]["amount"] == "20"
    with pytest.raises(QueryUnavailable, match="比较期不属于"):
        evidence(growth_report, body.model_copy(update={"period": "year_over_year"}))


def test_growth_api_and_signed_manual_followup(growth_report):
    from fastapi.testclient import TestClient

    from app.core.settings import ApiSettings
    from app.main import create_app
    from app.semantic.provider import SemanticPlanner
    from app.semantic.schemas import SemanticRequest

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
    query = step(dimension="buyer", filters=[dict(field="product", code="P")])
    from app.schemas.analytics import ObjectFilter

    query = query.model_copy(
        update={
            "filters": [
                ObjectFilter(
                    field="product",
                    code="P",
                    variant=dict(specification="合成规格", manufacturer="合成厂家", unit="盒"),
                )
            ]
        }
    )
    response = client.post("/api/v1/analysis/run", json={"steps": [query.model_dump(mode="json")]})
    assert response.status_code == 200, response.text
    token = response.json()["conversation_token"]
    followup = client.post(
        "/api/v1/analysis/converse",
        json={
            "question": "换成去年同期",
            "conversation_token": token,
        },
    )
    assert followup.status_code == 200, followup.text
    turn = followup.json()
    assert turn["status"] == "result", turn
    assert turn["result"]["plan"]["steps"][0]["filters"][0]["code"] == "P"
    assert turn["result"]["plan"]["steps"][0]["growth_basis"] == "year_over_year"
    assert turn["result"]["plan"]["steps"][0]["filters"][0]["variant"]["unit"] == "盒"
    assert "code" not in str(provider.requests[0].previous.entity_bindings)
    assert provider.requests[0].previous.intents[0].filters[0].value != "P"
    assert "合成厂家" not in str(provider.requests[0])


def test_cloud_semantics_compile_growth(growth_report):
    import asyncio

    from app.analysis.planning import resolve_question
    from app.schemas.analytics import AnalysisQuestion
    from app.semantic.schemas import SemanticRequest

    class Planner:
        async def interpret(self, request):
            return SemanticRequest(
                intents=[
                    dict(
                        domain="shipping",
                        operation="growth",
                        target="product",
                        time="2026-08-01至2026-08-31",
                    )
                ]
            )

    planned = asyncio.run(
        resolve_question(
            AnalysisQuestion(question="比较八月品种增长"),
            growth_report,
            Planner(),
            date(2026, 10, 5),
        )
    )
    assert planned.plan is not None, planned.semantic.message
    assert planned.plan.steps[0].metric == "amount"
    assert planned.plan.steps[0].growth_basis == "both"


def test_feishu_growth_card_shows_quantity_without_currency(growth_report):
    from app.integrations.feishu_analytics_layouts import details, metrics

    query = step(metric="quantity", growth_basis="previous")
    r = result(growth_report, metric="quantity", growth_basis="previous")
    columns, rows = details(query, r, "CNY", 10)
    assert ("delta", "数量差额") in columns
    assert rows[0]["delta"] == "60.0000"
    assert metrics(r, "CNY") == []


@pytest.mark.parametrize(
    "phrase,start,end",
    [
        ("八月", "2026-08-01", "2026-09-01"),
        ("2026-08", "2026-08-01", "2026-09-01"),
        ("2026/08", "2026-08-01", "2026-09-01"),
        ("去年八月", "2025-08-01", "2025-09-01"),
        ("上个月", "2026-09-01", "2026-10-01"),
        ("2024年二月", "2024-02-01", "2024-03-01"),
    ],
)
def test_full_month_expressions(phrase, start, end):
    from app.semantic.dates import explicit_periods, resolve_period

    expected = (date.fromisoformat(start), date.fromisoformat(end))
    assert resolve_period(phrase, date(2026, 10, 5)) == expected
    assert explicit_periods("比较" + phrase + "的品种变化", date(2026, 10, 5)) == [expected]


def test_changed_variant_is_flagged_and_customer_drilldown_stays_scoped(growth_report):
    from app.schemas.sales import SalesReport

    data = growth_report.model_dump()
    data["operations"]["shipping_history"][0]["documents"][0]["lines"][0]["unit"] = "瓶"
    data["operations"]["shipping_history"][0]["control_quantities"] = {"瓶": "100", "盒": "60"}
    report = SalesReport.model_validate(data)
    rows = result(report, growth_basis="previous").rows
    changed = next(r for r in rows if r["unit"] == "瓶")
    assert changed["change_percent"] == "不可比"
    assert changed["change_rate"] is None
    r = result(
        report,
        growth_basis="previous",
        dimension="buyer",
        filters=[
            dict(
                field="product",
                code="P",
                variant=dict(specification="合成规格", manufacturer="合成厂家", unit="盒"),
            )
        ],
    )
    assert r.totals["groups"][0]["delta"] == "-40.0000"
    assert {row["unit"] for row in r.rows} == {"盒"}


def test_comparable_partition_rejects_missing_identity_and_wrong_status(growth_report):
    for key, value in [("source_tables", ["XSDDH", "XSDDB"]), ("included_statuses", ["订单完成"])]:
        data = growth_report.operations.shipping_history[0].model_dump()
        data[key] = value
        with pytest.raises(ValidationError, match="可比出库分区"):
            from app.schemas.operations import DocumentFacts

            DocumentFacts.model_validate(data)


def test_partial_source_day_is_never_complete(growth_report):
    from app.analysis.growth import validate_growth

    with pytest.raises(QueryUnavailable, match="完整出库数据未覆盖"):
        validate_growth(
            growth_report,
            step().model_copy(
                update={
                    "start_date": growth_report.metadata.source_as_of.date(),
                    "end_date_exclusive": growth_report.metadata.source_as_of.date().replace(
                        day=17
                    ),
                }
            ),
        )


def test_history_windows_exclude_primary_and_split_calendar_months(growth_report):
    from workers.extend_shipping_history import history_windows

    parts = history_windows(date(2026, 8, 1), date(2026, 9, 4), growth_report.operations.shipping)
    assert [(p.start, p.end) for p in parts] == [(date(2026, 8, 1), date(2026, 9, 1))]


def test_export_and_evidence_routes(growth_report):
    from fastapi.testclient import TestClient

    from app.core.settings import ApiSettings
    from app.main import create_app

    client = TestClient(create_app(ApiSettings(), report=growth_report))
    query = step(
        dimension="buyer",
        growth_basis="previous",
        top_n=1,
        filters=[dict(field="product", code="P")],
    ).model_dump(mode="json")
    exported = client.post("/api/v1/analysis/growth/export", json=query)
    assert exported.status_code == 200
    assert len(exported.json()["rows"]) == 3
    response = client.post(
        "/api/v1/analysis/growth/evidence",
        json=dict(
            step=query,
            period="previous",
            product_code="P",
            specification="合成规格",
            manufacturer="合成厂家",
            unit="盒",
            buyer_code="A",
            limit=1,
        ),
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["amount"] == "60"
