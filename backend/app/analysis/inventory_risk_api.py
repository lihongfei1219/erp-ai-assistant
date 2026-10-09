"""Validated stock-risk exports and underlying local batch records."""

from typing import Annotated

from fastapi import Depends, HTTPException
from pydantic import Field

from app.analysis.analytics import validate_plan
from app.analysis.inventory_risk import execute_inventory_risk
from app.analysis.operations import validate_operations
from app.analysis.sales_query import QueryUnavailable
from app.schemas.analytics import AnalysisPlan, AnalysisStep
from app.schemas.sales import SalesReport, StrictModel


class InventoryEvidenceRequest(StrictModel):
    step: AnalysisStep
    record_ids: list[int] = Field(min_length=1, max_length=200)


def register_inventory_risk_routes(router, get_report):
    Report = Annotated[SalesReport, Depends(get_report)]

    def checked(report, step):
        if step.kind != "inventory_risk":
            raise QueryUnavailable("此接口仅支持库存积压分析")
        validate_plan(report, AnalysisPlan(steps=[step]))
        validate_operations(report)
        return execute_inventory_risk(report, step, full=True)

    @router.post("/analysis/inventory-risk/export")
    def export(step: AnalysisStep, report: Report):
        try:
            return checked(report, step)
        except QueryUnavailable as exc:
            raise HTTPException(422, str(exc)) from None

    @router.post("/analysis/inventory-risk/evidence")
    def evidence(body: InventoryEvidenceRequest, report: Report):
        try:
            result = checked(report, body.step)
            allowed = {i for row in result.rows for i in row["record_ids"]}
            if not set(body.record_ids) <= allowed:
                raise QueryUnavailable("批次记录不在当前分析与授权范围内")
            return dict(
                as_of=report.operations.inventory.as_of,
                items=[
                    row.model_dump(mode="json")
                    for row in report.operations.inventory.records
                    if row.record_id in body.record_ids
                ],
            )
        except QueryUnavailable as exc:
            raise HTTPException(422, str(exc)) from None
