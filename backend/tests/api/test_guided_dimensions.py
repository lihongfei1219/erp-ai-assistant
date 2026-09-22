from tests.api.test_guided_dialogue import Provider, post, setup


def test_region_ranking_preserves_dimension_and_guides_instead_of_failing(
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
                    "operation": "ranking",
                    "target": "region",
                    "metric": "orders",
                    "time": "9月1号",
                }
            ]
        }
    )
    client = setup(extract, window, scope, source_as_of, provider)
    first = post(client, question="9月1号哪些地区成交频繁")
    assert first["status"] == "capability_gap" and first["result"] is None
    assert "地区" in first["understood_summary"]
    assert "地区" in first["clarification"]["question"]
    assert first["clarification"]["field"] == "target"
    goal = first["draft"]["intents"][0]
    assert goal["fields"]["target"] == "region" and goal["constraints"] == []
    buyer = next(c for c in first["choices"] if "客户" in c["label"])
    second = post(client, choice_id=buyer["id"], conversation_token=first["conversation_token"])
    assert second["status"] == "result"
    step = second["result"]["plan"]["steps"][0]
    assert (step["kind"], step["metric"], step["start_date"]) == (
        "buyer_ranking",
        "orders",
        "2026-09-01",
    )
    assert len(provider.requests) == 1


def test_arbitrary_dimension_can_be_understood_without_inventing_executor(
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
                    "operation": "ranking",
                    "target": "配送线路",
                    "metric": "amount",
                    "time": "9月1号",
                }
            ]
        }
    )
    client = setup(extract, window, scope, source_as_of, provider)
    first = post(client, question="9月1号按配送线路看销售排行")
    assert first["status"] == "capability_gap" and first["result"] is None
    assert "配送线路" in first["clarification"]["question"]
    assert first["draft"]["intents"][0]["fields"]["target"] == "配送线路"


def test_summary_by_unsupported_dimension_offers_an_explicit_grouped_alternative(
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
                    "operation": "summary",
                    "target": "region",
                    "metric": "orders",
                    "time": "9月1号",
                }
            ]
        }
    )
    client = setup(extract, window, scope, source_as_of, provider)
    first = post(client, question="9月1号按地区看成交情况")
    buyer = next(c for c in first["choices"] if "客户" in c["label"])
    assert "排行" in buyer["label"]
    second = post(client, choice_id=buyer["id"], conversation_token=first["conversation_token"])
    assert second["status"] == "result"
    assert second["result"]["plan"]["steps"][0]["kind"] == "buyer_ranking"
