"""Public, bounded analysis contracts. No executable SQL or code fields."""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import Field, model_validator

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


class AnalysisStep(StrictModel):
    domain: BusinessDomain = "sales"
    kind: AnalysisKind
    start_date: date
    end_date_exclusive: date
    metric: Literal["amount", "orders", "quantity", "stock"] = "amount"
    order: Literal["descending", "ascending"] = "descending"
    top_n: int = Field(default=10, ge=1, le=50, strict=True)
    dimension: Literal["product", "buyer"] = "product"
    comparison_start_date: date | None = None
    comparison_end_date_exclusive: date | None = None

    @model_validator(mode="after")
    def check_intervals(self):
        days = (self.end_date_exclusive - self.start_date).days
        if not 1 <= days <= 90:
            raise ValueError("分析区间必须为 1 至 90 个完整日")
        if self.domain != "sales":
            allowed = {"summary", "product_ranking", "list", "existence"}
            if self.domain != "inventory":
                allowed |= {"trend", "buyer_ranking"}
            if self.kind not in allowed:
                raise ValueError("该业务不支持此分析方式")
            metrics = (
                {"stock", "quantity"}
                if self.domain == "inventory"
                else {"amount", "orders", "quantity"}
            )
            if self.metric not in metrics:
                raise ValueError("该业务不支持此指标")
            if self.domain == "inventory" and days != 1:
                raise ValueError("库存必须指定单一时点日期")
            if (
                self.comparison_start_date is not None
                or self.comparison_end_date_exclusive is not None
            ):
                raise ValueError("当前业务未接入期间比较")
            if self.dimension != "product" and (self.kind != "list" or self.domain == "inventory"):
                raise ValueError("请通过客户排行选择客户维度")
            if self.kind not in {"product_ranking", "buyer_ranking", "list"} and self.top_n != 10:
                raise ValueError("只有排行或明细接受展示条数")
            if self.kind not in {"product_ranking", "buyer_ranking"} and self.order != "descending":
                raise ValueError("只有排行接受排序方向")
            return self
        if self.metric not in {"amount", "orders"} or self.kind in {"list", "existence"}:
            raise ValueError("销售尚未支持此分析或指标")
        if self.kind not in {"product_ranking", "buyer_ranking"} and self.order != "descending":
            raise ValueError("只有排行接受排序方向")
        if self.kind in {"summary", "comparison", "anomalies"} and self.metric != "amount":
            raise ValueError("该分析使用订单金额口径")
        if self.kind == "comparison":
            start, end = self.comparison_start_date, self.comparison_end_date_exclusive
            if start is None or end is None or (end - start).days != days:
                raise ValueError("比较期必须明确指定，且与分析期天数相同")
            if not (end <= self.start_date or start >= self.end_date_exclusive):
                raise ValueError("分析期与比较期不能重叠")
        elif (
            self.comparison_start_date is not None or self.comparison_end_date_exclusive is not None
        ):
            raise ValueError("只有期间比较接受比较期")
        if self.kind != "comparison" and self.dimension != "product":
            raise ValueError("只有期间比较接受拆解维度")
        if self.kind not in {"buyer_ranking", "product_ranking", "comparison"} and self.top_n != 10:
            raise ValueError("该分析不接受排行条数")
        return self


class AnalysisPlan(StrictModel):
    steps: list[AnalysisStep] = Field(min_length=1, max_length=6)


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
