"""Finite sales-query grammar and deterministic ranking of snapshot evidence."""

import re
import unicodedata
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, ValidationError, model_validator

from app.core.business_rules import policy_fingerprint
from app.schemas.sales import SalesReport, StrictModel


class QueryUnavailable(ValueError):
    """A safe explanation that can be returned to an authorized caller."""


class QueryRequest(StrictModel):
    start_date: date
    end_date_exclusive: date
    metric: Literal["amount", "orders"] = "amount"
    top_n: int = Field(default=10, ge=1, le=50, strict=True)

    @model_validator(mode="after")
    def check_range(self):
        if not 0 < (self.end_date_exclusive - self.start_date).days <= 90:
            raise ValueError("查询必须包含 1 至 90 个完整日")
        return self


_PERIOD = (
    r"(?P<period>最近一周|之前一周|上周|"
    r"(?P<start>[0-9]{4}-[0-9]{2}-[0-9]{2})\s*至\s*"
    r"(?P<end>[0-9]{4}-[0-9]{2}-[0-9]{2}))"
)
_COMMAND = re.compile(
    _PERIOD + r"\s*商品(?P<metric>销售额|订单数)排行(?:\s*前(?P<top>[0-9]{1,2}))?[？?]?"
)
_NATURAL = re.compile(r"(?P<period>最近一周|之前一周|上周)哪些商品卖得好[？?]?")


def parse_question(text: str, today: date) -> QueryRequest:
    """Parse only complete supported commands; never discard unknown conditions."""
    if "药品" in text:
        raise QueryUnavailable("药品分类尚未建立；如需全部商品，请明确询问商品销售额排行。")
    if any(word in text for word in ("销量", "数量", "盒数")):
        raise QueryUnavailable("数量单位与规格尚未统一；请明确选择商品销售额或订单数排行。")
    match = _COMMAND.fullmatch(text.strip()) or _NATURAL.fullmatch(text.strip())
    if match is None:
        raise QueryUnavailable(
            "暂不支持该问题的条件或指标。请使用：2026-09-01至2026-09-02 商品销售额排行 前10，"
            "或：最近一周哪些商品卖得好？"
        )
    parts = match.groupdict()
    try:
        if parts["period"] in {"最近一周", "之前一周"}:
            start, end = today - timedelta(days=7), today
        elif parts["period"] == "上周":
            end = today - timedelta(days=today.weekday())
            start = end - timedelta(days=7)
        else:
            start = date.fromisoformat(parts["start"])
            end = date.fromisoformat(parts["end"]) + timedelta(days=1)
        return QueryRequest(
            start_date=start,
            end_date_exclusive=end,
            metric="orders" if parts.get("metric") == "订单数" else "amount",
            top_n=int(parts.get("top") or 10),
        )
    except (ValueError, OverflowError, ValidationError) as exc:
        raise QueryUnavailable(
            "日期或排行范围无效；请提供 1 至 90 个完整日、前 1 至 50 名。"
        ) from exc


class RankingItem(StrictModel):
    product_code: str
    product_name: str | None
    amount: Decimal
    order_count: int
    buyer_count: int
    amount_share: Decimal | None
    order_ids: tuple[int, ...]


class RankingResult(StrictModel):
    request: QueryRequest
    items: list[RankingItem]
    total_amount: Decimal
    order_count: int
    buyer_count: int
    source_as_of: datetime
    source_name: str
    source_kind: str
    metric_version: str
    policy_id: str
    policy_fingerprint: str
    business_timezone: str
    currency: str
    included_statuses: tuple[str, ...]


def _validate_report(report: SalesReport, request: QueryRequest) -> ZoneInfo:
    metadata, operating, quality = report.metadata, report.operating, report.quality
    if not metadata.scope.all_buyers or operating is None:
        raise QueryUnavailable("当前快照不是全平台经营快照，暂不能提供排行。")
    if (
        not quality.sql_control_totals_match
        or quality.header_line_mismatch_count
        or quality.reconciled_orders != report.summary.order_count
        or len(report.evidence) != report.summary.order_count
        or len({order.order_id for order in report.evidence}) != len(report.evidence)
        or policy_fingerprint(operating.policy) != operating.policy_fingerprint
    ):
        raise QueryUnavailable("快照对账或证据完整性校验未通过，暂不能提供排行。")
    tz = ZoneInfo(operating.policy.business_timezone)
    if (
        metadata.source_as_of.tzinfo is None
        or request.start_date < metadata.window.start
        or request.end_date_exclusive > metadata.window.end
        or metadata.source_as_of < datetime.combine(request.end_date_exclusive, time.min, tz)
    ):
        source = metadata.source_as_of
        if source.tzinfo is None:
            watermark = f"{source:%Y-%m-%d %H:%M:%S}（来源时区缺失）"
            available = "无法确认完整日期，请联系管理员检查快照时区"
        else:
            local = source.astimezone(tz)
            watermark = f"{local:%Y-%m-%d %H:%M:%S}（{tz.key}）"
            complete_end = min(metadata.window.end, local.date())
            available = (
                f"{metadata.window.start}至{complete_end - timedelta(days=1)}"
                if complete_end > metadata.window.start else "暂无完整日期"
            )
        requested = f"{request.start_date}至{request.end_date_exclusive - timedelta(days=1)}"
        raise QueryUnavailable(
            f"数据尚未覆盖所请求的完整区间：{requested}。"
            f"数据截至：{watermark}；可查询完整日期：{available}。请明确指定新的日期范围。"
        )
    for order in report.evidence:
        local_date = _local_date(order.created_at, tz)
        if (
            not metadata.window.start <= local_date < metadata.window.end
            or not order.lines
            or len({line.line_id for line in order.lines}) != len(order.lines)
            or not order.amount.is_finite()
            or any(
                not line.amount.is_finite() or not line.product_code.strip() for line in order.lines
            )
            or not order.buyer_code.strip()
            or sum((line.amount for line in order.lines), Decimal(0)) != order.amount
        ):
            raise QueryUnavailable("快照证据校验未通过，暂不能提供排行。")
    if sum((o.amount for o in report.evidence), Decimal(0)) != report.summary.order_amount:
        raise QueryUnavailable("快照证据金额对账未通过，暂不能提供排行。")
    return tz


def _local_date(value: datetime, tz: ZoneInfo) -> date:
    # SQL Server source timestamps are naive business-local timestamps.
    return value.astimezone(tz).date() if value.tzinfo is not None else value.date()


def product_ranking(report: SalesReport, request: QueryRequest) -> RankingResult:
    """Calculate from all evidence in the validated scope before taking top N."""
    tz = _validate_report(report, request)
    policy = report.operating.policy
    orders = [
        order
        for order in report.evidence
        if request.start_date <= _local_date(order.created_at, tz) < request.end_date_exclusive
        and order.status in policy.included_statuses
    ]
    total = sum((order.amount for order in orders), Decimal("0.0000"))
    groups = {}
    for order in orders:
        for line in order.lines:
            group = groups.setdefault(
                line.product_code,
                {"name": None, "amount": Decimal("0.0000"), "orders": set(), "buyers": set()},
            )
            group["name"] = line.product_name or group["name"]
            group["amount"] += line.amount
            group["orders"].add(order.order_id)
            group["buyers"].add(order.buyer_code)
    items = [
        RankingItem(
            product_code=code,
            product_name=group["name"],
            amount=group["amount"],
            order_count=len(group["orders"]),
            buyer_count=len(group["buyers"]),
            amount_share=group["amount"] / total if total else None,
            order_ids=tuple(sorted(group["orders"])),
        )
        for code, group in groups.items()
    ]
    items.sort(
        key=lambda item: (
            -item.amount if request.metric == "amount" else -item.order_count,
            item.product_code,
        )
    )
    return RankingResult(
        request=request,
        items=items[: request.top_n],
        total_amount=total,
        order_count=len(orders),
        buyer_count=len({order.buyer_code for order in orders}),
        source_as_of=report.metadata.source_as_of,
        source_name=report.metadata.source_name,
        source_kind=report.metadata.source_kind,
        metric_version=report.metadata.metric_version,
        policy_id=policy.policy_id,
        policy_fingerprint=report.operating.policy_fingerprint,
        business_timezone=policy.business_timezone,
        currency=policy.currency,
        included_statuses=policy.included_statuses,
    )


def _label(value: str, limit: int = 24) -> str:
    """Keep untrusted labels on one bounded plain-text line."""
    cleaned = " ".join(
        "".join(
            " " if unicodedata.category(char).startswith("C") or char in "<>" else char
            for char in value
        ).split()
    )
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def render_ranking(result: RankingResult, query_id: str) -> str:
    request = result.request
    metric = "销售额" if request.metric == "amount" else "订单数"
    lines = [
        f"{request.start_date}至{request.end_date_exclusive - timedelta(days=1)} 商品{metric}排行",
        f"全平台 · 有效销售 · 按{metric}降序 · 前{request.top_n}名 · {result.currency}",
        f"按订单创建日期（{result.business_timezone}）；纳入状态：{'、'.join(result.included_statuses)}",
        "金额为ERP订单明细金额，不是支付成交额，未扣减退款。",
        f"合计 {_money(result.total_amount)}；订单 {result.order_count}；"
        f"采购企业 {result.buyer_count}",
    ]
    for index, item in enumerate(result.items, 1):
        share = _money(item.amount_share * 100) + "%" if item.amount_share is not None else "不计算"
        name = f" {_label(item.product_name)}" if item.product_name else ""
        lines.append(
            f"{index}. {_label(item.product_code)}{name} | {_money(item.amount)} | "
            f"订单 {item.order_count} | 采购企业 {item.buyer_count} | 金额占比 {share}"
        )
    if not result.items:
        lines.append("该完整区间无有效销售订单。")
    local = result.source_as_of.astimezone(ZoneInfo(result.business_timezone))
    lines.extend(
        [
            f"来源：{_label(result.source_name, 60)}（{result.source_kind}，非实时）；区间完整",
            f"数据截至：{local:%Y-%m-%d %H:%M:%S}（{result.business_timezone}）",
            f"指标版本：{result.metric_version}；规则：{result.policy_id}",
            f"规则指纹：{result.policy_fingerprint}",
            f"查询编号：{_label(query_id, 80)}",
        ]
    )
    return "\n".join(lines)
