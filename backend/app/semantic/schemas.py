"""Versioned semantic contracts shared by cloud providers and business adapters."""

from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from app.capabilities.registry import MAX_ITEMS, MAX_STEPS
from app.schemas.sales import StrictModel
from app.semantic.catalog import load_catalog

Domain = Literal["sales", "returns", "shipping", "inventory", "unknown"]
Operation = Literal[
    "summary", "trend", "ranking", "comparison", "anomalies", "list", "existence", "unknown"
]
Metric = Literal[
    "amount",
    "orders",
    "quantity",
    "return_rate",
    "stock",
    "turnover",
    "payment",
    "profit",
    "unknown",
]


class SemanticFilter(StrictModel):
    id: str | None = Field(default=None, min_length=1, max_length=80)
    field: Literal[
        "product", "buyer", "category", "warehouse", "supplier", "region", "status", "other"
    ]
    operator: Literal["include", "exclude", "equal"] = "equal"
    value: str = Field(min_length=1, max_length=100)


class SemanticIntent(StrictModel):
    id: str | None = Field(default=None, min_length=1, max_length=80)
    # Null means unspecified, allowing a follow-up to update only explicit slots.
    domain: Domain | None = None
    operation: Operation | None = None
    # Understanding a dimension does not imply that its executor is available.
    target: str | None = Field(default=None, min_length=1, max_length=80)
    metric: Metric | None = None
    time: str | None = Field(default=None, min_length=1, max_length=100)
    comparison_time: str | None = Field(default=None, min_length=1, max_length=100)
    limit: int | None = Field(default=None, ge=1, le=MAX_ITEMS, strict=True)
    order: Literal["descending", "ascending"] | None = None
    scope: Literal["authorized", "all_buyers"] | None = None
    generic_product_scope: bool | None = None
    filters: list[SemanticFilter] = Field(default_factory=list, max_length=10)
    unresolved: list[str] = Field(default_factory=list, max_length=10)


class SemanticCandidate(StrictModel):
    label: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=100)


class SemanticIssue(StrictModel):
    id: str | None = Field(default=None, min_length=1, max_length=80)
    intent_id: str | None = Field(default=None, min_length=1, max_length=80)
    field: str = Field(min_length=1, max_length=80)
    kind: Literal["missing", "ambiguous", "dependency"] = "ambiguous"
    question: str = Field(min_length=1, max_length=500)
    choices: list[SemanticCandidate] = Field(default_factory=list, max_length=3)


class SemanticEdit(StrictModel):
    intent_id: str = Field(min_length=1, max_length=80)
    operation: Literal[
        "set", "unset", "remove_intent", "add_filter", "replace_filter", "remove_filter"
    ]
    field: str | None = Field(default=None, min_length=1, max_length=80)
    value: str | int | bool | None = None
    constraint_id: str | None = Field(default=None, min_length=1, max_length=80)
    filter: SemanticFilter | None = None


class SemanticRequest(StrictModel):
    schema_version: Literal["1"] = "1"
    mode: Literal["new", "followup", "answer"] = "new"
    intents: list[SemanticIntent] = Field(default_factory=list, max_length=MAX_STEPS)
    add_intents: list[SemanticIntent] = Field(default_factory=list, max_length=MAX_STEPS)
    unresolved: list[str] = Field(default_factory=list, max_length=10)
    resolved_conditions: list[str] = Field(default_factory=list, max_length=10)
    issues: list[SemanticIssue] = Field(default_factory=list, max_length=10)
    resolved_issue_ids: list[str] = Field(default_factory=list, max_length=10)
    edits: list[SemanticEdit] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_goal_or_edit(self):
        if not self.intents and not self.edits and not self.add_intents:
            raise ValueError("请保留至少一个分析目标或明确的修改。")
        return self


class GraphReference(StrictModel):
    channel: Literal["web", "feishu"]
    thread_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    revision: int = Field(ge=1)
    version: Literal["erp.graph.v1"] = "erp.graph.v1"


class SemanticContext(StrictModel):
    schema_version: Literal["1"] = "1"
    catalog_version: str = Field(default_factory=lambda: load_catalog()["version"])
    intents: list[SemanticIntent] = Field(min_length=1, max_length=MAX_STEPS)
    pending: list[str] = Field(default_factory=list, max_length=10)
    unresolved: list[str] = Field(default_factory=list, max_length=10)
    product_scope_note: bool = False
    requires_restatement: bool = False
    issues: list[SemanticIssue] = Field(default_factory=list, max_length=80)
    field_sources: dict[str, dict[str, Literal["explicit", "inherited", "default"]]] = Field(
        default_factory=dict
    )
    clarification_key: str | None = None
    clarification_attempts: int = Field(default=0, ge=0)
    dialogue_choices: list[dict] = Field(default_factory=list, max_length=20)
    dialogue_id: str | None = None
    # A rejected target patch is context for the next model turn, never an execution plan.
    pending_request: SemanticRequest | None = None
    runtime_ref: GraphReference | None = None
    # Server bindings are stripped from cloud inputs and bound to the signed snapshot context.
    entity_bindings: list[dict] = Field(default_factory=list, max_length=60)
    entity_issue: dict | None = None


class SemanticInput(StrictModel):
    question: str = Field(min_length=1, max_length=1000)
    today: date
    timezone: str = "Asia/Shanghai"
    previous: SemanticContext | None = None
    capabilities: dict = Field(default_factory=dict)


class SemanticInterpretation(StrictModel):
    """Public, execution-free interpretation response; no provider details."""

    status: Literal["ready", "clarify", "unsupported", "data_unavailable"]
    message: str = ""
    semantic: SemanticContext
    conversation_token: str | None = None
