"""Manual scenario inputs stay local and never write purchase orders."""

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import Depends, HTTPException
from pydantic import Field

from app.analysis.analytics import validate_plan
from app.analysis.operations import validate_operations
from app.analysis.sales_query import QueryUnavailable
from app.analysis.stocking import calculate_scenario, execute_stocking
from app.schemas.analytics import AnalysisPlan, AnalysisStep, ProductVariant
from app.schemas.sales import SalesReport, StrictModel

Quantity = Annotated[
    Decimal, Field(ge=0, le=Decimal("1000000000000"), max_digits=17, decimal_places=4)
]


class StockingScenario(StrictModel):
    step: AnalysisStep
    product_code: str = Field(min_length=1, max_length=200)
    variant: ProductVariant
    in_transit_quantity: Quantity
    lead_time_days: int = Field(ge=0, le=365, strict=True)
    expected_arrival_date: date | None = None
    expected_demand: Quantity | None = None


def register_stocking_routes(router, get_report):
    Report = Annotated[SalesReport, Depends(get_report)]

    def checked(report, step):
        if step.kind != "stocking":
            raise QueryUnavailable("此接口仅支持旺季备货情景测算。")
        validate_plan(report, AnalysisPlan(steps=[step]))
        validate_operations(report)

    @router.post("/analysis/stocking/export")
    def export(step: AnalysisStep, report: Report):
        try:
            checked(report, step)
            return execute_stocking(report, step, full=True)
        except QueryUnavailable as exc:
            raise HTTPException(422, str(exc)) from None

    @router.post("/analysis/stocking/scenario")
    def scenario(body: StockingScenario, report: Report):
        try:
            checked(report, body.step)
            return calculate_scenario(report, body)
        except QueryUnavailable as exc:
            raise HTTPException(422, str(exc)) from None
