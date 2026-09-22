from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.operations import OperationsSnapshot

METRIC_VERSION = "qy.sales_orders.v1"
REPORT_SCHEMA_VERSION = "1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AnalysisWindow(StrictModel):
    start: date
    end: date

    @model_validator(mode="after")
    def check_window(self):
        if not 0 < (self.end - self.start).days <= 366:
            raise ValueError("日期范围使用 [start, end)，长度必须为 1—366 天")
        return self


class DataScope(StrictModel):
    all_buyers: bool = False
    buyer_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def check_scope(self):
        if self.all_buyers == bool(self.buyer_codes):
            raise ValueError("必须明确选择全部企业，或指定企业编码")
        if len(self.buyer_codes) > 100:
            raise ValueError("单次最多指定 100 个企业编码")
        if any(
            not code.strip() or code != code.strip() or len(code) > 100 for code in self.buyer_codes
        ):
            raise ValueError("企业编码不能为空、含首尾空白或超过 100 字符")
        if len(set(self.buyer_codes)) != len(self.buyer_codes):
            raise ValueError("企业编码不能重复")
        return self


class BusinessRules(StrictModel):
    policy_id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=80)
    included_statuses: tuple[str, ...]
    known_statuses: tuple[str, ...]
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    business_timezone: str
    amount_basis: Literal["erp_order_total"] = "erp_order_total"
    refund_policy: Literal["not_deducted"] = "not_deducted"
    provisional: bool = True
    assumption_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_policy(self):
        for values in (self.included_statuses, self.known_statuses):
            if not values or len(set(values)) != len(values):
                raise ValueError("状态列表不能为空或包含重复项")
            if any(not value.strip() or value != value.strip() for value in values):
                raise ValueError("状态不能含空值或首尾空白")
        if not set(self.included_statuses).issubset(self.known_statuses):
            raise ValueError("纳入状态必须在已知状态中")
        try:
            ZoneInfo(self.business_timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("业务时区无效") from exc
        return self


class Summary(StrictModel):
    order_amount: Decimal
    order_count: int
    buyer_count: int
    average_order_amount: Decimal | None
    line_count: int
    product_count: int
    completed_status_order_count: int
    completed_status_order_amount: Decimal


class DailyPoint(StrictModel):
    day: date
    order_amount: Decimal
    order_count: int
    buyer_count: int


class Breakdown(StrictModel):
    code: str
    name: str | None = None
    order_amount: Decimal
    order_count: int


class StatusPoint(StrictModel):
    status: str
    order_count: int
    order_amount: Decimal


class EvidenceLine(StrictModel):
    line_id: int
    product_code: str
    product_name: str | None = None
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal


class OrderEvidence(StrictModel):
    order_id: int
    order_number: str
    buyer_code: str
    buyer_name: str | None = None
    created_at: datetime
    status: str
    amount: Decimal
    lines: list[EvidenceLine]


class Quality(StrictModel):
    reconciled_orders: int
    header_line_mismatch_count: int = 0
    price_quantity_mismatch_count: int
    sql_control_totals_match: bool


class RunStats(StrictModel):
    source_read_seconds: float = 0
    compute_seconds: float = 0
    dataframe_bytes: int = 0
    process_peak_memory_bytes: int = 0


class ReportMetadata(StrictModel):
    schema_version: Literal["1"] = REPORT_SCHEMA_VERSION
    metric_version: Literal["qy.sales_orders.v1"] = METRIC_VERSION
    source_name: str = "QY restored backup"
    source_as_of: datetime
    generated_at: datetime
    window: AnalysisWindow
    scope: DataScope
    source_kind: Literal["backup_snapshot", "synthetic"] = "backup_snapshot"
    business_scope_status: Literal["platform_confirmed_by_owner"] = "platform_confirmed_by_owner"
    time_basis: str = "ERP 原库创建日期；暂按 Asia/Shanghai 解释，业务时区待确认"
    currency: str = "未确认；金额保留 ERP 原始单位，未进行币种转换"
    state_policy: str = "全部原始订单状态；不是支付成交额，不扣减退货单"
    warnings: list[str] = Field(
        default_factory=lambda: [
            "当前为备份快照，不代表实时经营数据。",
            "数据覆盖整个平台全部商家；有效成交状态及币种尚待业务确认。",
            "支付、成功退款、平台 GMV 和平台收入指标尚未启用。",
            "日期按订单创建日期统计，不能当作支付或履约发生日期。",
        ]
    )


class OperatingAnalysis(StrictModel):
    policy: BusinessRules
    policy_fingerprint: str
    summary: Summary
    daily: list[DailyPoint]
    buyers: list[Breakdown]
    products: list[Breakdown]
    excluded_order_count: int
    excluded_order_amount: Decimal
    unknown_statuses: list[StatusPoint]


class SalesReport(StrictModel):
    metadata: ReportMetadata
    summary: Summary
    daily: list[DailyPoint]
    buyers: list[Breakdown]
    products: list[Breakdown]
    statuses: list[StatusPoint]
    quality: Quality
    stats: RunStats = Field(default_factory=RunStats)
    evidence: list[OrderEvidence]
    operating: OperatingAnalysis | None = None
    operations: OperationsSnapshot | None = None
