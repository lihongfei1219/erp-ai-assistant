"""Exercise real SQLite checkpoints across fresh event loops, never a cloud or ERP connection."""

import asyncio
import base64
import json
import os
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.analysis.dialogue import converse, legacy_converse
from app.analysis.sales_query import QueryUnavailable
from app.orchestration.graph import build_graph
from app.orchestration.runtime import binding_for
from app.orchestration.store import GraphConflict, GraphStore, owner_key
from app.semantic.context import decode_context, encode_context
from app.semantic.dialogue_schemas import ConversationRequest
from app.semantic.provider import SemanticPlanner, SemanticProviderUnavailable
from app.semantic.schemas import SemanticRequest

TODAY = date(2026, 9, 22)
SECRET = "local-test-signature-secret-00000000"
READY = {"intents": [{"domain": "sales", "operation": "ranking", "time": "9月2号"}]}
PENDING = {"intents": [{"domain": "sales", "operation": "ranking"}]}


class Provider:
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.requests = []

    async def interpret(self, request):
        self.requests.append(request)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return SemanticRequest.model_validate(output)


def run(report, provider, *, previous=None, channel="web", owner="local-workspace", **body):
    return asyncio.run(
        converse(
            ConversationRequest(**body),
            report,
            SemanticPlanner(provider=provider),
            TODAY,
            previous=previous,
            channel=channel,
            owner=owner,
        )
    )


def history(context):
    async def read():
        store = GraphStore(context.runtime_ref.channel)
        async with AsyncSqliteSaver.from_conn_string(str(store.checkpoints)) as saver:
            graph = build_graph(saver)
            config = {"configurable": {"thread_id": context.runtime_ref.thread_id}}
            return [item async for item in graph.aget_state_history(config)]

    return asyncio.run(read())


def test_restart_resumes_interrupt_and_choice_without_model(multi_report):
    provider = Provider(PENDING)
    first = run(multi_report, provider, question="看一下卖得好的", request_id="first")
    assert history(first.context)[0].next == ("wait",)
    token = encode_context(first.context, multi_report, SECRET)
    data = json.loads(base64.urlsafe_b64decode(token.split(".")[0]))
    assert "graph" in data and "context" not in data
    previous = decode_context(token, multi_report, SECRET)
    choice = next(c for c in first.turn.choices if c.action.kind == "date_range")
    second = run(
        multi_report,
        provider,
        previous=previous,
        choice_id=choice.id,
        date_range={"start": "2026-09-01", "end_exclusive": "2026-09-03"},
    )
    assert second.turn.status == "result"
    assert len(provider.requests) == 1
    assert second.context.runtime_ref.thread_id == first.context.runtime_ref.thread_id
    assert second.context.runtime_ref.revision == 2
    assert history(second.context)[0].next == ()
    assert second.context.intents[0].id == first.context.intents[0].id


def test_fresh_python_process_resumes_durable_pending_turn(multi_report, tmp_path):
    first = run(multi_report, Provider(PENDING), question="商品排行")
    choice = next(c for c in first.turn.choices if c.action.kind == "date_range")
    fixture = tmp_path / "restart.json"
    fixture.write_text(
        json.dumps(
            {
                "report": multi_report.model_dump(mode="json"),
                "context": first.context.model_dump(mode="json"),
                "body": {
                    "choice_id": choice.id,
                    "date_range": {
                        "start": "2026-09-01",
                        "end_exclusive": "2026-09-03",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    script = """
import asyncio, json, sys
from datetime import date
from app.analysis.dialogue import converse
from app.schemas.sales import SalesReport
from app.semantic.schemas import SemanticContext
from app.semantic.dialogue_schemas import ConversationRequest

class NoModel:
    async def interpret(self, request):
        raise AssertionError('A signed date choice must not call the model')

with open(sys.argv[1], encoding='utf-8') as stream:
    data = json.load(stream)
result = asyncio.run(converse(
    ConversationRequest.model_validate(data['body']),
    SalesReport.model_validate(data['report']), NoModel(), date(2026, 9, 22),
    previous=SemanticContext.model_validate(data['context']),
))
assert result.turn.status == 'result'
assert result.context.runtime_ref.revision == 2
print('resumed')
"""
    process = subprocess.run(
        [sys.executable, "-c", script, str(fixture)],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == "resumed"


def test_exact_duplicate_replays_result_and_mismatch_is_rejected(multi_report):
    provider = Provider(READY)
    body = dict(question="9月2号畅销商品", request_id="same")
    first = run(multi_report, provider, **body)
    again = run(multi_report, provider, **body)
    assert first == again
    assert len(provider.requests) == 1
    with pytest.raises(GraphConflict) as error:
        run(multi_report, provider, **{**body, "question": "另一问题"})
    assert error.value.code == "request_conflict"


def test_stale_revision_cannot_mutate_but_exact_replay_is_allowed(multi_report):
    provider = Provider(READY, {"mode": "followup", "intents": [{"order": "ascending"}]})
    first = run(multi_report, provider, question="9月2号畅销商品")
    second = run(
        multi_report, provider, question="从少到多", previous=first.context, request_id="next"
    )
    replay = run(
        multi_report, provider, question="从少到多", previous=first.context, request_id="next"
    )
    assert second == replay
    with pytest.raises(GraphConflict) as error:
        run(multi_report, provider, question="再看看", previous=first.context, request_id="stale")
    assert error.value.code == "context_expired"
    assert len(provider.requests) == 2
    assert provider.requests[1].previous.runtime_ref is None


@pytest.mark.parametrize("mismatch", ["owner", "channel", "snapshot", "expiry", "graph", "catalog"])
def test_thread_is_bound_to_owner_channel_snapshot_and_expiry(multi_report, mismatch):
    provider = Provider(READY)
    first = run(multi_report, provider, question="9月2号畅销商品")
    kwargs = {}
    previous = first.context
    if mismatch == "owner":
        kwargs["owner"] = "another-user"
    elif mismatch == "channel":
        kwargs["channel"] = "feishu"
    elif mismatch == "snapshot":
        multi_report = multi_report.model_copy(
            update={
                "metadata": multi_report.metadata.model_copy(
                    update={"metric_version": "changed"},
                )
            }
        )
    elif mismatch == "expiry":
        with GraphStore().connect() as conn:
            conn.execute("UPDATE sessions SET expires=0")
    elif mismatch == "graph":
        previous = first.context.model_copy(
            update={
                "runtime_ref": first.context.runtime_ref.model_copy(update={"version": "old"}),
            }
        )
    else:
        previous = first.context.model_copy(update={"catalog_version": "old"})
    with pytest.raises(GraphConflict):
        run(multi_report, provider, question="继续", previous=previous, **kwargs)
    assert len(provider.requests) == 1


def test_failed_model_call_needs_explicit_new_request_and_keeps_context(multi_report):
    provider = Provider(PENDING, SemanticProviderUnavailable("模型超时"), READY)
    first = run(multi_report, provider, question="看看排行")
    body = dict(question="9月2号", request_id="failed", previous=first.context)
    with pytest.raises(SemanticProviderUnavailable):
        run(multi_report, provider, **body)
    with pytest.raises(GraphConflict) as error:
        run(multi_report, provider, **body)
    assert error.value.code == "request_failed"
    third = run(multi_report, provider, **{**body, "request_id": "explicit-retry"})
    assert third.turn.status == "result"
    assert third.context.runtime_ref.revision == 2
    assert len(provider.requests) == 3


def test_concurrent_turns_serialize_before_calling_provider(multi_report):
    provider = Provider(READY)
    first = run(multi_report, provider, question="9月2号畅销商品")

    async def race():
        entered, release = asyncio.Event(), asyncio.Event()

        class Slow:
            async def interpret(self, request):
                entered.set()
                await release.wait()
                return SemanticRequest.model_validate(READY)

        task = asyncio.create_task(
            converse(
                ConversationRequest(question="继续", request_id="one"),
                multi_report,
                Slow(),
                TODAY,
                previous=first.context,
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
        try:
            with pytest.raises(GraphConflict) as error:
                await converse(
                    ConversationRequest(question="继续", request_id="two"),
                    multi_report,
                    Slow(),
                    TODAY,
                    previous=first.context,
                )
            assert error.value.code == "request_busy"
        finally:
            release.set()
            await task

    asyncio.run(race())


def test_checkpoints_contain_stages_but_no_raw_question_business_rows_or_owner(multi_report):
    raw = "9月2号商品排行-private-question-marker-0001"
    owner = "private-user-identity-marker-0002"
    first = run(multi_report, Provider(READY), question=raw, owner=owner)
    states = history(first.context)
    content = json.dumps([s.values for s in states], ensure_ascii=False)
    assert raw not in content and owner not in content
    assert "BUYER-A" not in content and "SKU-A" not in content
    assert "orders" not in states[0].values  # No report/ERP row collections.
    assert states[0].values["result_ref"]
    assert {n for s in states for n in s.next} >= {
        "load",
        "understand",
        "compile",
        "validate",
        "execute",
        "respond",
    }
    with GraphStore().connect() as conn:
        rows = [tuple(r) for r in conn.execute("SELECT owner,fingerprint FROM requests")]
    assert raw not in str(rows) and owner not in str(rows)
    conn = sqlite3.connect(GraphStore().checkpoints)
    try:
        persisted = conn.execute("SELECT checkpoint,metadata FROM checkpoints").fetchall()
        persisted += conn.execute("SELECT value FROM writes").fetchall()
        content = b"".join(
            v.encode() if isinstance(v, str) else v for row in persisted for v in row
        )
        assert all(value.encode() not in content for value in (raw, owner, "BUYER-A", "SKU-A"))
    finally:
        conn.close()


def test_legacy_signed_choice_is_imported_and_fallback_requires_explicit_restore(
    multi_report, monkeypatch
):
    first = asyncio.run(
        legacy_converse(
            ConversationRequest(question="看排行"),
            multi_report,
            Provider(PENDING),
            TODAY,
        )
    )
    previous = decode_context(
        encode_context(first.context, multi_report, SECRET), multi_report, SECRET
    )
    choice = next(c for c in first.turn.choices if c.action.kind == "date_range")
    migrated = run(
        multi_report,
        Provider(),
        previous=previous,
        choice_id=choice.id,
        date_range={"start": "2026-09-01", "end_exclusive": "2026-09-03"},
    )
    assert migrated.turn.status == "result"
    assert migrated.context.intents[0].id == first.context.intents[0].id
    monkeypatch.setenv("ERP_ANALYSIS_ENGINE", "legacy")
    with pytest.raises(GraphConflict):
        run(multi_report, Provider(), previous=migrated.context, question="继续")
    assert run(multi_report, Provider(READY), question="9月2号畅销商品").context.runtime_ref is None


def test_expired_checkpoints_and_artifacts_are_pruned_on_next_turn(multi_report):
    first = run(multi_report, Provider(READY), question="9月2号畅销商品")
    store = GraphStore()
    old_artifact = store.save_artifact({"obsolete": True})
    with store.connect() as conn:
        conn.execute("UPDATE sessions SET expires=0")
        conn.execute("UPDATE artifacts SET expires=0")
    run(multi_report, Provider(READY), question="9月2号畅销商品")
    assert history(first.context) == []
    with pytest.raises(GraphConflict):
        store.artifact(old_artifact)
    with pytest.raises(GraphConflict):
        store.load_context(
            first.context.runtime_ref, binding_for(multi_report), owner=owner_key("local-workspace")
        )


def test_invalid_choice_does_not_consume_pending_context(multi_report):
    first = run(multi_report, Provider(PENDING), question="看排行")
    with pytest.raises(QueryUnavailable):
        run(multi_report, Provider(), previous=first.context, choice_id="forged")
    choice = next(c for c in first.turn.choices if c.action.kind == "date_range")
    next_turn = run(
        multi_report,
        Provider(),
        previous=first.context,
        choice_id=choice.id,
        date_range={"start": "2026-09-01", "end_exclusive": "2026-09-03"},
    )
    assert next_turn.turn.status == "result"


def test_abandoned_request_is_not_automatically_reexecuted_or_busy_forever(multi_report):
    store = GraphStore()
    principal = owner_key("local-workspace")
    binding = binding_for(multi_report)
    ticket, _ = store.claim(principal, "abandoned", "digest", binding)
    with store.connect() as conn:
        conn.execute("UPDATE sessions SET busy_since=0 WHERE thread_id=?", (ticket["thread_id"],))
    with pytest.raises(GraphConflict) as error:
        store.claim(principal, "abandoned", "digest", binding)
    assert error.value.code == "request_failed"
