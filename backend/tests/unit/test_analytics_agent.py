import asyncio
import json

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from app.ai.model_client import ModelSettings
from app.analysis.sales_query import QueryUnavailable
from app.schemas.analytics import AnalysisQuestion

CONTEXT = {
    "today": "2026-09-20",
    "available_start": "2026-09-01",
    "available_end_exclusive": "2026-09-16",
    "timezone": "Asia/Shanghai",
}
PLAN = {
    "steps": [{"kind": "summary", "start_date": "2026-09-01", "end_date_exclusive": "2026-09-04"}]
}


def test_agent_returns_validated_plan_and_only_sends_planning_context():
    from app.ai.analytics_agent import AnalyticsPlanner

    captured = []

    def model(messages, info):
        captured.extend(messages)
        return ModelResponse(parts=[TextPart(json.dumps({"action": "run", "plan": PLAN}))])

    planner = AnalyticsPlanner(ModelSettings(), model=FunctionModel(model))
    result = asyncio.run(planner.plan(AnalysisQuestion(question="9月1日至3日销售概览"), CONTEXT))
    assert result.plan.steps[0].kind == "summary"
    assert "2026-09-20" in str(captured)
    assert "BUYER-A" not in str(captured)


def test_clarification_and_previous_plan():
    from app.ai.analytics_agent import AnalyticsPlanner

    def model(messages, info):
        assert "previous_plan" in str(messages)
        return ModelResponse(
            parts=[
                TextPart(
                    json.dumps({"action": "clarify", "explanation": "缺少成本数据，无法计算利润。"})
                )
            ]
        )

    result = asyncio.run(
        AnalyticsPlanner(ModelSettings(), model=FunctionModel(model)).plan(
            AnalysisQuestion(question="再看利润", previous_plan=PLAN), CONTEXT
        )
    )
    assert result.action == "clarify"
    assert result.plan is None


def test_disabled_model_does_not_attempt_network():
    from app.ai.analytics_agent import AnalyticsPlanner

    with pytest.raises(QueryUnavailable, match="未启用"):
        asyncio.run(
            AnalyticsPlanner(ModelSettings()).plan(AnalysisQuestion(question="销售"), CONTEXT)
        )


def test_invalid_model_output_fails_closed():
    from app.ai.analytics_agent import AnalyticsPlanner

    def model(messages, info):
        return ModelResponse(
            parts=[TextPart('{"action":"run","plan":{"steps":[{"sql":"drop table x"}]}}')]
        )

    with pytest.raises(QueryUnavailable, match="计划"):
        asyncio.run(
            AnalyticsPlanner(ModelSettings(), model=FunctionModel(model)).plan(
                AnalysisQuestion(question="销售"), CONTEXT
            )
        )
