import asyncio
from datetime import date

import pytest
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.models.function import FunctionModel

from app.ai.model_client import ModelSettings
from app.semantic.provider import CloudSemanticProvider, SemanticProviderUnavailable
from app.semantic.schemas import SemanticInput


@pytest.mark.parametrize(
    "failure,reason,message",
    [
        (TimeoutError("sensitive-provider-payload"), "timeout", "超时"),
        (
            ModelHTTPError(401, "private-model", "sensitive-provider-payload"),
            "authentication",
            "鉴权",
        ),
        (ModelHTTPError(429, "private-model", "sensitive-provider-payload"), "rate_limit", "繁忙"),
        (
            ModelHTTPError(503, "private-model", "sensitive-provider-payload"),
            "upstream",
            "暂时不可用",
        ),
        (UnexpectedModelBehavior("sensitive-provider-payload"), "invalid_response", "有效"),
    ],
)
def test_cloud_failure_reports_safe_reason_without_logging_provider_content(
    failure, reason, message, caplog
):
    def fail(messages, info):
        raise failure

    provider = CloudSemanticProvider(ModelSettings(), model=FunctionModel(fail))
    with pytest.raises(SemanticProviderUnavailable) as error:
        asyncio.run(
            provider.interpret(SemanticInput(question="今天销售概览", today=date(2026, 9, 22)))
        )
    assert error.value.reason == reason
    assert message in str(error.value)
    assert f"reason={reason}" in caplog.text
    assert "elapsed_ms=" in caplog.text
    assert "sensitive-provider-payload" not in caplog.text + str(error.value)
    assert "private-model" not in caplog.text + str(error.value)


def test_sdk_wrapped_timeout_is_still_reported_as_timeout(caplog):
    wrapped = ModelAPIError("private-model", "sensitive-provider-payload")
    wrapped.__cause__ = TimeoutError("sensitive-provider-payload")

    def fail(messages, info):
        raise wrapped

    provider = CloudSemanticProvider(ModelSettings(), model=FunctionModel(fail))
    with pytest.raises(SemanticProviderUnavailable) as error:
        asyncio.run(
            provider.interpret(SemanticInput(question="今天销售概览", today=date(2026, 9, 22)))
        )
    assert error.value.reason == "timeout"
    assert "超时" in str(error.value)
    assert "sensitive-provider-payload" not in caplog.text + str(error.value)
