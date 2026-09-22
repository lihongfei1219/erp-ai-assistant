from fastapi.testclient import TestClient

from app.analysis.sales import analyze_sales
from app.core.business_rules import load_business_rules
from app.core.settings import ApiSettings
from app.main import create_app
from app.semantic.provider import SemanticPlanner
from app.semantic.schemas import SemanticRequest

HEADERS = {}
URL = "/api/v1/analysis/converse"


class Provider:
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.requests = []

    async def interpret(self, request):
        self.requests.append(request)
        return SemanticRequest.model_validate(self.outputs.pop(0))


def setup(extract, window, scope, source_as_of, provider):
    report = analyze_sales(
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


def post(client, **body):
    response = client.post(URL, headers=HEADERS, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_sales_followup_can_switch_both_directions(extract, window, scope, source_as_of):
    provider = Provider(
        {
            "intents": [
                {
                    "domain": "sales",
                    "operation": "ranking",
                    "target": "product",
                    "metric": "orders",
                    "time": "9月1号至9月3号",
                    "order": "descending",
                    "limit": 1,
                }
            ]
        },
        {"mode": "followup", "intents": [{"order": "ascending"}]},
        {"mode": "followup", "intents": [{"order": "descending"}]},
    )
    client = setup(extract, window, scope, source_as_of, provider)
    result = post(client, question="9月1号至9月3号按订单数看第一名商品")
    for question, direction, code in [
        ("那卖得少的呢", "ascending", "SKU-B"),
        ("再换成卖得多的", "descending", "SKU-A"),
    ]:
        result = post(client, question=question, conversation_token=result["conversation_token"])
        assert result["status"] == "result"
        step = result["result"]["plan"]["steps"][0]
        assert (step["order"], step["metric"], step["top_n"]) == (direction, "orders", 1)
        assert step["start_date"] == "2026-09-01"
        assert step["end_date_exclusive"] == "2026-09-04"
        assert result["result"]["results"][0]["rows"][0]["code"] == code
        interpretation = "".join(result["result"]["interpretation"])
        label = "从低到高" if direction == "ascending" else "从高到低"
        assert label in interpretation


def test_missing_date_is_guidance_then_signed_date_executes_without_model(
    extract,
    window,
    scope,
    source_as_of,
):
    provider = Provider({"intents": [{"domain": "sales", "operation": "ranking"}]})
    client = setup(extract, window, scope, source_as_of, provider)
    first = post(client, question="看看什么货卖得好")
    assert first["status"] == "needs_input"
    assert first["clarification"]["field"] == "time"
    assert first["draft"]["intents"][0]["id"]
    choice = next(c for c in first["choices"] if c["action"]["kind"] == "date_range")
    second = post(
        client,
        choice_id=choice["id"],
        conversation_token=first["conversation_token"],
        date_range={"start": "2026-09-01", "end_exclusive": "2026-09-02"},
    )
    assert second["status"] == "result"
    assert second["result"]["plan"]["steps"][0]["kind"] == "product_ranking"
    assert second["applied_defaults"]
    assert len(provider.requests) == 1
    assert provider.requests[0].capabilities["available_start"] == "2026-09-01"
    assert "BUYER-A" not in provider.requests[0].model_dump_json()


def test_explicit_unsupported_metric_requires_customer_choice(
    extract,
    window,
    scope,
    source_as_of,
):
    provider = Provider(
        {
            "intents": [
                {"domain": "sales", "operation": "ranking", "metric": "quantity", "time": "9月1号"}
            ]
        }
    )
    client = setup(extract, window, scope, source_as_of, provider)
    first = post(client, question="9月1号卖出了多少盒，排个名")
    assert first["status"] == "capability_gap" and first["result"] is None
    assert first["draft"]["intents"][0]["fields"]["metric"] == "quantity"
    choice = next(c for c in first["choices"] if "金额" in c["label"])
    second = post(client, choice_id=choice["id"], conversation_token=first["conversation_token"])
    assert second["status"] == "result"
    assert second["result"]["plan"]["steps"][0]["metric"] == "amount"
    assert len(provider.requests) == 1


def test_uncovered_today_is_not_silently_moved_to_snapshot(
    extract,
    window,
    scope,
    source_as_of,
):
    provider = Provider({"intents": [{"domain": "sales", "operation": "summary", "time": "今天"}]})
    client = setup(extract, window, scope, source_as_of, provider)
    first = post(client, question="今天卖了多少")
    assert first["status"] == "data_gap" and first["result"] is None
    assert first["available_dates"] == {"start": "2026-09-01", "end_exclusive": "2026-09-04"}
    assert first["draft"]["intents"][0]["fields"]["time"] != "2026-09-01/2026-09-04"


def test_choice_ids_are_bound_to_signed_context(extract, window, scope, source_as_of):
    provider = Provider({"intents": [{"domain": "sales", "operation": "summary"}]})
    client = setup(extract, window, scope, source_as_of, provider)
    first = post(client, question="看看销售")
    bad = client.post(
        URL,
        headers=HEADERS,
        json={
            "choice_id": "forged",
            "conversation_token": first["conversation_token"],
        },
    )
    assert bad.status_code == 409
    stale = client.post(
        URL,
        headers=HEADERS,
        json={
            "question": "继续",
            "conversation_token": first["conversation_token"][:-4] + "abcd",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "context_expired"
    assert len(provider.requests) == 1


def test_free_text_answer_and_repeated_issue_keep_known_conditions(
    extract,
    window,
    scope,
    source_as_of,
):
    provider = Provider(
        {"intents": [{"domain": "sales", "operation": "ranking", "metric": "orders"}]},
        {"mode": "answer", "intents": [{}]},
        {"mode": "answer", "intents": [{"time": "9月1号"}]},
    )
    client = setup(extract, window, scope, source_as_of, provider)
    first = post(client, question="按订单看看商品排名")
    again = post(client, question="我不清楚", conversation_token=first["conversation_token"])
    assert again["status"] == "needs_input" and again["repeated_clarification"]
    assert again["draft"]["intents"][0]["fields"]["metric"] == "orders"
    assert provider.requests[-1].previous.dialogue_choices == []
    done = post(client, question="9月1号", conversation_token=again["conversation_token"])
    assert done["status"] == "result"
    assert done["result"]["plan"]["steps"][0]["metric"] == "orders"


def test_model_failure_is_service_error_not_customer_error(
    extract,
    window,
    scope,
    source_as_of,
):
    class Failure:
        async def interpret(self, request):
            raise TimeoutError()

    client = setup(extract, window, scope, source_as_of, Failure())
    assert client.post(URL, headers=HEADERS, json={"question": "看看销售"}).status_code == 503


def test_filter_removal_only_removes_selected_condition(extract, window, scope, source_as_of):
    provider = Provider(
        {
            "intents": [
                {
                    "domain": "sales",
                    "operation": "summary",
                    "time": "9月1号",
                    "filters": [
                        {"field": "product", "value": "合成药品"},
                        {"field": "region", "value": "合成地区"},
                    ],
                }
            ]
        }
    )
    client = setup(extract, window, scope, source_as_of, provider)
    first = post(client, question="9月1号合成地区的合成药品卖得如何")
    assert len(first["draft"]["intents"][0]["constraints"]) == 2
    choice = next(c for c in first["choices"] if "合成药品" in c["label"])
    second = post(client, choice_id=choice["id"], conversation_token=first["conversation_token"])
    assert second["status"] == "capability_gap" and second["result"] is None
    remaining = second["draft"]["intents"][0]["constraints"]
    assert len(remaining) == 1 and "合成地区" in remaining[0]["label"]
    assert len(provider.requests) == 1


def test_independent_combination_needs_explicit_choice_to_drop_unsupported_goal(
    extract,
    window,
    scope,
    source_as_of,
):
    provider = Provider(
        {
            "intents": [
                {"domain": "sales", "operation": "summary", "time": "9月1号"},
                {"domain": "returns", "operation": "summary", "time": "9月1号"},
            ]
        }
    )
    client = setup(extract, window, scope, source_as_of, provider)
    first = post(client, question="9月1号销售额和退货情况分别看下")
    assert first["result"] is None and len(first["draft"]["intents"]) == 2
    choice = next(c for c in first["choices"] if "只保留销售" in c["label"])
    second = post(client, choice_id=choice["id"], conversation_token=first["conversation_token"])
    assert second["status"] == "result" and len(second["draft"]["intents"]) == 1


def test_dependent_combination_cannot_offer_partial_execution(
    extract,
    window,
    scope,
    source_as_of,
):
    provider = Provider(
        {
            "intents": [
                {"domain": "sales", "operation": "ranking", "time": "9月1号"},
                {"domain": "inventory", "operation": "ranking"},
            ],
            "issues": [
                {
                    "field": "conditions",
                    "kind": "dependency",
                    "question": "需要先用库存筛选销售对象",
                }
            ],
        }
    )
    client = setup(extract, window, scope, source_as_of, provider)
    first = post(client, question="9月1号库存很多的商品销售如何")
    assert first["result"] is None
    assert not any("只保留销售" in c["label"] for c in first["choices"])
    assert "需要先用库存筛选销售对象" in first["understood_summary"]


def test_broad_request_asks_goal_before_date(extract, window, scope, source_as_of):
    provider = Provider({"intents": [{"domain": "sales"}]})
    client = setup(extract, window, scope, source_as_of, provider)
    first = post(client, question="我想了解销售")
    assert first["clarification"]["field"] == "operation"
    assert len(first["choices"]) in {2, 3}


def test_model_suggestion_values_are_validated_and_issue_resolves_by_id(
    extract,
    window,
    scope,
    source_as_of,
):
    provider = Provider(
        {
            "intents": [{"id": "one", "domain": "sales", "operation": "ranking", "time": "9月1号"}],
            "issues": [
                {
                    "id": "which",
                    "intent_id": "one",
                    "field": "target",
                    "question": "你想比较商品表现还是客户采购表现？",
                    "choices": [
                        {"label": "商品表现", "value": "product"},
                        {"label": "危险无效动作", "value": "run_sql"},
                    ],
                }
            ],
        }
    )
    client = setup(extract, window, scope, source_as_of, provider)
    first = post(client, question="9月1号谁表现比较好")
    assert first["status"] == "needs_input"
    assert "商品表现" in first["clarification"]["question"]
    assert len(first["choices"]) == 1
    done = post(
        client, choice_id=first["choices"][0]["id"], conversation_token=first["conversation_token"]
    )
    assert done["status"] == "result"
    assert done["draft"]["intents"][0]["fields"]["target"] == "product"


def test_prior_choice_cannot_be_applied_to_new_turn(extract, window, scope, source_as_of):
    provider = Provider(
        {"intents": [{"domain": "sales", "operation": "summary"}]},
        {"mode": "answer", "intents": [{}]},
    )
    client = setup(extract, window, scope, source_as_of, provider)
    first = post(client, question="看销售概览")
    second = post(client, question="我还没想好", conversation_token=first["conversation_token"])
    response = client.post(
        URL,
        headers=HEADERS,
        json={
            "choice_id": first["choices"][0]["id"],
            "conversation_token": second["conversation_token"],
            "date_range": {"start": "2026-09-01", "end_exclusive": "2026-09-02"},
        },
    )
    assert response.status_code == 409 and len(provider.requests) == 2


def test_change_comparison_to_trend_explicitly_removes_comparison_period(
    extract,
    window,
    scope,
    source_as_of,
):
    provider = Provider(
        {
            "intents": [
                {
                    "domain": "sales",
                    "operation": "comparison",
                    "metric": "orders",
                    "target": "buyer",
                    "time": "9月2号",
                    "comparison_time": "9月1号",
                }
            ]
        }
    )
    client = setup(extract, window, scope, source_as_of, provider)
    first = post(client, question="比较9月2号与9月1号订单数，按客户拆解")
    choice = next(c for c in first["choices"] if "不再比较" in c["label"])
    second = post(client, choice_id=choice["id"], conversation_token=first["conversation_token"])
    assert second["status"] == "result"
    step = second["result"]["plan"]["steps"][0]
    assert step["kind"] == "trend" and step["metric"] == "orders"
    assert step["comparison_start_date"] is None


def test_date_suggestion_addresses_its_issue_target_and_filters_unavailable_dates(
    extract,
    window,
    scope,
    source_as_of,
):
    provider = Provider(
        {
            "intents": [
                {"id": "a", "domain": "sales", "operation": "summary"},
                {"id": "b", "domain": "sales", "operation": "trend", "time": "9月1号"},
            ],
            "issues": [
                {
                    "intent_id": "b",
                    "field": "time",
                    "question": "第二项趋势想看哪个日期？",
                    "choices": [
                        {"label": "可用日", "value": "9月2号"},
                        {"label": "未来", "value": "今天"},
                    ],
                }
            ],
        }
    )
    client = setup(extract, window, scope, source_as_of, provider)
    first = post(client, question="整体概览，另看9月1号附近几天的趋势")
    second_id = first["draft"]["intents"][1]["id"]
    assert all(c["action"]["intent_id"] == second_id for c in first["choices"])
    assert len(first["choices"]) == 2
    choice = next(c for c in first["choices"] if c["action"]["kind"] == "patch")
    second = post(client, choice_id=choice["id"], conversation_token=first["conversation_token"])
    assert second["status"] == "needs_input"
    assert second["clarification"]["intent_id"] != second_id


def test_explicit_top_count_guides_to_ranking_and_keeps_the_count(
    extract,
    window,
    scope,
    source_as_of,
):
    provider = Provider(
        {"intents": [{"domain": "sales", "operation": "summary", "time": "9月1号"}]},
        {"mode": "followup", "intents": [{"limit": 3}]},
    )
    client = setup(extract, window, scope, source_as_of, provider)
    first = post(client, question="9月1号销售概览")
    pending = post(client, question="只看前三个", conversation_token=first["conversation_token"])
    assert pending["status"] == "needs_input"
    assert pending["draft"]["intents"][0]["fields"]["limit"] == 3
    assert all("前 3 名" in choice["label"] for choice in pending["choices"])
    done = post(
        client,
        choice_id=pending["choices"][0]["id"],
        conversation_token=pending["conversation_token"],
    )
    assert done["status"] == "result"
    assert done["result"]["plan"]["steps"][0]["top_n"] == 3


def test_unlocated_change_stays_visible_in_recoverable_summary(
    extract,
    window,
    scope,
    source_as_of,
):
    provider = Provider(
        {
            "intents": [
                {"domain": "sales", "operation": "ranking", "target": "product", "time": "9月1号"},
                {"domain": "sales", "operation": "ranking", "target": "buyer", "time": "9月1号"},
            ]
        },
        {"mode": "followup", "intents": [{"metric": "orders"}]},
    )
    client = setup(extract, window, scope, source_as_of, provider)
    first = post(client, question="9月1号商品和客户各自的金额排行")
    pending = post(client, question="改按订单数", conversation_token=first["conversation_token"])
    assert pending["status"] == "needs_input"
    assert "等待确认适用目标的修改" in pending["understood_summary"]
    assert "指标改为订单数" in pending["understood_summary"]
    assert all(i["fields"]["metric"] == "amount" for i in pending["draft"]["intents"])
