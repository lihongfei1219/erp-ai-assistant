"""Explicit dialogue graph. Runtime inputs and business rows never become graph state."""

from dataclasses import dataclass
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt
from starlette.concurrency import run_in_threadpool

from app.analysis.analytics import execute_analysis
from app.analysis.dialogue import _apply_choice, build_turn
from app.analysis.operations import executable_domains
from app.analysis.planning import (
    PlanningResult,
    finish_compilation,
    prepare_semantic_input,
    resolve_question,
)
from app.analysis.sales_query import QueryUnavailable
from app.schemas.analytics import AnalysisPlan, AnalysisQuestion, AnalysisResponse
from app.semantic.compiler import Compilation, compile_request
from app.semantic.provider import SemanticProviderUnavailable
from app.semantic.schemas import SemanticContext, SemanticRequest


class DialogueState(TypedDict, total=False):
    request_id: str
    revision: int
    semantic: dict | None
    compilation: dict | None
    plan: dict | None
    interpretation: list[str]
    context: dict | None
    result_ref: str | None
    outcome_ref: str | None
    status: str
    model_calls_reserved: int


@dataclass
class TurnRuntime:
    body: object
    report: object
    planner: object
    today: object
    previous: object
    store: object
    ticket: dict

    @property
    def question(self):
        return AnalysisQuestion(question=self.body.question or "应用已选择的条件")


def pack(compiled):
    if compiled is None:
        return None
    return {
        "status": compiled.status,
        "context": compiled.context.model_dump(mode="json", exclude={"runtime_ref"}),
        "plan": compiled.plan.model_dump(mode="json") if compiled.plan else None,
        "message": compiled.message,
    }


def unpack(value):
    if value is None:
        return None
    return Compilation(
        value["status"],
        SemanticContext.model_validate(value["context"]),
        AnalysisPlan.model_validate(value["plan"]) if value["plan"] else None,
        value["message"],
    )


def resolution(state):
    return PlanningResult(
        AnalysisPlan.model_validate(state["plan"]) if state["plan"] else None,
        state["interpretation"],
        unpack(state["compilation"]),
    )


def load_turn(state, runtime: Runtime[TurnRuntime]):
    turn = runtime.context
    return {
        "request_id": turn.ticket["request_id"],
        "revision": turn.ticket["base_revision"] + 1,
        "semantic": None,
        "compilation": None,
        "plan": None,
        "interpretation": [],
        "result_ref": None,
        "outcome_ref": None,
        "context": turn.previous.model_dump(mode="json", exclude={"runtime_ref"})
        if turn.previous
        else None,
        "status": "planning",
        # Reserve before the provider call. The provider owns its maximum two HTTP attempts.
        "model_calls_reserved": 0 if turn.body.choice_id else 1,
    }


async def understand(state, runtime: Runtime[TurnRuntime]):
    turn = runtime.context
    if turn.body.choice_id:
        return {}
    if not hasattr(turn.planner, "interpret"):
        planned = await resolve_question(
            turn.question,
            turn.report,
            turn.planner,
            turn.today,
            conversation=turn.previous,
        )
        return {
            "compilation": pack(planned.semantic),
            "plan": planned.plan.model_dump(mode="json") if planned.plan else None,
            "interpretation": planned.interpretation,
        }
    request = prepare_semantic_input(turn.question, turn.report, turn.today, turn.previous)
    try:
        understood = await turn.planner.interpret(request)
        understood = SemanticRequest.model_validate(understood)
    except SemanticProviderUnavailable:
        raise
    except Exception:
        raise SemanticProviderUnavailable("云端模型未返回有效的理解结果，请稍后重试。") from None
    return {"semantic": understood.model_dump(mode="json")}


def compile_plan(state, runtime: Runtime[TurnRuntime]):
    turn = runtime.context
    if turn.body.choice_id:
        compiled = _apply_choice(
            turn.body,
            turn.previous,
            turn.today,
            domains=executable_domains(turn.report),
        )
    elif state["semantic"] is not None:
        compiled = compile_request(
            SemanticRequest.model_validate(state["semantic"]),
            question=turn.body.question,
            today=turn.today,
            previous=turn.previous,
            domains=executable_domains(turn.report),
        )
    else:
        return {}
    return {"compilation": pack(compiled)}


def validate(state, runtime: Runtime[TurnRuntime]):
    turn = runtime.context
    if state["compilation"] is None:
        return {}  # Legacy injected planner already applied the same scope/data gates.
    planned = finish_compilation(unpack(state["compilation"]), turn.question, turn.report)
    return {
        "compilation": pack(planned.semantic),
        "plan": planned.plan.model_dump(mode="json") if planned.plan else None,
        "interpretation": planned.interpretation,
    }


async def execute(state, runtime: Runtime[TurnRuntime]):
    turn = runtime.context
    result = await run_in_threadpool(
        execute_analysis,
        turn.report,
        AnalysisPlan.model_validate(state["plan"]),
    )
    result = result.model_copy(update={"interpretation": state["interpretation"]})
    return {"result_ref": turn.store.save_artifact(result.model_dump(mode="json"))}


def respond(state, runtime: Runtime[TurnRuntime]):
    turn = runtime.context
    result = (
        AnalysisResponse.model_validate(turn.store.artifact(state["result_ref"]))
        if state["result_ref"]
        else None
    )
    outcome = build_turn(
        resolution(state),
        turn.report,
        turn.today,
        previous=turn.previous,
        result=result,
    )
    context = outcome.context.model_dump(mode="json", exclude={"runtime_ref"})
    ref = turn.store.save_artifact(
        {"turn": outcome.turn.model_dump(mode="json"), "context": context}
    )
    return {"context": context, "outcome_ref": ref, "status": outcome.turn.status}


def wait_for_input(state):
    # On resume LangGraph restarts this node. No model call, execution or persistence here.
    interrupt({"kind": "needs_input", "revision": state["revision"]})
    return {}


def guarded(function):
    """Checkpoints can contain task errors: never persist an unknown exception's payload."""
    import inspect

    if inspect.iscoroutinefunction(function):

        async def call(state, runtime: Runtime[TurnRuntime]):
            try:
                return await function(state, runtime)
            except QueryUnavailable:
                raise
            except Exception:
                raise QueryUnavailable("分析流程未完成，请重新提交或联系管理员。") from None
    else:

        def call(state, runtime: Runtime[TurnRuntime]):
            try:
                return function(state, runtime)
            except QueryUnavailable:
                raise
            except Exception:
                raise QueryUnavailable("分析流程未完成，请重新提交或联系管理员。") from None

    return call


def build_graph(checkpointer):
    builder = StateGraph(DialogueState, context_schema=TurnRuntime)
    for name, function in (
        ("load", load_turn),
        ("understand", understand),
        ("compile", compile_plan),
        ("validate", validate),
        ("execute", execute),
        ("respond", respond),
    ):
        builder.add_node(name, guarded(function))
    builder.add_node("wait", wait_for_input)
    builder.add_edge(START, "load")
    builder.add_edge("load", "understand")
    builder.add_edge("understand", "compile")
    builder.add_edge("compile", "validate")
    builder.add_conditional_edges("validate", lambda s: "execute" if s["plan"] else "respond")
    builder.add_edge("execute", "respond")
    builder.add_conditional_edges("respond", lambda s: END if s["status"] == "result" else "wait")
    builder.add_edge("wait", "load")
    return builder.compile(checkpointer=checkpointer)
