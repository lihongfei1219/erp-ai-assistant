"""Channel-independent question planning and deterministic interpretation text."""

import re
from dataclasses import dataclass
from datetime import date, timedelta

from app.ai.analytics_bounds import validate_question_plan
from app.ai.analytics_language import compact_question
from app.analysis.analytics import TITLES, available_dates, validate_plan
from app.analysis.operations import DOMAIN_LABELS, TARGETS, domain_dates, executable_domains
from app.analysis.operations import METRICS as DOMAIN_METRICS
from app.analysis.sales_query import QueryUnavailable
from app.schemas.analytics import AnalysisPlan, AnalysisQuestion
from app.schemas.sales import SalesReport
from app.semantic.catalog import load_catalog
from app.semantic.compiler import Compilation, compile_request, context_from_plan
from app.semantic.schemas import SemanticContext, SemanticInput


@dataclass(frozen=True)
class PlanningResult:
    plan: AnalysisPlan | None
    interpretation: list[str]
    semantic: Compilation | None = None


def describe_interpretation(
    body: AnalysisQuestion, plan: AnalysisPlan, *, product_scope_note: bool | None = None
) -> list[str]:
    descriptions = []
    for step in plan.steps:
        period = f"{step.start_date} 至 {step.end_date_exclusive - timedelta(days=1)}（含首尾两天）"
        if step.domain != "sales":
            label = DOMAIN_LABELS[step.domain]
            metric = {
                "amount": f"{label}单据金额",
                "orders": f"{label}单据数",
                "quantity": "数量（按单位分别统计）",
                "stock": "库存数量（按单位分别统计）",
            }[step.metric]
            operation = {
                "summary": "概览",
                "trend": "趋势",
                "product_ranking": "商品排行",
                "buyer_ranking": "客户排行",
                "list": "明细",
                "existence": "有无记录",
            }[step.kind]
            detail = f"{label}{operation}：{period}；按{metric}统计"
            if step.kind.endswith("ranking"):
                direction = "从高到低" if step.order == "descending" else "从低到高"
                detail += f"；{direction}，每组前 {step.top_n} 名"
            if step.domain == "inventory":
                detail += "；该日期仅选择备份库存时点，实际截至时间见结果，不是日末或实时库存"
            descriptions.append(detail + "。")
            continue
        metric = "订单数" if step.metric == "orders" else "销售金额（有效订单口径）"
        detail = f"{TITLES[step.kind]}：{period}；按{metric}统计"
        if step.kind in {"product_ranking", "buyer_ranking"}:
            direction = "从高到低" if step.order == "descending" else "从低到高"
            detail += f"，{direction}展示前 {step.top_n} 名"
        if step.kind == "comparison":
            detail += (
                f"；比较期 {step.comparison_start_date} 至 "
                f"{step.comparison_end_date_exclusive - timedelta(days=1)}"
            )
        descriptions.append(detail + "。")
    if (
        product_scope_note
        if product_scope_note is not None
        else "药品" in compact_question(body.question)
    ):
        descriptions.append(
            "“药品”按当前商品范围理解：本次未按药品类别筛选，可能包含器械、保健品等其他商品。"
        )
    return descriptions


async def resolve_question(
    body: AnalysisQuestion,
    report: SalesReport,
    planner,
    today: date,
    *,
    conversation: SemanticContext | None = None,
) -> PlanningResult:
    start, end = available_dates(report)
    if hasattr(planner, "interpret"):
        previous = conversation
        if previous and previous.catalog_version != load_catalog()["version"]:
            previous = None
        if previous is None and body.previous_plan is not None:
            previous = context_from_plan(body.previous_plan)
        request = SemanticInput(
            question=body.question,
            today=today,
            timezone=report.operating.policy.business_timezone,
            previous=previous.model_copy(update={"dialogue_choices": [], "dialogue_id": None})
            if previous
            else None,
            capabilities={
                "available_start": start.isoformat(),
                "available_end_exclusive": end.isoformat(),
                "all_buyers_authorized": report.metadata.scope.all_buyers,
                "executable_domains": executable_domains(report),
                "domain_capabilities": {
                    domain: {
                        "metrics": sorted(DOMAIN_METRICS[domain]),
                        "targets": sorted(TARGETS[domain]),
                        "available_start": domain_dates(report, domain)[0].isoformat(),
                        "available_end_exclusive": domain_dates(report, domain)[1].isoformat(),
                        **(
                            {"snapshot_as_of": report.operations.inventory.as_of.isoformat()}
                            if domain == "inventory"
                            else {}
                        ),
                    }
                    for domain in executable_domains(report)
                    if domain != "sales"
                },
                "metrics": ["amount", "orders"],
                "executable_targets": ["product", "buyer"],
                "analyses": list(TITLES),
                "object_filters": False,
            },
        )
        semantic = await planner.interpret(request)
        compiled = compile_request(
            semantic,
            question=body.question,
            today=today,
            previous=previous,
            domains=executable_domains(report),
        )
        return finish_compilation(compiled, body, report)
    return await _legacy_resolution(body, report, planner, today, start, end)


def finish_compilation(compiled, body, report) -> PlanningResult:
    """Apply the same scope/data gates to model decisions and signed user choices."""
    if not report.metadata.scope.all_buyers and any(
        i.scope == "all_buyers" for i in compiled.context.intents
    ):
        return PlanningResult(
            None,
            [],
            Compilation(
                "unsupported",
                compiled.context,
                message="当前快照只包含授权企业，不能提供全平台分析。",
            ),
        )
    if compiled.plan is None:
        return PlanningResult(None, [], compiled)
    try:
        validate_plan(report, compiled.plan)
    except QueryUnavailable as exc:
        return PlanningResult(
            None,
            [],
            Compilation(
                "data_unavailable",
                compiled.context,
                message=str(exc),
            ),
        )
    interpretation = describe_interpretation(
        body, compiled.plan, product_scope_note=compiled.context.product_scope_note
    )
    for index, intent in enumerate(compiled.context.intents):
        if intent.domain == "sales" and intent.operation == "summary" and intent.metric == "orders":
            interpretation[index] = interpretation[index].replace(
                "按销售金额（有效订单口径）统计", "查看有效订单数，概览同时展示金额等指标"
            )
    if compiled.context.product_scope_note and not any(
        "未按药品类别筛选" in s for s in interpretation
    ):
        interpretation.append(
            "沿用当前商品范围，未按药品类别筛选，可能包含器械、保健品等其他商品。"
        )
    return PlanningResult(compiled.plan, interpretation, compiled)


async def _legacy_resolution(body, report, planner, today, start, end):
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
    return PlanningResult(decision.plan, describe_interpretation(body, decision.plan))


async def plan_question(body: AnalysisQuestion, report: SalesReport, planner, today: date):
    """Compatibility for existing integrations that only accept executable plans."""
    result = await resolve_question(body, report, planner, today)
    if result.plan is None:
        raise QueryUnavailable(result.semantic.message)
    return result.plan, result.interpretation
