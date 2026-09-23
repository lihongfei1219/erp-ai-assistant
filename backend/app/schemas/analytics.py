"""Public, bounded analysis contracts. No executable SQL or code fields."""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from app.capabilities.registry import DEFAULT_ITEMS, MAX_ITEMS, MAX_STEPS, validate_step
from app.schemas.sales import DataScope, StrictModel

AnalysisKind = Literal[
    "summary",
    "trend",
    "buyer_ranking",
    "product_ranking",
    "comparison",
    "anomalies",
    "list",
    "existence",
]
BusinessDomain = Literal["sales", "returns", "shipping", "inventory"]


class ObjectFilter(StrictModel):
    field: Literal["product", "buyer"]
    operator: Literal["include", "exclude", "equal"] = "equal"
    code: str = Field(min_length=1, max_length=200, pattern=r"\S")


class AnalysisStep(StrictModel):
    domain: BusinessDomain = "sales"
    kind: AnalysisKind
    start_date: date
    end_date_exclusive: date
    metric: Literal["amount", "orders", "quantity", "stock"] = "amount"
    order: Literal["descending", "ascending"] = "descending"
    top_n: int = Field(default=DEFAULT_ITEMS, ge=1, le=MAX_ITEMS, strict=True)
    dimension: Literal["product", "buyer"] = "product"
    comparison_start_date: date | None = None
    comparison_end_date_exclusive: date | None = None
    filters: list[ObjectFilter] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def check_intervals(self):
        return validate_step(self)


class AnalysisPlan(StrictModel):
    steps: list[AnalysisStep] = Field(min_length=1, max_length=MAX_STEPS)


class AnalysisEvidenceRequest(StrictModel):
    step: AnalysisStep
    order_id: int = Field(strict=True)
    product_code: str | None = Field(default=None, max_length=200)
    buyer_code: str | None = Field(default=None, max_length=200)


class AnalysisQuestion(StrictModel):
    question: str = Field(min_length=1, max_length=1000)
    previous_plan: AnalysisPlan | None = None
    conversation_token: str | None = Field(default=None, min_length=1, max_length=60000)

    @model_validator(mode="after")
    def not_blank(self):
        if not self.question.strip():
            raise ValueError("请填写分析问题")
        return self


class PlanDecision(StrictModel):
    action: Literal["run", "clarify"]
    plan: AnalysisPlan | None = None
    explanation: str = Field(default="", max_length=500)
    unsupported_conditions: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def consistent(self):
        if self.action == "run" and (self.plan is None or self.unsupported_conditions):
            raise ValueError("执行计划必须完整，且不能忽略不支持的条件")
        if self.action == "clarify" and self.plan is not None:
            raise ValueError("澄清时不能同时执行计划")
        return self


class AnalysisResult(StrictModel):
    domain: BusinessDomain = "sales"
    kind: AnalysisKind
    title: str
    columns: dict[str, str]
    rows: list[dict[str, Any]]
    totals: dict[str, Any] = Field(default_factory=dict)
    findings: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    chart: dict[str, Any] | None = None


class AnalysisProvenance(StrictModel):
    source_as_of: datetime
    snapshot_generated_at: datetime
    source_kind: str
    scope: DataScope
    policy_id: str
    policy_fingerprint: str
    metric_version: str
    currency: str
    business_timezone: str
    included_statuses: tuple[str, ...]


class AnalysisResponse(StrictModel):
    run_id: str
    generated_at: datetime
    plan: AnalysisPlan
    provenance: AnalysisProvenance
    results: list[AnalysisResult]
    warnings: list[str]
    interpretation: list[str] = Field(default_factory=list)
    conversation_token: str | None = None
