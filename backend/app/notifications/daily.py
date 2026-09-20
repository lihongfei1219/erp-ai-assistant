"""Build one day's digest from the already-authorized, immutable snapshot."""

import hashlib
import json
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from app.schemas.sales import Breakdown, OrderEvidence, SalesReport, StrictModel, Summary

ZERO = Decimal("0.0000")


class DigestUnavailable(ValueError):
    pass


class DailyDigest(StrictModel):
    day: date
    demo: bool
    partial: bool
    source_as_of: datetime
    currency: str
    timezone: str
    policy_id: str
    scope_key: str
    summary: Summary
    previous_summary: Summary | None
    amount_change_percent: Decimal | None
    comparison_note: str
    excluded_order_count: int
    buyers: list[Breakdown]
    products: list[Breakdown]
    title: str
    text: str


def summarize(orders: list[OrderEvidence]) -> Summary:
    total = sum((order.amount for order in orders), ZERO)
    completed = [order for order in orders if order.status == "订单完成"]
    return Summary(
        order_amount=total,
        order_count=len(orders),
        buyer_count=len({order.buyer_code for order in orders}),
        average_order_amount=(total / len(orders)).quantize(Decimal("0.0001"), ROUND_HALF_UP)
        if orders
        else None,
        line_count=sum(len(order.lines) for order in orders),
        product_count=len({line.product_code for order in orders for line in order.lines}),
        completed_status_order_count=len(completed),
        completed_status_order_amount=sum((order.amount for order in completed), ZERO),
    )


def ranking(orders: list[OrderEvidence], *, product: bool = False) -> list[Breakdown]:
    groups: dict[str, dict] = {}
    for order in orders:
        rows = (
            [(line.product_code, line.product_name, line.amount) for line in order.lines]
            if product
            else [(order.buyer_code, order.buyer_name, order.amount)]
        )
        for code, name, amount in rows:
            group = groups.setdefault(code, {"amount": ZERO, "ids": set(), "name": None})
            group["amount"] += amount
            group["ids"].add(order.order_id)
            if name:
                group["name"] = name
    points = [
        Breakdown(
            code=code, name=row["name"], order_amount=row["amount"], order_count=len(row["ids"])
        )
        for code, row in groups.items()
    ]
    return sorted(points, key=lambda point: (-point.order_amount, point.code))[:5]


def money(value: Decimal | None) -> str:
    return "—" if value is None else f"{value.quantize(Decimal('0.01'), ROUND_HALF_UP):,.2f}"


def build_digest(report: SalesReport, day: date, *, demo: bool = False) -> DailyDigest:
    if report.operating is None:
        raise DigestUnavailable("快照不含有效销售规则，请重新生成经营快照")
    policy = report.operating.policy
    tz = ZoneInfo(policy.business_timezone)
    if report.metadata.source_as_of.tzinfo is None:
        raise DigestUnavailable("快照截至时间缺少时区")
    source_as_of = report.metadata.source_as_of.astimezone(tz)
    if not report.metadata.window.start <= day < report.metadata.window.end:
        raise DigestUnavailable("所选日期不在快照覆盖范围内，请更新快照")
    if day > source_as_of.date():
        raise DigestUnavailable("数据尚未覆盖所选日期，请更新快照")
    partial = source_as_of < datetime.combine(day + timedelta(days=1), time.min, tz)
    if partial and not demo:
        raise DigestUnavailable("昨日数据不完整，暂不发送正式日报；可使用演示预览")
    if not report.quality.sql_control_totals_match or report.quality.header_line_mismatch_count:
        raise DigestUnavailable("快照未通过对账，暂不生成日报")
    raw = [order for order in report.evidence if order.created_at.date() == day]
    included = [order for order in raw if order.status in policy.included_statuses]
    summary = summarize(included)
    previous_day = day - timedelta(days=1)
    previous = None
    change = None
    note = "前一天不在快照范围内，不计算环比。"
    if report.metadata.window.start <= previous_day:
        previous = summarize(
            [
                order
                for order in report.evidence
                if order.created_at.date() == previous_day
                and order.status in policy.included_statuses
            ]
        )
        if partial:
            note = "当日数据不完整，前一天仅供参考，不计算环比。"
        elif previous.order_amount == 0:
            note = "前一天有效销售金额为零，不计算环比百分比。"
        else:
            change = (
                (summary.order_amount - previous.order_amount) / previous.order_amount * 100
            ).quantize(Decimal("0.01"), ROUND_HALF_UP)
            note = f"较前一天有效销售金额 {change:+.2f}%。"
    buyers = ranking(included)
    products = ranking(included, product=True)
    title = f"{'【演示】' if demo else ''}销售日报 · {day.isoformat()}"
    text = [
        f"统计日期：{day.isoformat()}（{'演示：将该日视为昨日' if demo else '昨日'}）",
        f"数据截至：{source_as_of:%Y-%m-%d %H:%M:%S}（{policy.business_timezone}）",
        f"范围：{'全平台采购企业' if report.metadata.scope.all_buyers else '当前授权采购企业范围'}",
        f"有效销售金额：{money(summary.order_amount)} {policy.currency}",
        f"有效订单：{summary.order_count} 单 · 采购企业：{summary.buyer_count} 家",
        f"客单价：{money(summary.average_order_amount)} {policy.currency}",
        f"排除订单：{len(raw) - len(included)} 单",
    ]
    if partial:
        text.append("⚠ 当日数据不完整，当前仅为截至备份时刻的结果。")
    if previous is not None:
        text.append(
            f"前一天有效销售：{money(previous.order_amount)} {policy.currency}"
            f" / {previous.order_count} 单"
        )
    text.append(note)
    for label, points in [("客户 Top 5", buyers), ("商品 Top 5", products)]:
        text.extend(["", label])
        text.extend(
            f"{index}. {point.name or point.code} · {money(point.order_amount)}"
            f" {policy.currency} · {point.order_count} 单"
            for index, point in enumerate(points, start=1)
        )
        if not points:
            text.append("当日无符合条件的订单。")
    text.extend(
        [
            "",
            f"口径：{policy.label}；纳入{'、'.join(policy.included_statuses)}；"
            "按订单创建日期统计。",
            "订单金额不是支付成交额，未扣减退款。",
        ]
    )
    scope = report.metadata.scope.model_dump(mode="json")
    scope["buyer_codes"] = sorted(scope["buyer_codes"])
    scope_key = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
    return DailyDigest(
        day=day,
        demo=demo,
        partial=partial,
        source_as_of=source_as_of,
        currency=policy.currency,
        timezone=policy.business_timezone,
        policy_id=policy.policy_id,
        scope_key=scope_key,
        summary=summary,
        previous_summary=previous,
        amount_change_percent=change,
        comparison_note=note,
        excluded_order_count=len(raw) - len(included),
        buyers=buyers,
        products=products,
        title=title,
        text="\n".join(text),
    )


def message_card(digest: DailyDigest) -> dict:
    # ERP display names remain plain text; they cannot create mentions or links.
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange" if digest.partial else "blue",
            "title": {"tag": "plain_text", "content": digest.title},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "plain_text", "content": block}}
            for block in digest.text.split("\n\n")
        ],
    }
