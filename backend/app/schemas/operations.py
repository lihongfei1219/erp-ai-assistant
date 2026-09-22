"""Reconciled, local-only facts for returns, sales dispatches and point-in-time stock."""

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Nonnegative = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
Code = Annotated[str, Field(min_length=1, pattern=r"\S")]


class FactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def quantities(records):
    totals = defaultdict(Decimal)
    for row in records:
        totals[row.unit] += row.quantity
    return dict(totals)


class DocumentLine(FactModel):
    line_id: int
    product_code: Code
    product_name: str | None = None
    unit: Code
    quantity: Nonnegative
    amount: Nonnegative


class BusinessDocument(FactModel):
    document_id: int
    document_number: Code
    buyer_code: Code
    buyer_name: str | None = None
    original_order_id: int
    original_order_number: Code
    occurred_at: datetime
    status: Code
    amount: Nonnegative
    lines: list[DocumentLine] = Field(min_length=1)

    @model_validator(mode="after")
    def reconciled(self):
        if self.occurred_at.tzinfo is None:
            raise ValueError("业务事件必须带时区")
        if len({row.line_id for row in self.lines}) != len(self.lines):
            raise ValueError("业务单据明细键重复")
        if sum((row.amount for row in self.lines), Decimal(0)) != self.amount:
            raise ValueError("业务单据主明细金额不一致")
        return self


class DocumentFacts(FactModel):
    start: date
    end_exclusive: date
    time_basis: Code
    source_tables: list[Code] = Field(min_length=1)
    included_statuses: list[Code] = Field(min_length=1)
    excluded_document_count: int = Field(ge=0)
    documents: list[BusinessDocument]
    control_document_count: int = Field(ge=0)
    control_line_count: int = Field(ge=0)
    control_amount: Nonnegative
    control_quantities: dict[Code, Nonnegative]

    @model_validator(mode="after")
    def reconciled(self):
        lines = [line for doc in self.documents for line in doc.lines]
        if (
            not 0 < (self.end_exclusive - self.start).days <= 366
            or self.control_document_count != len(self.documents)
            or len({doc.document_id for doc in self.documents}) != len(self.documents)
            or len({doc.document_number for doc in self.documents}) != len(self.documents)
            or self.control_line_count != len(lines)
            or self.control_amount != sum((doc.amount for doc in self.documents), Decimal(0))
            or self.control_quantities != quantities(lines)
            or any(doc.status not in self.included_statuses for doc in self.documents)
        ):
            raise ValueError("业务事实与控制总数不一致")
        return self


class StockRecord(FactModel):
    record_id: int
    product_code: Code
    product_name: str | None = None
    unit: Code
    batch_code: str
    quantity: Nonnegative


class InventoryFacts(FactModel):
    as_of: datetime
    source_tables: list[Code] = Field(default_factory=lambda: ["SPPHGLBH", "HGJYSPDAH"])
    time_basis: str = "备份截至时点商品批次库存余额"
    records: list[StockRecord]
    control_record_count: int = Field(ge=0)
    control_quantities: dict[Code, Nonnegative]

    @model_validator(mode="after")
    def reconciled(self):
        if (
            self.as_of.tzinfo is None
            or self.control_record_count != len(self.records)
            or len({row.record_id for row in self.records}) != len(self.records)
            or self.control_quantities != quantities(self.records)
        ):
            raise ValueError("库存事实与控制总数不一致")
        return self


class OperationsSnapshot(FactModel):
    policy_version: Literal["qy.operations.v1"] = "qy.operations.v1"
    source_as_of: datetime
    all_buyers: bool
    buyer_codes: tuple[str, ...] = ()
    returns: DocumentFacts | None = None
    shipping: DocumentFacts | None = None
    inventory: InventoryFacts | None = None

    @model_validator(mode="after")
    def consistent(self):
        if self.source_as_of.tzinfo is None or self.all_buyers == bool(self.buyer_codes):
            raise ValueError("业务事实范围或水位无效")
        if self.inventory and (not self.all_buyers or self.inventory.as_of != self.source_as_of):
            raise ValueError("库存只能用于全平台授权范围且时点必须与备份一致")
        for facts in (self.returns, self.shipping):
            if facts and any(
                doc.occurred_at > self.source_as_of
                or (not self.all_buyers and doc.buyer_code not in self.buyer_codes)
                for doc in facts.documents
            ):
                raise ValueError("业务记录超出授权范围或来源水位")
        return self
