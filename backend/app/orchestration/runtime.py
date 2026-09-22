"""Durable, scoped, idempotent entry point shared by web and Feishu."""

import asyncio
import hashlib
import hmac
import json
import os
from uuid import uuid4

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from langsmith import tracing_context

from app.analysis.dialogue import DialogueOutcome, legacy_converse
from app.orchestration.graph import TurnRuntime, build_graph
from app.orchestration.store import GRAPH_VERSION, GraphConflict, GraphStore, owner_key
from app.semantic.catalog import load_catalog
from app.semantic.context import signing_key, snapshot_key
from app.semantic.dialogue_schemas import DialogueTurn
from app.semantic.provider import SemanticProviderUnavailable
from app.semantic.schemas import GraphReference, SemanticContext


def binding_for(report):
    value = f"{snapshot_key(report)}:{load_catalog()['version']}:{GRAPH_VERSION}"
    return hashlib.sha256(value.encode()).hexdigest()


def outcome_from(payload):
    return DialogueOutcome(
        DialogueTurn.model_validate(payload["turn"]),
        SemanticContext.model_validate(payload["context"]),
    )


async def run_dialogue(
    body, report, planner, today, *, previous=None, channel="web", owner="local-workspace"
):
    engine = os.environ.get("ERP_ANALYSIS_ENGINE", "langgraph")
    if engine not in {"langgraph", "legacy"}:
        raise GraphConflict("分析引擎配置无效，请联系管理员。")
    if previous and previous.catalog_version != load_catalog()["version"]:
        raise GraphConflict("业务语义版本已更新，请确认并恢复草稿后继续。")
    if engine == "legacy":
        if previous and previous.runtime_ref:
            raise GraphConflict("分析引擎已切换，请确认并恢复草稿后继续。")
        return await legacy_converse(body, report, planner, today, previous=previous)
    store = GraphStore(channel)
    binding, principal = binding_for(report), owner_key(owner)
    ref = previous.runtime_ref if previous else None
    if ref:
        previous = store.load_context(ref, binding, owner=principal)
    # HMAC avoids retaining raw questions, including guessable short questions, in the ledger.
    fingerprint = hmac.new(
        signing_key().encode(),
        json.dumps(
            {
                "body": body.model_dump(mode="json", exclude={"conversation_token", "request_id"}),
                "binding": binding,
                "previous": previous.model_dump(mode="json") if previous else None,
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode(),
        hashlib.sha256,
    ).hexdigest()
    ticket, cached = store.claim(
        principal, body.request_id or uuid4().hex, fingerprint, binding, ref
    )
    if cached:
        return outcome_from(cached)
    try:
        # Each invocation owns and closes its SQLite connection, also across asyncio.run in Feishu.
        async with AsyncSqliteSaver.from_conn_string(str(store.checkpoints)) as saver:
            expired = store.expired_threads()
            for thread_id in expired:
                await saver.adelete_thread(thread_id)
            store.prune(expired)
            graph = build_graph(saver)
            config = {"configurable": {"thread_id": ticket["thread_id"]}, "recursion_limit": 20}
            with tracing_context(enabled=False):
                state = await graph.aget_state(config)
                can_resume = (
                    ref and state.next == ("wait",) and state.values.get("revision") == ref.revision
                )
                inputs = (
                    Command(resume={"request_id": ticket["request_id"]})
                    if can_resume
                    else {
                        "request_id": ticket["request_id"],
                    }
                )
                async with asyncio.timeout(45):
                    state = await graph.ainvoke(
                        inputs,
                        config,
                        durability="sync",
                        context=TurnRuntime(body, report, planner, today, previous, store, ticket),
                    )
        payload = store.artifact(state["outcome_ref"])
        payload["context"]["runtime_ref"] = GraphReference(
            channel=channel,
            thread_id=ticket["thread_id"],
            revision=ticket["base_revision"] + 1,
        ).model_dump(mode="json")
        store.complete(ticket, payload)
        return outcome_from(payload)
    except TimeoutError:
        store.fail(ticket)
        raise SemanticProviderUnavailable(
            "分析流程响应超时，请稍后重试。", reason="timeout"
        ) from None
    except BaseException:
        store.fail(ticket)
        raise
