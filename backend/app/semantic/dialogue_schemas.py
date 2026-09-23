"""Channel-neutral guided conversation contracts; actions carry no executable code."""

from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from app.schemas.analytics import AnalysisResponse
from app.schemas.sales import StrictModel


class DateRange(StrictModel):
    start: date
    end_exclusive: date

    @model_validator(mode="after")
    def bounded(self):
        if not 1 <= (self.end_exclusive - self.start).days <= 90:
            raise ValueError("请选择 1 至 90 天的日期区间")
        return self


class ConversationRequest(StrictModel):
    request_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,80}$")
    question: str | None = Field(default=None, min_length=1, max_length=1000)
    conversation_token: str | None = Field(default=None, min_length=1, max_length=60000)
    choice_id: str | None = Field(default=None, min_length=1, max_length=100)
    date_range: DateRange | None = None

    @model_validator(mode="after")
    def one_input(self):
        if bool(self.question) == bool(self.choice_id):
            raise ValueError("请提供问题或选择一个当前建议")
        if self.question is not None and not self.question.strip():
            raise ValueError("请填写问题")
        if self.date_range is not None and self.choice_id is None:
            raise ValueError("日期选择需要对应当前建议")
        return self


class ChoiceAction(StrictModel):
    kind: str
    intent_id: str | None = None
    field: str | None = None


class DialogueChoice(StrictModel):
    id: str
    label: str
    action: ChoiceAction


class DraftIntent(StrictModel):
    id: str
    label: str
    fields: dict[str, str | int | bool | None]
    constraints: list[dict[str, str]] = Field(default_factory=list)
    field_sources: dict[str, Literal["explicit", "inherited", "default"]] = Field(
        default_factory=dict
    )


class DialogueDraft(StrictModel):
    intents: list[DraftIntent]


class Clarification(StrictModel):
    id: str
    intent_id: str | None = None
    field: str
    kind: str
    question: str


class AvailableDates(StrictModel):
    start: date
    end_exclusive: date


class DialogueTurn(StrictModel):
    status: Literal["result", "needs_input", "capability_gap", "data_gap"]
    understood_summary: str
    draft: DialogueDraft
    clarification: Clarification | None = None
    choices: list[DialogueChoice] = Field(default_factory=list)
    applied_defaults: list[str] = Field(default_factory=list)
    allow_free_text: Literal[True] = True
    conversation_token: str | None = None
    result: AnalysisResponse | None = None
    repeated_clarification: bool = False
    available_dates: AvailableDates
