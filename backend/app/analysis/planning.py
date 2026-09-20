"""Channel-independent question planning and deterministic interpretation text."""

import re
from datetime import date, timedelta

from app.ai.analytics_bounds import validate_question_plan
from app.ai.analytics_language import compact_question
from app.analysis.analytics import TITLES, available_dates, validate_plan
from app.analysis.sales_query import QueryUnavailable
from app.schemas.analytics import AnalysisPlan, AnalysisQuestion
from app.schemas.sales import SalesReport


def describe_interpretation(body: AnalysisQuestion, plan: AnalysisPlan) -> list[str]:
    descriptions = []
    for step in plan.steps:
        period = f"{step.start_date} 至 {step.end_date_exclusive - timedelta(days=1)}（含首尾两天）"
        metric = "订单数" if step.metric == "orders" else "销售金额（有效订单口径）"
        detail = f"{TITLES[step.kind]}：{period}；按{metric}统计"
        if step.kind in {"product_ranking", "buyer_ranking"}:
            detail += f"，从高到低展示前 {step.top_n} 名"
        if step.kind == "comparison":
            detail += (
                f"；比较期 {step.comparison_start_date} 至 "
                f"{step.comparison_end_date_exclusive - timedelta(days=1)}"
            )
        descriptions.append(detail + "。")
    if "药品" in compact_question(body.question):
        descriptions.append(
            "“药品”按当前商品范围理解：本次未按药品类别筛选，可能包含器械、保健品等其他商品。"
        )
    return descriptions


async def plan_question(body: AnalysisQuestion, report: SalesReport, planner, today: date):
    start, end = available_dates(report)
    if not report.metadata.scope.all_buyers and re.search(
        r"全平台|平台整体", compact_question(body.question)
    ):
        raise QueryUnavailable("当前快照只包含授权企业，不能提供全平台分析。")
    decision = await planner.plan(
        body,
        {
            "today": today.isoformat(),
            "available_start": start.isoformat(),
            "available_end_exclusive": end.isoformat(),
            "timezone": report.operating.policy.business_timezone,
        },
    )
    if decision.action == "clarify":
        raise QueryUnavailable(decision.explanation or "请明确日期、分析目标及支持的指标。")
    validate_question_plan(body, decision.plan, today)
    validate_plan(report, decision.plan)
    return decision.plan, describe_interpretation(body, decision.plan)
