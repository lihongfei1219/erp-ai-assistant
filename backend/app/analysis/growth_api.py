"""Local evidence and explicit drill-down for validated growth plans."""

from typing import Annotated, Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import Field

from app.analysis.analytics import validate_plan
from app.analysis.growth import selected_lines, validate_growth
from app.analysis.object_filters import binding_key
from app.analysis.operations import validate_operations
from app.analysis.sales_query import QueryUnavailable
from app.schemas.analytics import AnalysisPlan, AnalysisStep
from app.schemas.sales import SalesReport, StrictModel
from app.semantic.compiler import context_from_plan
from app.semantic.context import encode_context


class GrowthEvidenceRequest(StrictModel):
    step: AnalysisStep
    period: Literal["current", "previous", "year_over_year"]
    product_code: str = Field(min_length=1, max_length=200)
    buyer_code: str | None = Field(default=None, max_length=200)
    specification: str = Field(min_length=1, max_length=200)
    manufacturer: str = Field(min_length=1, max_length=200)
    unit: str = Field(min_length=1, max_length=200)
    offset: int = Field(default=0, ge=0, le=200000)
    limit: int = Field(default=50, ge=1, le=200)


def manual_context(plan, report, secret):
    """Selected codes remain in server bindings, never become cloud semantic filter values."""
    context = context_from_plan(plan)
    bindings = []
    intents = []
    for intent, step in zip(context.intents, plan.steps, strict=True):
        filters = []
        for index, (condition, selected) in enumerate(
            zip(intent.filters, step.filters, strict=True)
        ):
            label = "商品" if condition.field == "product" else "客户"
            condition = condition.model_copy(
                update={
                    "id": "filter_" + uuid4().hex[:12],
                    "value": f"已选择的{label}{index + 1}",
                }
            )
            filters.append(condition)
            bindings.append(
                {
                    **binding_key(intent, condition),
                    "code": selected.code,
                    **({"variant": selected.variant.model_dump()} if selected.variant else {}),
                }
            )
        intents.append(intent.model_copy(update={"filters": filters}))
    context = context.model_copy(update={"intents": intents, "entity_bindings": bindings})
    return encode_context(context, report, secret)


def evidence(report, body):
    if body.step.kind not in {"growth", "price", "margin"}:
        raise QueryUnavailable("此接口只支持品种变化证据")
    validate_plan(report, AnalysisPlan(steps=[body.step]))
    validate_operations(report)
    if body.step.kind == "margin":
        from app.analysis.costs import validate_margin

        periods = validate_margin(report, body.step)
    else:
        periods = validate_growth(report, body.step)
    if body.period not in periods:
        raise QueryUnavailable("该比较期不属于当前分析")
    rows = []
    for doc, line in selected_lines(report, body.step, *periods[body.period]):
        if (
            (line.product_code, line.specification, line.manufacturer, line.unit)
            != (body.product_code, body.specification, body.manufacturer, body.unit)
            or body.buyer_code is not None
            and doc.buyer_code != body.buyer_code
        ):
            continue
        rows.append(
            dict(
                source=f"shipping:{doc.document_id}:{line.line_id}",
                document_number=doc.document_number,
                occurred_at=doc.occurred_at.isoformat(),
                buyer_code=doc.buyer_code,
                buyer_name=doc.buyer_name,
                **line.model_dump(mode="json"),
            )
        )
    rows.sort(key=lambda row: (row["occurred_at"], row["source"]))
    return dict(
        total=len(rows),
        offset=body.offset,
        limit=body.limit,
        items=rows[body.offset : body.offset + body.limit],
    )


def register_growth_routes(router, get_report):
    from fastapi import Depends

    Report = Annotated[SalesReport, Depends(get_report)]

    @router.post("/analysis/margin/export")
    @router.post("/analysis/price/export")
    @router.post("/analysis/growth/export")
    def growth_export(step: AnalysisStep, report: Report):
        from app.analysis.growth import execute_growth

        try:
            if step.kind not in {"growth", "price", "margin"}:
                raise QueryUnavailable("只支持品种变化导出")
            validate_plan(report, AnalysisPlan(steps=[step]))
            validate_operations(report)
            if step.kind == "margin":
                from app.analysis.margin import execute_margin

                return execute_margin(report, step, full=True)
            if step.kind == "price":
                from app.analysis.price import execute_price

                return execute_price(report, step, full=True)
            return execute_growth(report, step, full=True)
        except QueryUnavailable as exc:
            raise HTTPException(422, str(exc)) from None

    @router.post("/analysis/margin/evidence")
    @router.post("/analysis/price/evidence")
    @router.post("/analysis/growth/evidence")
    def growth_evidence(body: GrowthEvidenceRequest, report: Report):
        try:
            return evidence(report, body)
        except QueryUnavailable as exc:
            raise HTTPException(422, str(exc)) from None
