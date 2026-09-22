from fastapi.testclient import TestClient

from app.analysis.sales import analyze_sales
from app.core.business_rules import load_business_rules
from app.core.settings import ApiSettings
from app.main import create_app
from app.semantic.provider import SemanticPlanner
from app.semantic.schemas import SemanticRequest

HEADERS = {}


class Provider:
    def __init__(self):
        self.requests = []

    async def interpret(self, request):
        self.requests.append(request)
        if request.question == "九月一号":
            return SemanticRequest(mode="answer", intents=[dict(time="九月一号")])
        if "退货" in request.question:
            return SemanticRequest(
                intents=[dict(domain="returns", operation="existence", time="九月一号")]
            )
        return SemanticRequest(intents=[dict(domain="sales", operation="summary")])


def client_for(extract, window, scope, source_as_of, provider, report=None):
    report = report or analyze_sales(
        extract,
        window,
        scope,
        source_as_of=source_as_of,
        rules=load_business_rules(),
        synthetic=True,
    )
    return TestClient(
        create_app(
            ApiSettings(),
            report=report,
            analysis_planner=SemanticPlanner(provider=provider),
        )
    )


def test_web_clarification_token_completes_sales_and_can_be_used_after_recreation(
    extract, window, scope, source_as_of
):
    provider = Provider()
    report = analyze_sales(
        extract,
        window,
        scope,
        source_as_of=source_as_of,
        rules=load_business_rules(),
        synthetic=True,
    )
    client = client_for(extract, window, scope, source_as_of, provider, report)
    pending = client.post(
        "/api/v1/analysis/ask", headers=HEADERS, json={"question": "这几天卖了多少钱"}
    )
    assert pending.status_code == 422
    detail = pending.json()["detail"]
    assert isinstance(detail, dict) and detail["code"] == "clarify"
    token = detail["conversation_token"]
    client = client_for(extract, window, scope, source_as_of, provider, report)
    completed = client.post(
        "/api/v1/analysis/ask",
        headers=HEADERS,
        json={"question": "九月一号", "conversation_token": token},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["results"][0]["rows"][0]["amount"] == "100.0000"
    assert provider.requests[-1].previous.pending == ["time"]


def test_interpret_is_independent_of_execution_and_reports_capabilities(
    extract, window, scope, source_as_of
):
    client = client_for(extract, window, scope, source_as_of, Provider())
    result = client.post(
        "/api/v1/analysis/interpret", headers=HEADERS, json={"question": "九月一号有退货吗"}
    )
    assert result.status_code == 200
    body = result.json()
    assert body["status"] == "unsupported"
    assert body["semantic"]["intents"][0]["domain"] == "returns"
    assert "未接入" in body["message"] and "results" not in body
    assert client.post("/api/v1/analysis/interpret", json={"question": "销售"}).status_code == 200


def test_client_cannot_modify_signed_conversation(extract, window, scope, source_as_of):
    client = client_for(extract, window, scope, source_as_of, Provider())
    pending = client.post("/api/v1/analysis/ask", headers=HEADERS, json={"question": "卖了多少钱"})
    token = pending.json()["detail"]["conversation_token"]
    result = client.post(
        "/api/v1/analysis/ask",
        headers=HEADERS,
        json={"question": "九月一号", "conversation_token": token[:-5] + "AAAAA"},
    )
    assert result.status_code == 422
    assert "上下文" in str(result.json())


def test_model_requested_scope_cannot_expand_authorized_snapshot(
    extract, window, scope, source_as_of
):
    from app.schemas.sales import DataScope

    class AllBuyers:
        async def interpret(self, request):
            return SemanticRequest(
                intents=[
                    dict(
                        domain="sales",
                        operation="summary",
                        time="9月1号",
                        scope="all_buyers",
                    )
                ]
            )

    report = analyze_sales(
        extract,
        window,
        scope,
        source_as_of=source_as_of,
        rules=load_business_rules(),
        synthetic=True,
    )
    restricted = report.model_copy(
        update={
            "metadata": report.metadata.model_copy(
                update={
                    "scope": DataScope(buyer_codes=["BUYER-A"]),
                }
            ),
        }
    )
    client = client_for(extract, window, scope, source_as_of, AllBuyers(), restricted)
    result = client.post(
        "/api/v1/analysis/ask",
        headers=HEADERS,
        json={"question": "9月1号，把其他公司也算上"},
    )
    assert result.status_code == 422
    assert result.json()["detail"]["code"] == "unsupported"
    assert "授权" in result.json()["detail"]["message"]


def test_scope_note_comes_from_model_semantics_without_matching_customer_words(
    extract, window, scope, source_as_of
):
    class GenericProducts:
        async def interpret(self, request):
            return SemanticRequest(
                intents=[
                    dict(
                        domain="sales",
                        operation="ranking",
                        target="product",
                        time="9月1号",
                        generic_product_scope=True,
                    )
                ]
            )

    client = client_for(extract, window, scope, source_as_of, GenericProducts())
    result = client.post(
        "/api/v1/analysis/ask",
        headers=HEADERS,
        json={"question": "9月1号，看看那些药卖得怎么样"},
    )
    assert result.status_code == 200
    assert any("未按药品类别筛选" in s for s in result.json()["interpretation"])
