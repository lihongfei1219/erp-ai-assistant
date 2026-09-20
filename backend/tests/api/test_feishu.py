from fastapi.testclient import TestClient

from app.analysis.sales import analyze_sales
from app.core.business_rules import load_business_rules
from app.core.settings import ApiSettings
from app.main import create_app
from app.notifications import feishu

TOKEN = "feishu-test-token-0123456789abcdef"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def daily_client(extract, window, scope, source_as_of):
    report = analyze_sales(
        extract,
        window,
        scope,
        source_as_of=source_as_of,
        rules=load_business_rules(),
        synthetic=True,
    )
    return TestClient(create_app(ApiSettings(token=TOKEN), report=report))


def test_preview_uses_only_selected_day_and_effective_orders(extract, window, scope, source_as_of):
    client = daily_client(extract, window, scope, source_as_of)
    response = client.get("/api/v1/feishu/daily?day=2026-09-02&demo=true", headers=HEADERS)
    assert response.status_code == 200
    digest = response.json()["digest"]
    assert digest["day"] == "2026-09-02"
    assert digest["demo"] is True
    assert digest["summary"]["order_amount"] == "200.0000"
    assert digest["summary"]["order_count"] == 1
    assert digest["summary"]["buyer_count"] == 1
    assert digest["excluded_order_count"] == 1
    assert digest["amount_change_percent"] == "100.00"
    assert digest["buyers"][0]["code"] == "BUYER-A"
    assert digest["products"][0]["order_amount"] == "200.0000"


def test_preview_auth_scope_and_unavailable_dates(extract, window, scope, source_as_of):
    client = daily_client(extract, window, scope, source_as_of)
    assert client.get("/api/v1/feishu/daily?day=2026-09-02").status_code == 401
    assert (
        client.get(
            "/api/v1/feishu/daily?day=2026-09-02&buyer_code=another", headers=HEADERS
        ).status_code
        == 422
    )
    assert client.get("/api/v1/feishu/daily?day=2026-09-19", headers=HEADERS).status_code == 409
    assert client.post("/api/v1/feishu/send", json={"day": "2026-09-02"}).status_code == 401


def test_send_uses_configured_group_and_never_returns_secrets(
    monkeypatch,
    tmp_path,
    extract,
    window,
    scope,
    source_as_of,
):
    settings = feishu.FeishuSettings(
        webhook="https://open.feishu.cn/open-apis/bot/v2/hook/test-only",
        secret="secret-not-for-browser",
        state_dir=tmp_path,
    )
    monkeypatch.setattr(feishu.FeishuSettings, "from_env", classmethod(lambda cls: settings))
    sent = []
    monkeypatch.setattr(feishu, "send_webhook", lambda config, digest: sent.append(digest))
    client = daily_client(extract, window, scope, source_as_of)
    preview = client.get("/api/v1/feishu/daily?day=2026-09-02&demo=true", headers=HEADERS)
    assert preview.json()["configured"] is True
    assert not sent
    assert settings.webhook not in preview.text
    assert settings.secret not in preview.text
    body = {"day": "2026-09-02", "demo": True}
    assert (
        client.post(
            "/api/v1/feishu/send", json={**body, "webhook": "another"}, headers=HEADERS
        ).status_code
        == 422
    )
    first = client.post("/api/v1/feishu/send", json=body, headers=HEADERS)
    assert first.status_code == 200
    assert first.json()["status"] == "sent"
    second = client.post("/api/v1/feishu/send", json=body, headers=HEADERS)
    assert second.json()["status"] == "already_sent"
    assert len(sent) == 1


def test_unconfigured_robot_cannot_send(monkeypatch, extract, window, scope, source_as_of):
    monkeypatch.setattr(
        feishu.FeishuSettings, "from_env", classmethod(lambda cls: feishu.FeishuSettings())
    )
    client = daily_client(extract, window, scope, source_as_of)
    response = client.post(
        "/api/v1/feishu/send", headers=HEADERS, json={"day": "2026-09-02", "demo": True}
    )
    assert response.status_code == 503
