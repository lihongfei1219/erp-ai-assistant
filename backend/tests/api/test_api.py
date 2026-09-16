import pytest
from fastapi.testclient import TestClient

from app.core.settings import ApiSettings
from app.main import create_app

TOKEN = "synthetic-test-token-0123456789abcdef"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(report):
    return TestClient(create_app(ApiSettings(token=TOKEN), report=report))


@pytest.mark.parametrize(
    "path",
    [
        "/dashboard/summary",
        "/sales/trends",
        "/sales/breakdown",
        "/sales/states",
        "/data/status",
        "/orders",
        "/orders/1",
    ],
)
def test_all_business_endpoints_require_auth(client, path):
    assert client.get("/api/v1" + path).status_code == 401
    assert (
        client.get("/api/v1" + path, headers={"Authorization": "Bearer wrong"}).status_code == 401
    )


def test_summary_includes_scope_time_and_exact_decimal(client):
    response = client.get("/api/v1/dashboard/summary", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["summary"]["order_amount"] == "390.0000"
    assert response.json()["metadata"]["metric_version"] == "qy.sales_orders.v1"
    assert response.json()["metadata"]["source_kind"] == "synthetic"
    assert "evidence" not in response.json()
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "path",
    [
        "/sales/breakdown?dimension=supplier",
        "/sales/breakdown?limit=1000",
        "/orders?page=0",
        "/orders?page_size=1000",
    ],
)
def test_invalid_query_parameters(client, path):
    assert client.get("/api/v1" + path, headers=HEADERS).status_code == 422


def test_evidence_pagination_and_detail(client):
    response = client.get("/api/v1/orders?page=2&page_size=2", headers=HEADERS)
    assert response.json()["total"] == 3
    assert [item["order_id"] for item in response.json()["items"]] == [3]
    assert client.get("/api/v1/orders/999", headers=HEADERS).status_code == 404
    assert len(client.get("/api/v1/orders/1", headers=HEADERS).json()["item"]["lines"]) == 2


def test_absent_token_fails_closed(report):
    client = TestClient(create_app(ApiSettings(token=""), report=report))
    assert client.get("/api/v1/dashboard/summary", headers=HEADERS).status_code == 503


def test_missing_report_returns_unavailable_not_zero():
    client = TestClient(create_app(ApiSettings(token=TOKEN)))
    assert client.get("/api/v1/dashboard/summary", headers=HEADERS).status_code == 503
    assert client.get("/healthz").status_code == 200


def test_corrupt_snapshot_returns_unavailable(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not json", encoding="utf-8")
    client = TestClient(create_app(ApiSettings(token=TOKEN, report_path=path)))
    assert client.get("/api/v1/data/status", headers=HEADERS).status_code == 503


def test_cannot_select_another_snapshot_or_inject_scope(client):
    response = client.get(
        "/api/v1/dashboard/summary?buyer_code=OTHER&report_path=other.json", headers=HEADERS
    )
    assert response.status_code == 422
    assert client.get("/api/v1/reports/other", headers=HEADERS).status_code == 404


def test_openapi_is_available(client):
    assert client.get("/openapi.json").status_code == 200


def test_restricted_snapshot_cannot_expose_another_buyer(extract, window, source_as_of):
    from decimal import Decimal

    from app.analysis.sales import analyze_sales
    from app.schemas.sales import DataScope

    extract.orders = extract.orders.loc[extract.orders.buyer_code == "BUYER-A"]
    extract.lines = extract.lines.loc[extract.lines.order_id.isin(extract.orders.order_id)]
    extract.sql_order_count, extract.sql_order_amount = 2, Decimal("300.0000")
    scoped = analyze_sales(
        extract,
        window,
        DataScope(buyer_codes=("BUYER-A",)),
        source_as_of=source_as_of,
        synthetic=True,
    )
    client = TestClient(create_app(ApiSettings(token=TOKEN), report=scoped))
    assert client.get("/api/v1/orders/3", headers=HEADERS).status_code == 404
    response = client.get("/api/v1/dashboard/summary", headers=HEADERS).json()
    assert response["summary"]["order_amount"] == "300.0000"
    assert response["metadata"]["scope"] == {"all_buyers": False, "buyer_codes": ["BUYER-A"]}
