from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.analysis.sales import analyze_sales
from app.core.business_rules import load_business_rules
from app.core.settings import ApiSettings
from app.main import create_app

TOKEN = "analytics-test-token-0123456789abcdef"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def analytics_report(extract, window, scope, source_as_of):
    return analyze_sales(
        extract,
        window,
        scope,
        source_as_of=source_as_of,
        rules=load_business_rules(),
        synthetic=True,
    )


@pytest.fixture
def client(analytics_report):
    return TestClient(create_app(ApiSettings(token=TOKEN), report=analytics_report))


def step(kind="summary", **kwargs):
    return dict(kind=kind, start_date="2026-09-01", end_date_exclusive="2026-09-04", **kwargs)


def run(client, *steps):
    return client.post("/api/v1/analysis/run", headers=HEADERS, json={"steps": list(steps)})


def test_summary_and_rankings_use_effective_evidence(client):
    response = run(client, step(), step("product_ranking"), step("buyer_ranking"))
    assert response.status_code == 200
    data = response.json()
    assert data["results"][0]["rows"][0]["amount"] == "300.0000"
    assert data["results"][0]["rows"][0]["order_count"] == 2
    assert data["results"][0]["rows"][0]["buyer_count"] == 1
    products = data["results"][1]
    assert [row["amount"] for row in products["rows"]] == ["260.0000", "40.0000"]
    assert [row["order_count"] for row in products["rows"]] == [2, 1]
    assert products["rows"][0]["evidence_ids"] == [1, 2]
    assert data["results"][2]["rows"][0]["code"] == "BUYER-A"
    assert data["provenance"]["policy_fingerprint"]
    assert data["provenance"]["source_kind"] == "synthetic"
    assert products["chart"]["data"][0]["type"] == "bar"


def test_comparison_reconciles_all_dimension_changes(client):
    query = dict(
        kind="comparison",
        start_date="2026-09-02",
        end_date_exclusive="2026-09-03",
        comparison_start_date="2026-09-01",
        comparison_end_date_exclusive="2026-09-02",
    )
    response = run(client, query)
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert Decimal(result["totals"]["delta"]) == 100
    assert Decimal(result["totals"]["change_rate"]) == 1
    assert {r["code"]: Decimal(r["delta"]) for r in result["rows"]} == {
        "SKU-A": Decimal(140),
        "SKU-B": Decimal(-40),
    }
    assert "因果" in "".join(result["notes"])


def test_empty_period_has_zero_amount_but_null_average(client):
    response = run(
        client, dict(kind="summary", start_date="2026-09-03", end_date_exclusive="2026-09-04")
    )
    row = response.json()["results"][0]["rows"][0]
    assert Decimal(row["amount"]) == 0
    assert row["average_order_amount"] is None


def test_trend_fills_only_covered_days(client):
    response = run(client, step("trend"))
    rows = response.json()["results"][0]["rows"]
    assert [Decimal(row["amount"]) for row in rows] == [100, 200, 0]


@pytest.mark.parametrize(
    "change",
    [
        {"start_date": "2026-08-31"},
        {"end_date_exclusive": "2026-09-17"},
        {"kind": "profit"},
        {"sql": "SELECT 1"},
        {"top_n": 1000},
        {"kind": "comparison"},
        {"metric": "quantity"},
    ],
)
def test_invalid_or_unavailable_requests_are_not_silently_changed(client, change):
    assert run(client, step() | change).status_code == 422


def test_endpoints_require_auth_and_reject_unknown_filters(client):
    assert client.get("/api/v1/analysis/catalog").status_code == 401
    assert client.post("/api/v1/analysis/run", json={"steps": [step()]}).status_code == 401
    assert client.post("/api/v1/analysis/ask", json={"question": "销售额"}).status_code == 401
    assert client.get("/api/v1/analysis/catalog?buyer=other", headers=HEADERS).status_code == 422


def test_catalog_describes_metrics_and_covered_dates(client):
    response = client.get("/api/v1/analysis/catalog", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["available_start"] == "2026-09-01"
    assert response.json()["available_end_exclusive"] == "2026-09-04"
    assert response.json()["metrics"]


def test_restricted_snapshot_retains_scope(extract, window, source_as_of):
    from app.schemas.sales import DataScope

    extract.orders = extract.orders[extract.orders.buyer_code == "BUYER-A"].copy()
    extract.lines = extract.lines[extract.lines.order_id.isin([1, 2])].copy()
    extract.sql_order_count, extract.sql_order_amount = 2, Decimal(300)
    report = analyze_sales(
        extract,
        window,
        DataScope(buyer_codes=("BUYER-A",)),
        source_as_of=source_as_of,
        rules=load_business_rules(),
        synthetic=True,
    )
    client = TestClient(create_app(ApiSettings(token=TOKEN), report=report))
    response = run(client, step("buyer_ranking"))
    assert response.status_code == 200
    assert response.json()["provenance"]["scope"]["buyer_codes"] == ["BUYER-A"]


def test_zero_baseline_and_remaining_contributions(client):
    response = run(
        client,
        dict(
            kind="comparison",
            start_date="2026-09-02",
            end_date_exclusive="2026-09-03",
            comparison_start_date="2026-09-03",
            comparison_end_date_exclusive="2026-09-04",
            top_n=1,
        ),
    )
    assert response.status_code == 200
    totals = response.json()["results"][0]["totals"]
    assert totals["change_rate"] is None
    response = run(
        client,
        dict(
            kind="comparison",
            start_date="2026-09-02",
            end_date_exclusive="2026-09-03",
            comparison_start_date="2026-09-01",
            comparison_end_date_exclusive="2026-09-02",
            top_n=1,
        ),
    )
    result = response.json()["results"][0]
    assert Decimal(result["totals"]["other_delta"]) == -40
    assert Decimal(result["rows"][0]["delta"]) + Decimal(result["totals"]["other_delta"]) == 100


def test_anomaly_rule_and_order_metric_chart(client):
    result = run(client, step("anomalies")).json()["results"][0]
    assert [row["day"] for row in result["rows"]] == ["2026-09-02", "2026-09-03"]
    assert [Decimal(row["change_rate"]) for row in result["rows"]] == [1, -1]
    result = run(client, step("trend", metric="orders")).json()["results"][0]
    assert result["chart"]["data"][0]["y"] == [1.0, 1.0, 0.0]


def test_partial_day_and_corrupt_evidence_are_rejected(analytics_report):
    from datetime import datetime

    partial = analytics_report.model_copy(
        update={
            "metadata": analytics_report.metadata.model_copy(
                update={"source_as_of": datetime.fromisoformat("2026-09-03T12:00:00+08:00")}
            )
        }
    )
    client = TestClient(create_app(ApiSettings(token=TOKEN), report=partial))
    assert run(client, step()).status_code == 422
    corrupt = analytics_report.model_copy(update={"evidence": analytics_report.evidence[1:]})
    client = TestClient(create_app(ApiSettings(token=TOKEN), report=corrupt))
    assert run(client, step()).status_code == 422


def test_ask_runs_typed_plan_without_business_data_in_model_context(analytics_report):
    from app.schemas.analytics import PlanDecision

    class Planner:
        enabled = True

        async def plan(self, body, context):
            assert set(context) == {
                "today",
                "available_start",
                "available_end_exclusive",
                "timezone",
            }
            assert "BUYER-A" not in str(context)
            return PlanDecision(action="run", plan={"steps": [step()]})

    client = TestClient(
        create_app(ApiSettings(token=TOKEN), report=analytics_report, analysis_planner=Planner())
    )
    response = client.post(
        "/api/v1/analysis/ask", headers=HEADERS, json={"question": "2026-09-01至2026-09-03销售概览"}
    )
    assert response.status_code == 200
    assert response.json()["results"][0]["rows"][0]["amount"] == "300.0000"


def test_ask_clarifies_instead_of_executing_partial_request(analytics_report):
    from app.schemas.analytics import PlanDecision

    class Planner:
        async def plan(self, body, context):
            return PlanDecision(
                action="clarify", explanation="缺少成本数据", unsupported_conditions=["利润"]
            )

    client = TestClient(
        create_app(ApiSettings(token=TOKEN), report=analytics_report, analysis_planner=Planner())
    )
    response = client.post("/api/v1/analysis/ask", headers=HEADERS, json={"question": "利润"})
    assert response.status_code == 422
    assert "results" not in response.json()


@pytest.mark.parametrize("product_word", ["药品", "药 品"])
def test_colloquial_drug_question_runs_and_explains_actual_scope(
    extract, scope, source_as_of, product_word
):
    from app.schemas.analytics import PlanDecision
    from app.schemas.sales import AnalysisWindow

    analytics_report = analyze_sales(
        extract,
        AnalysisWindow(start="2026-09-01", end="2026-09-06"),
        scope,
        source_as_of=source_as_of,
        rules=load_business_rules(),
        synthetic=True,
    )

    class Planner:
        async def plan(self, body, context):
            return PlanDecision(
                action="run",
                plan={
                    "steps": [
                        {
                            "kind": "product_ranking",
                            "start_date": "2026-09-01",
                            "end_date_exclusive": "2026-09-06",
                        }
                    ]
                },
                explanation="Do not display unverified model claims",
            )

    client = TestClient(
        create_app(
            ApiSettings(token=TOKEN),
            report=analytics_report,
            analysis_planner=Planner(),
        )
    )
    response = client.post(
        "/api/v1/analysis/ask",
        headers=HEADERS,
        json={
            "question": f"分析2026年9月1日至5号的销售数据，哪些{product_word}卖的好",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["plan"]["steps"][0]["end_date_exclusive"] == "2026-09-06"
    interpretation = " ".join(data["interpretation"])
    assert "2026-09-01 至 2026-09-05（含首尾两天）" in interpretation
    assert "销售金额" in interpretation and "前 10 名" in interpretation
    assert "当前商品范围" in interpretation and "未按药品类别筛选" in interpretation
    assert "unverified" not in interpretation
    assert data["results"][0]["rows"][0]["amount"] == "260.0000"


@pytest.mark.parametrize(
    "question",
    [
        "2026-09-02至2026-09-03销售概览",
        "2026-09-01至2026-09-03华东地区销售概览",
        "2026-09-01至2026-09-03利润",
        "2026-09-01至2026-09-03每日销售趋势",
        "昨天销售概览",
        "销售概览",
    ],
)
def test_model_cannot_ignore_question_conditions(analytics_report, question):
    from app.schemas.analytics import PlanDecision

    class WrongPlanner:
        async def plan(self, body, context):
            return PlanDecision(action="run", plan={"steps": [step()]})

    client = TestClient(
        create_app(
            ApiSettings(token=TOKEN), report=analytics_report, analysis_planner=WrongPlanner()
        )
    )
    response = client.post("/api/v1/analysis/ask", headers=HEADERS, json={"question": question})
    assert response.status_code == 422
    assert "results" not in response.json()


def test_chart_distinguishes_same_named_products(analytics_report):
    orders = [
        order.model_copy(
            update={
                "lines": [
                    line.model_copy(update={"product_name": "同名商品"}) for line in order.lines
                ]
            }
        )
        for order in analytics_report.evidence
    ]
    report = analytics_report.model_copy(update={"evidence": orders})
    client = TestClient(create_app(ApiSettings(token=TOKEN), report=report))
    result = run(client, step("product_ranking")).json()["results"][0]
    assert len(set(result["chart"]["data"][0]["x"])) == 2
    assert result["chart"]["data"][0]["y"] == [260.0, 40.0]
