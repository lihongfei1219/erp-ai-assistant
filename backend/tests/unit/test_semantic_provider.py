import asyncio
import json
from datetime import date

import httpx2
import pytest
from openai import AsyncOpenAI
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from app.ai.model_client import ModelSettings
from app.semantic.provider import (
    CloudSemanticProvider,
    SemanticPlanner,
    SemanticProviderUnavailable,
)
from app.semantic.schemas import SemanticInput, SemanticRequest


def test_cloud_adapter_returns_semantics_without_receiving_business_results():
    def respond(messages, info):
        text = str(messages)
        assert "退货" in text and "库存" in text and "九月十五号" in text
        assert "report" not in text and "api_key" not in text
        return ModelResponse(
            parts=[
                TextPart(
                    json.dumps(
                        {
                            "intents": [
                                {
                                    "domain": "returns",
                                    "operation": "existence",
                                    "time": "九月十五号",
                                },
                            ]
                        }
                    )
                )
            ]
        )

    provider = CloudSemanticProvider(ModelSettings(), model=FunctionModel(respond))
    result = asyncio.run(
        provider.interpret(SemanticInput(question="九月十五号有退货吗", today=date(2026, 9, 22)))
    )
    assert result.intents[0].domain == "returns"


def test_provider_protocol_accepts_an_alternative_without_executor_changes():
    class Alternative:
        async def interpret(self, request):
            return SemanticRequest(intents=[dict(domain="inventory", operation="ranking")])

    result = asyncio.run(
        SemanticPlanner(provider=Alternative()).interpret(
            SemanticInput(question="库存多的药", today=date(2026, 9, 22)),
        )
    )
    assert result.intents[0].domain == "inventory"


def test_provider_failure_does_not_expose_secrets_or_blame_customer():
    class Broken:
        async def interpret(self, request):
            raise RuntimeError("secret-api-key-provider-response")

    with pytest.raises(SemanticProviderUnavailable) as error:
        asyncio.run(
            SemanticPlanner(provider=Broken()).interpret(
                SemanticInput(question="今天卖了多少钱", today=date(2026, 9, 22)),
            )
        )
    assert "secret" not in str(error.value)


def test_malformed_adapter_cannot_inject_executable_fields():
    class Malformed:
        async def interpret(self, request):
            return {"intents": [{"domain": "sales", "sql": "select 1"}]}

    with pytest.raises(SemanticProviderUnavailable):
        asyncio.run(
            SemanticPlanner(provider=Malformed()).interpret(
                SemanticInput(question="销售", today=date(2026, 9, 22)),
            )
        )


@pytest.mark.parametrize(
    ("host", "model_name", "thinking_mode", "reasoning_effort"),
    [
        ("dashscope.aliyuncs.com", "deepseek-v4-flash-0731", True, "low"),
        ("dashscope-intl.aliyuncs.com", "deepseek-v4-pro-0813", True, "low"),
        ("dashscope-us.aliyuncs.com", "deepseek-v4.1-flash", True, "low"),
        ("workspace.cn-beijing.maas.aliyuncs.com", "deepseek-v4-flash-0731", True, "low"),
        ("dashscope.aliyuncs.com", "deepseek-v4-flash", False, None),
        ("workspace.cn-beijing.maas.aliyuncs.com", "qwen-plus", False, None),
        ("api.openai.com", "deepseek-v4-flash-0731", None, None),
        ("dashscope.aliyuncs.com.example.test", "deepseek-v4-flash-0731", None, None),
        (
            "workspace.cn-beijing.maas.aliyuncs.com.example.test",
            "deepseek-v4-flash-0731",
            None,
            None,
        ),
        ("notmaas.aliyuncs.com", "deepseek-v4-flash-0731", None, None),
    ],
)
def test_cloud_request_selects_supported_vendor_thinking_mode(
    host, model_name, thinking_mode, reasoning_effort
):
    captured = []
    semantics = {
        "intents": [
            {
                "domain": "sales",
                "operation": "ranking",
                "target": "product",
                "metric": "amount",
                "time": "九月五号",
            }
        ]
    }

    def respond(request):
        captured.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "synthetic-completion",
                "object": "chat.completion",
                "created": 0,
                "model": model_name,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": json.dumps(semantics)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            },
        )

    async def run():
        settings = ModelSettings(
            base_url=f"https://{host}/v1",
            model=model_name,
            api_key="synthetic-test-key",
            enabled=True,
        )
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
            async with AsyncOpenAI(
                base_url=settings.base_url,
                api_key=settings.api_key.get_secret_value(),
                http_client=http,
                max_retries=0,
            ) as client:
                model = OpenAIChatModel(
                    settings.model, provider=OpenAIProvider(openai_client=client)
                )
                return await CloudSemanticProvider(settings, model=model).interpret(
                    SemanticInput(question="九月五号那天什么商品卖的多", today=date(2026, 9, 22))
                )

    result = asyncio.run(run())
    assert result == SemanticRequest.model_validate(semantics)
    assert len(captured) == 1
    if thinking_mode is None:
        assert "enable_thinking" not in captured[0]
    else:
        assert captured[0].get("enable_thinking") is thinking_mode
    if reasoning_effort is None:
        assert "reasoning_effort" not in captured[0]
    else:
        assert captured[0].get("reasoning_effort") == reasoning_effort
