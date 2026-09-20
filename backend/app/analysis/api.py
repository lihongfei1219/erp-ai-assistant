"""Authenticated analysis endpoints over the server-selected snapshot."""

from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from app.analysis.analytics import METRICS, TITLES, available_dates, execute_analysis
from app.analysis.planning import plan_question
from app.analysis.sales_query import QueryUnavailable
from app.schemas.analytics import AnalysisPlan, AnalysisQuestion, AnalysisResponse
from app.schemas.sales import SalesReport


def register_analysis_routes(router, get_report, planner=None):
    Report = Annotated[SalesReport, Depends(get_report)]

    def get_planner():
        if planner is not None:
            return planner
        from app.ai.analytics_agent import AnalyticsPlanner
        from app.ai.model_client import load_model_settings

        return AnalyticsPlanner(load_model_settings())

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
        return {
            "available_start": start,
            "available_end_exclusive": end,
            "metrics": METRICS,
            "analyses": TITLES,
            "model_enabled": enabled,
            "max_days": 90,
            "max_steps": 6,
        }

    @router.post("/analysis/run", response_model=AnalysisResponse)
    def run(plan: AnalysisPlan, report: Report):
        try:
            return execute_analysis(report, plan)
        except QueryUnavailable as exc:
            raise HTTPException(422, str(exc)) from None

    @router.post("/analysis/ask", response_model=AnalysisResponse)
    async def ask(body: AnalysisQuestion, report: Report):
        try:
            # Establish metadata validity before accessing its business timezone.
            available_dates(report)
            plan, interpretation = await plan_question(
                body,
                report,
                get_planner(),
                datetime.now(ZoneInfo(report.operating.policy.business_timezone)).date(),
            )
            result = await run_in_threadpool(execute_analysis, report, plan)
            return result.model_copy(
                update={
                    "interpretation": interpretation,
                }
            )
        except QueryUnavailable as exc:
            raise HTTPException(422, str(exc)) from None
