"""Replaceable cloud adapter. Business execution depends only on SemanticProvider."""

import asyncio
import json
import logging
import time
from typing import Protocol
from urllib.parse import urlsplit

import httpx2
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI
from pydantic import ValidationError
from pydantic_ai import Agent, PromptedOutput
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import UsageLimits

from app.ai.model_client import ModelSettings
from app.analysis.sales_query import QueryUnavailable
from app.semantic.catalog import load_catalog
from app.semantic.prompt import PROMPT
from app.semantic.schemas import SemanticInput, SemanticRequest

logger = logging.getLogger(__name__)


def _request_settings(base_url: str, model_name: str) -> dict:
    settings = {"temperature": 0, "max_tokens": 3000}
    host = urlsplit(base_url).hostname or ""
    if host in {
        "dashscope.aliyuncs.com",
        "dashscope-intl.aliyuncs.com",
        "dashscope-us.aliyuncs.com",
    } or host.endswith(".maas.aliyuncs.com"):
        # This is a Model Studio parameter, not part of the generic OpenAI protocol.
        if model_name in {"deepseek-v4-flash-0731", "deepseek-v4-pro-0813", "deepseek-v4.1-flash"}:
            # Preserve semantic reasoning without silently using the vendor's high default.
            settings["extra_body"] = {"enable_thinking": True}
            settings["openai_reasoning_effort"] = "low"
        else:
            settings["extra_body"] = {"enable_thinking": False}
    return settings


def _failure_reason(error: Exception) -> str:
    seen = set()
    reason = "unavailable"
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, (TimeoutError, APITimeoutError, httpx2.TimeoutException)):
            return "timeout"
        if isinstance(error, ModelHTTPError):
            if error.status_code in {401, 403}:
                return "authentication"
            if error.status_code == 429:
                return "rate_limit"
            return "upstream"
        if isinstance(error, (UnexpectedModelBehavior, ValidationError)):
            return "invalid_response"
        if isinstance(error, (APIConnectionError, httpx2.RequestError)):
            reason = "connection"
        error = error.__cause__ or error.__context__
    return reason


def _unavailable(error: Exception, started: float) -> "SemanticProviderUnavailable":
    reason = _failure_reason(error)
    # Never log exception messages, response bodies, prompts, endpoints or credentials.
    logger.warning(
        "semantic_provider_failed reason=%s elapsed_ms=%d",
        reason,
        round((time.monotonic() - started) * 1000),
    )
    messages = {
        "timeout": "云端模型响应超时，请稍后重试。",
        "authentication": "云端模型鉴权失败，请联系管理员检查模型配置。",
        "rate_limit": "云端模型服务繁忙，请稍后重试。",
        "upstream": "云端模型服务暂时不可用，请稍后重试。",
        "invalid_response": "云端模型未返回有效的理解结果，请稍后重试。",
        "connection": "暂时无法连接云端模型，请稍后重试。",
        "unavailable": "云端语义服务暂时不可用，请稍后重试。",
    }
    return SemanticProviderUnavailable(messages[reason], reason=reason)


class SemanticProvider(Protocol):
    async def interpret(self, request: SemanticInput) -> SemanticRequest: ...


class SemanticProviderUnavailable(QueryUnavailable):
    def __init__(self, message: str, *, reason: str = "unavailable"):
        super().__init__(message)
        self.reason = reason


class CloudSemanticProvider:
    """Default OpenAI-compatible transport; another provider can implement the protocol."""

    def __init__(self, settings: ModelSettings, *, model=None):
        self.settings, self.model = settings, model

    async def _run(self, model, request):
        agent = Agent(
            model,
            output_type=PromptedOutput(SemanticRequest),
            instructions=PROMPT
            + "\n业务语义配置："
            + json.dumps(load_catalog(), ensure_ascii=False),
            retries=1,
            model_settings=_request_settings(self.settings.base_url, self.settings.model),
        )
        result = await agent.run(
            request.model_dump_json(),
            usage_limits=UsageLimits(request_limit=2),
        )
        return result.output

    async def interpret(self, request: SemanticInput) -> SemanticRequest:
        if self.model is None and not self.settings.enabled:
            raise SemanticProviderUnavailable("自然语言理解未启用，请配置云端模型或使用手动分析。")
        started = time.monotonic()
        try:
            async with asyncio.timeout(30):
                if self.model is not None:
                    return await self._run(self.model, request)
                async with httpx2.AsyncClient(
                    timeout=25, follow_redirects=False, trust_env=False
                ) as http:
                    async with AsyncOpenAI(
                        base_url=self.settings.base_url,
                        api_key=self.settings.api_key.get_secret_value(),
                        max_retries=0,
                        http_client=http,
                    ) as client:
                        model = OpenAIChatModel(
                            self.settings.model,
                            provider=OpenAIProvider(openai_client=client),
                        )
                        return await self._run(model, request)
        except Exception as exc:
            raise _unavailable(exc, started) from None


class SemanticPlanner:
    """Channel-independent interface; providers never receive reports or executor objects."""

    def __init__(
        self, settings: ModelSettings | None = None, *, provider: SemanticProvider | None = None
    ):
        settings = settings or ModelSettings()
        self.enabled = provider is not None or settings.enabled
        self.provider = provider if provider is not None else CloudSemanticProvider(settings)

    async def interpret(self, request: SemanticInput) -> SemanticRequest:
        started = time.monotonic()
        try:
            response = await self.provider.interpret(request)
            # Revalidate custom adapter output at the boundary, including constructed models.
            value = response.model_dump() if isinstance(response, SemanticRequest) else response
            return SemanticRequest.model_validate(value)
        except SemanticProviderUnavailable:
            raise
        except Exception as exc:
            raise _unavailable(exc, started) from None
