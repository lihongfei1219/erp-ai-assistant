import pytest
from fastapi.testclient import TestClient

from app.analysis.sales import analyze_sales
from app.core.business_rules import load_business_rules
from app.core.settings import ApiSettings
from app.main import create_app

HEADERS = {}


@pytest.fixture
def operating_client(extract, window, scope, source_as_of):
    report = analyze_sales(
        extract,
        window,
        scope,
        source_as_of=source_as_of,
        rules=load_business_rules(),
        synthetic=True,
    )
    return TestClient(create_app(ApiSettings(), report=report))


def test_operating_endpoint_shows_rule_and_excluded_amount(operating_client):
    response = operating_client.get("/api/v1/dashboard/operating", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["operating"]["policy"]["currency"] == "CNY"
    assert data["operating"]["summary"]["order_amount"] == "300.0000"
    assert data["raw_summary"]["order_amount"] == "390.0000"
    assert data["operating"]["excluded_order_amount"] == "90.0000"
    assert "evidence" not in data
    assert len(data["operating"]["policy_fingerprint"]) == 64


def test_operating_order_pagination_uses_same_states(operating_client):
    response = operating_client.get(
        "/api/v1/orders?view=operating&page_size=1&page=2", headers=HEADERS
    )
    assert response.json()["total"] == 2
    assert response.json()["items"][0]["order_id"] == 2
    raw = operating_client.get("/api/v1/orders?view=all", headers=HEADERS).json()
    assert raw["total"] == 3
    assert operating_client.get("/api/v1/orders?view=other", headers=HEADERS).status_code == 422


@pytest.mark.parametrize("endpoint", ["/dashboard/operating", "/business/assumptions"])
def test_local_endpoints_allow_direct_access(operating_client, endpoint):
    assert operating_client.get("/api/v1" + endpoint).status_code == 200


def test_single_assumptions_document_is_downloadable(operating_client):
    response = operating_client.get("/api/v1/business/assumptions", headers=HEADERS)
    assert response.status_code == 200
    assert "B18" in response.text and "B01" in response.text
    assert "business-assumptions.md" in response.headers["content-disposition"]


def test_legacy_report_not_silently_treated_as_effective(report):
    client = TestClient(create_app(ApiSettings(), report=report))
    assert client.get("/api/v1/dashboard/operating", headers=HEADERS).status_code == 503
    assert client.get("/api/v1/orders?view=operating", headers=HEADERS).status_code == 503
    assert client.get("/api/v1/dashboard/summary", headers=HEADERS).status_code == 200
