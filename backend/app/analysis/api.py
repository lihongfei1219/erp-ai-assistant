"""Authenticated analysis endpoints over the server-selected snapshot."""

from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from app.analysis.analytics import (
    METRICS,
    TITLES,
    analysis_evidence,
    available_dates,
    execute_analysis,
)
from app.analysis.dialogue import DialogueChoiceUnavailable
from app.analysis.dialogue import converse as resolve_dialogue
from app.analysis.planning import resolve_question
from app.analysis.sales_query import QueryUnavailable
from app.capabilities.view import capability_view
from app.orchestration.store import GraphConflict
from app.schemas.analytics import (
    AnalysisEvidenceRequest,
    AnalysisPlan,
    AnalysisQuestion,
    AnalysisResponse,
)
from app.schemas.sales import SalesReport
from app.semantic.context import decode_context, encode_context
from app.semantic.dialogue_schemas import ConversationRequest, DialogueTurn
from app.semantic.provider import SemanticPlanner, SemanticProviderUnavailable
from app.semantic.schemas import SemanticInterpretation


def register_analysis_routes(router, get_report, planner=None, *, context_secret=""):
    Report = Annotated[SalesReport, Depends(get_report)]

    def get_planner():
        if planner is not None:
            return planner
        from app.ai.model_client import load_model_settings

        return SemanticPlanner(load_model_settings())

    async def resolve(body, report):
        available_dates(report)
        conversation = decode_context(body.conversation_token, report, context_secret)
        return await resolve_question(
            body,
            report,
            get_planner(),
            datetime.now(ZoneInfo(report.operating.policy.business_timezone)).date(),
            conversation=conversation,
        )

    def conversation_token(resolution, report):
        if resolution.semantic is None:
            return None
        return encode_context(resolution.semantic.context, report, context_secret)

    @router.get("/analysis/catalog")
    def catalog(report: Report):
        try:
            start, end = available_dates(report)
        except QueryUnavailable as exc:
            raise HTTPException(422, str(exc)) from None
        # Configuration state only. No key, model endpoint or source identity is exposed.
        try:
            enabled = get_planner().enabled
        except QueryUnavailable:
            enabled = False
        view = capability_view(report)
        return {
            "available_start": start,
            "available_end_exclusive": end,
            "metrics": METRICS,
            "analyses": TITLES,
            "model_enabled": enabled,
            "max_days": view["max_days"],
            "max_steps": view["max_steps"],
            "max_items": view["max_items"],
            "capability_version": view["version"],
            "object_filters": view["object_filters"],
            "semantic_domains": view["domains"],
        }

    @router.post("/analysis/interpret", response_model=SemanticInterpretation)
    async def interpret(body: AnalysisQuestion, report: Report):
        try:
            resolution = await resolve(body, report)
            if resolution.semantic is None:
                raise HTTPException(503, "当前注入的旧计划器不支持独立语义接口。")
            return SemanticInterpretation(
                status=resolution.semantic.status,
                message=resolution.semantic.message,
                semantic=resolution.semantic.context,
                conversation_token=conversation_token(resolution, report),
            )
        except SemanticProviderUnavailable as exc:
            raise HTTPException(503, str(exc)) from None
        except QueryUnavailable as exc:
            raise HTTPException(422, str(exc)) from None

    @router.post("/analysis/run", response_model=AnalysisResponse)
    def run(plan: AnalysisPlan, report: Report):
        try:
            return execute_analysis(report, plan)
        except QueryUnavailable as exc:
            raise HTTPException(422, str(exc)) from None

    @router.post("/analysis/evidence")
    def evidence(body: AnalysisEvidenceRequest, report: Report):
        try:
            return {"item": analysis_evidence(report, body)}
        except QueryUnavailable as exc:
            raise HTTPException(422, str(exc)) from None

    @router.post("/analysis/ask", response_model=AnalysisResponse)
    async def ask(body: AnalysisQuestion, report: Report):
        try:
            resolution = await resolve(body, report)
            token = conversation_token(resolution, report)
            if resolution.plan is None:
                raise HTTPException(
                    422,
                    {
                        "code": resolution.semantic.status,
                        "message": resolution.semantic.message,
                        "conversation_token": token,
                    },
                )
            result = await run_in_threadpool(execute_analysis, report, resolution.plan)
            return result.model_copy(
                update={
                    "interpretation": resolution.interpretation,
                    "conversation_token": token,
                }
            )
        except SemanticProviderUnavailable as exc:
            raise HTTPException(503, str(exc)) from None
        except QueryUnavailable as exc:
            raise HTTPException(422, str(exc)) from None

    @router.post("/analysis/converse", response_model=DialogueTurn)
    async def converse(body: ConversationRequest, report: Report):
        try:
            try:
                previous = decode_context(body.conversation_token, report, context_secret)
            except QueryUnavailable as exc:
                raise HTTPException(409, {"code": "context_expired", "message": str(exc)}) from None
            outcome = await resolve_dialogue(
                body,
                report,
                get_planner(),
                datetime.now(ZoneInfo(report.operating.policy.business_timezone)).date(),
                previous=previous,
            )
            token = encode_context(outcome.context, report, context_secret)
            return outcome.turn.model_copy(update={"conversation_token": token})
        except DialogueChoiceUnavailable as exc:
            raise HTTPException(409, {"code": "choice_expired", "message": str(exc)}) from None
        except GraphConflict as exc:
            raise HTTPException(409, {"code": exc.code, "message": str(exc)}) from None
        except SemanticProviderUnavailable as exc:
            raise HTTPException(503, str(exc)) from None
        except QueryUnavailable as exc:
            raise HTTPException(422, str(exc)) from None
