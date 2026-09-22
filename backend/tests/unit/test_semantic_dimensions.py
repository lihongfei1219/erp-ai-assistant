"""Understanding a grouping dimension must not imply executor support for it."""

import asyncio
import json
from datetime import date

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from app.ai.model_client import ModelSettings
from app.semantic.compiler import compile_request
from app.semantic.provider import CloudSemanticProvider
from app.semantic.schemas import SemanticInput, SemanticIntent, SemanticRequest

TODAY = date(2026, 9, 22)


def compile_intents(intents, *, previous=None, mode="new", **extra):
    return compile_request(
        SemanticRequest(intents=intents, mode=mode, **extra),
        question="九月十号的分析",
        today=TODAY,
        previous=previous,
    )


def test_provider_accepts_region_grouping_without_schema_repair():
    calls = 0

    def respond(messages, info):
        nonlocal calls
        calls += 1
        return ModelResponse(
            parts=[
                TextPart(
                    json.dumps(
                        {
                            "intents": [
                                {
                                    "domain": "sales",
                                    "operation": "ranking",
                                    "target": "region",
                                    "metric": "orders",
                                    "time": "九月十号",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                )
            ]
        )

    provider = CloudSemanticProvider(ModelSettings(), model=FunctionModel(respond))
    request = asyncio.run(
        provider.interpret(
            SemanticInput(question="九月十号哪些地区成交频繁", today=TODAY)
        )
    )
    assert calls == 1
    result = compile_request(request, question="九月十号哪些地区成交频繁", today=TODAY)
    assert result.status == "unsupported"
    assert result.plan is None
    assert result.context.intents[0].target == "region"
    assert result.context.intents[0].metric == "orders"
    assert result.context.intents[0].time == "2026-09-10至2026-09-10"


@pytest.mark.parametrize("target", ["region", "品牌系列"])
@pytest.mark.parametrize("operation", ["ranking", "summary", "comparison"])
def test_unavailable_groupings_are_preserved_and_never_ignored(target, operation):
    result = compile_intents(
        [dict(domain="sales", operation=operation, target=target, time="九月十号")]
    )
    assert result.status == "unsupported"
    assert result.plan is None
    intent = result.context.intents[0]
    assert (intent.target, intent.operation) == (target, operation)
    assert intent.time == "2026-09-10至2026-09-10"
    assert intent.filters == []
    assert intent.unresolved == []
    assert result.context.issues == []
    assert "维度" in result.message


def test_combined_supported_and_unavailable_groupings_do_not_execute_a_subset():
    result = compile_intents(
        [
            dict(domain="sales", operation="ranking", target="product", time="九月十号"),
            dict(domain="sales", operation="ranking", target="region", time="九月十号"),
        ]
    )
    assert result.status == "unsupported"
    assert result.plan is None
    assert [i.target for i in result.context.intents] == ["product", "region"]


def test_changed_grouping_keeps_other_goals_and_verified_dates():
    original = compile_intents(
        [
            dict(domain="sales", operation="ranking", target="product", time="九月十号"),
            dict(domain="sales", operation="ranking", target="buyer", time="九月十号"),
        ]
    )
    assert original.status == "ready"
    first, second = original.context.intents
    changed = compile_intents(
        [dict(id=second.id, target="region")],
        previous=original.context,
        mode="followup",
    )
    assert changed.status == "unsupported"
    assert changed.plan is None
    assert [i.id for i in changed.context.intents] == [first.id, second.id]
    assert [i.target for i in changed.context.intents] == ["product", "region"]
    assert all(i.time == "2026-09-10至2026-09-10" for i in changed.context.intents)


def test_explicit_alternative_dimension_edit_preserves_metric_and_date():
    original = compile_intents(
        [
            dict(
                domain="sales",
                operation="ranking",
                target="region",
                metric="orders",
                time="九月十号",
            )
        ]
    )
    changed = compile_intents(
        [],
        previous=original.context,
        mode="answer",
        edits=[
            dict(
                intent_id=original.context.intents[0].id,
                operation="set",
                field="target",
                value="buyer",
            )
        ],
    )
    assert changed.status == "ready"
    step = changed.plan.steps[0]
    assert (step.kind, step.metric) == ("buyer_ranking", "orders")
    assert step.start_date == date(2026, 9, 10)
    assert step.end_date_exclusive == date(2026, 9, 11)


@pytest.mark.parametrize("target", ["", "x" * 81])
def test_grouping_label_still_has_bounded_nonempty_contract(target):
    with pytest.raises(ValidationError):
        SemanticIntent(target=target)
