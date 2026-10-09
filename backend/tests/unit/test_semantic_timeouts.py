"""Slow cloud responses have bounded time without expiring active graph leases."""

import asyncio
import json
import time
from datetime import date

import httpx2
import pytest

from app.ai.model_client import ModelSettings
from app.analysis.dialogue import converse
from app.core.timeouts import ANALYSIS_TIMEOUT, GRAPH_EXECUTION_LEASE, SEMANTIC_TIMEOUT
from app.orchestration import runtime
from app.orchestration.store import GraphConflict, GraphStore
from app.semantic import provider
from app.semantic.dialogue_schemas import ConversationRequest
from app.semantic.provider import (
    CloudSemanticProvider,
    SemanticPlanner,
    SemanticProviderUnavailable,
)
from app.semantic.schemas import SemanticInput


def test_cloud_transport_has_separate_connection_and_read_deadlines(monkeypatch):
    original = httpx2.AsyncClient
    configured = []

    def respond(request):
        return httpx2.Response(200, json={
            "id": "synthetic", "object": "chat.completion", "created": 0,
            "model": "synthetic", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps({
                    "intents": [{"domain": "sales", "operation": "summary"}],
                })}}],
        })

    class Client(original):
        def __init__(self, **kwargs):
            configured.append(kwargs)
            super().__init__(**kwargs, transport=httpx2.MockTransport(respond))

    monkeypatch.setattr(provider.httpx2, "AsyncClient", Client)
    settings = ModelSettings(
        enabled=True, base_url="https://model.example/v1", model="synthetic", api_key="test-only",
    )
    result = asyncio.run(CloudSemanticProvider(settings).interpret(
        SemanticInput(question="销售概览", today=date(2026, 10, 8))
    ))
    assert result.intents[0].operation == "summary"
    assert configured[0]["timeout"].connect == 8
    assert configured[0]["timeout"].read == 55
    assert configured[0]["trust_env"] is False
    assert 55 < SEMANTIC_TIMEOUT < ANALYSIS_TIMEOUT < GRAPH_EXECUTION_LEASE < 120


def test_total_timeout_cancels_once_without_a_network_retry(monkeypatch, caplog):
    calls = []

    async def slow(*args):
        calls.append(True)
        await asyncio.sleep(10)

    adapter = CloudSemanticProvider(ModelSettings(), model=object())
    monkeypatch.setattr(adapter, "_run", slow)
    monkeypatch.setattr(provider, "SEMANTIC_TIMEOUT", 0.02)
    started = time.monotonic()
    with pytest.raises(SemanticProviderUnavailable) as error:
        asyncio.run(adapter.interpret(SemanticInput(question="销售", today=date(2026, 10, 8))))
    assert error.value.reason == "timeout" and len(calls) == 1
    assert time.monotonic() - started < 2
    assert "timeout_stage=total_or_unspecified" in caplog.text


@pytest.mark.parametrize("failure,stage", [
    (httpx2.ConnectTimeout("secret"), "connect"),
    (httpx2.ReadTimeout("secret"), "read"),
])
def test_timeout_stage_follows_sdk_cause_without_logging_sensitive_details(failure, stage, caplog):
    wrapped = RuntimeError("secret response")
    wrapped.__cause__ = failure
    error = provider._unavailable(wrapped, time.monotonic())
    assert error.reason == "timeout"
    assert f"timeout_stage={stage}" in caplog.text
    assert "secret" not in caplog.text + str(error)


def test_slow_active_graph_cannot_be_reclaimed_or_pruned_at_old_60_second_limit(monkeypatch):
    now = 10000.0
    monkeypatch.setattr("app.orchestration.store.time.time", lambda: now)
    store = GraphStore()
    ticket, _ = store.claim("owner", "request", "fingerprint", "binding")
    with store.connect() as conn:
        conn.execute("UPDATE sessions SET busy_since=?, expires=? WHERE thread_id=?",
                     (now - 65, now - 1, ticket["thread_id"]))
    with pytest.raises(GraphConflict) as error:
        store.claim("owner", "request", "fingerprint", "binding")
    assert error.value.code == "request_busy"
    assert store.expired_threads() == []
    with store.connect() as conn:
        conn.execute("UPDATE sessions SET busy_since=? WHERE thread_id=?",
                     (now - GRAPH_EXECUTION_LEASE - 1, ticket["thread_id"]))
    with pytest.raises(GraphConflict) as error:
        store.claim("owner", "request", "fingerprint", "binding")
    assert error.value.code == "request_failed"
    assert store.expired_threads() == [ticket["thread_id"]]


def test_outer_deadline_still_fails_and_releases_graph_request(report, monkeypatch):
    class SlowProvider:
        async def interpret(self, request):
            await asyncio.sleep(10)

    monkeypatch.setattr(runtime, "ANALYSIS_TIMEOUT", 0.02)
    with pytest.raises(SemanticProviderUnavailable) as error:
        asyncio.run(converse(
            ConversationRequest(question="分析销售", request_id="outer-timeout"),
            report, SemanticPlanner(provider=SlowProvider()), date(2026, 10, 8),
        ))
    assert error.value.reason == "timeout"
    with GraphStore().connect() as conn:
        assert conn.execute("SELECT status FROM requests").fetchone()[0] == "failed"
        assert conn.execute("SELECT busy_request FROM sessions").fetchone()[0] is None
