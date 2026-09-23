"""Deterministic read-only analysis of the authorized, reconciled sales snapshot."""

from datetime import datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from app.analysis.charts import build_chart
from app.analysis.object_filters import filter_notes, filtered_orders, validate_filters
from app.analysis.sales_query import QueryUnavailable, _local_date
from app.capabilities.registry import SALES_METRIC_DEFINITIONS, SALES_TITLES
from app.capabilities.view import capability_view, date_bounds, unavailable_message
from app.core.business_rules import policy_fingerprint
from app.schemas.analytics import AnalysisPlan, AnalysisResponse, AnalysisResult
from app.schemas.sales import SalesReport

ZERO = Decimal("0.0000")
METRICS = SALES_METRIC_DEFINITIONS
TITLES = SALES_TITLES


def available_dates(report: SalesReport):
    return date_bounds(report, "sales")


def validate_plan(report: SalesReport, plan: AnalysisPlan):
    from app.analysis.operations import validate_operation_step

    view = capability_view(report)
    for step in plan.steps:
        capability = view["domains"][step.domain]
        if not capability["executable"]:
            raise QueryUnavailable(unavailable_message(capability))
        validate_filters(report, step)
        if step.domain != "sales":
            validate_operation_step(report, step)
            continue
        start, end = available_dates(report)
        intervals = [(step.start_date, step.end_date_exclusive)]
        if step.kind == "comparison":
            intervals.append((step.comparison_start_date, step.comparison_end_date_exclusive))
        for requested_start, requested_end in intervals:
            if requested_start < start or requested_end > end:
                raise QueryUnavailable(
                    f"数据未覆盖完整区间；可用日期为 {start} 至 {end - timedelta(days=1)}。"
                )


def _validate_evidence(report: SalesReport):
    quality, operating, metadata = report.quality, report.operating, report.metadata
    if (
        operating is None
        or not quality.sql_control_totals_match
        or quality.header_line_mismatch_count
        or quality.reconciled_orders != len(report.evidence)
        or report.summary.order_count != len(report.evidence)
        or len({o.order_id for o in report.evidence}) != len(report.evidence)
        or operating.policy_fingerprint != policy_fingerprint(operating.policy)
    ):
        raise QueryUnavailable("快照对账或规则校验未通过，请重新生成分析快照。")
    tz = ZoneInfo(operating.policy.business_timezone)
    for order in report.evidence:
        day = _local_date(order.created_at, tz)
        if (
            not metadata.window.start <= day < metadata.window.end
            or not order.amount.is_finite()
            or not order.buyer_code.strip()
            or (
                not metadata.scope.all_buyers and order.buyer_code not in metadata.scope.buyer_codes
            )
            or not order.lines
            or len({line.line_id for line in order.lines}) != len(order.lines)
            or any(
                not line.amount.is_finite() or not line.product_code.strip() for line in order.lines
            )
            or sum((line.amount for line in order.lines), ZERO) != order.amount
            or metadata.source_as_of < datetime.combine(day, time.min, tz)
        ):
            raise QueryUnavailable("快照证据不完整或超出授权范围，请重新生成分析快照。")
    if sum((order.amount for order in report.evidence), ZERO) != report.summary.order_amount:
        raise QueryUnavailable("快照金额对账未通过，请重新生成分析快照。")


def _amount(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP), "f")


def _evidence(ids) -> dict:
    ordered = sorted(set(int(value) for value in ids))
    return {"evidence_ids": ordered[:100], "evidence_count": len(ordered)}


def _frames(report: SalesReport, evidence=None):
    tz = ZoneInfo(report.operating.policy.business_timezone)
    orders, lines = [], []
    for order in report.evidence if evidence is None else evidence:
        if order.status not in report.operating.policy.included_statuses:
            continue
        day = _local_date(order.created_at, tz)
        orders.append(
            dict(
                order_id=order.order_id,
                code=order.buyer_code,
                name=order.buyer_name or order.buyer_code,
                day=day,
                amount=order.amount,
            )
        )
        lines.extend(
            dict(
                order_id=order.order_id,
                code=line.product_code,
                name=line.product_name or line.product_code,
                day=day,
                amount=line.amount,
            )
            for line in order.lines
        )
    columns = ["order_id", "code", "name", "day", "amount"]
    return pd.DataFrame(orders, columns=columns), pd.DataFrame(lines, columns=columns)


def _period(frame, start, end):
    return frame.loc[(frame.day >= start) & (frame.day < end)]


def _groups(frame):
    return [
        dict(
            code=str(code),
            name=str(group.iloc[-1]["name"]),
            amount=_amount(sum(group.amount, ZERO)),
            order_count=int(group.order_id.nunique()),
            **_evidence(group.order_id),
        )
        for code, group in frame.groupby("code", sort=True)
    ]


def _daily(orders, step):
    groups = {day: group for day, group in orders.groupby("day")}
    rows = []
    for offset in range((step.end_date_exclusive - step.start_date).days):
        day = step.start_date + timedelta(days=offset)
        group = groups.get(day, orders.iloc[:0])
        rows.append(
            dict(
                day=day.isoformat(),
                amount=_amount(sum(group.amount, ZERO)),
                order_count=len(group),
                buyer_count=int(group.code.nunique()),
                **_evidence(group.order_id),
            )
        )
    return rows


def _comparison(orders, lines, step):
    frame = lines if step.dimension == "product" else orders
    current = _period(frame, step.start_date, step.end_date_exclusive)
    previous = _period(frame, step.comparison_start_date, step.comparison_end_date_exclusive)
    now = {row["code"]: row for row in _groups(current)}
    before = {row["code"]: row for row in _groups(previous)}
    rows = []
    for code in sorted(now.keys() | before.keys()):
        n, b = now.get(code, {}), before.get(code, {})
        amount, baseline = Decimal(n.get("amount", "0")), Decimal(b.get("amount", "0"))
        ids = frame.loc[frame.code == code]
        ids = ids.loc[
            ((ids.day >= step.start_date) & (ids.day < step.end_date_exclusive))
            | (
                (ids.day >= step.comparison_start_date)
                & (ids.day < step.comparison_end_date_exclusive)
            )
        ]
        rows.append(
            dict(
                code=code,
                name=n.get("name") or b["name"],
                amount=_amount(amount),
                previous_amount=_amount(baseline),
                delta=_amount(amount - baseline),
                **_evidence(ids.order_id),
            )
        )
    rows.sort(key=lambda row: (-abs(Decimal(row["delta"])), row["code"]))
    total, baseline = sum(current.amount, ZERO), sum(previous.amount, ZERO)
    shown = rows[: step.top_n]
    return AnalysisResult(
        kind=step.kind,
        title=TITLES[step.kind],
        rows=shown,
        columns={
            "name": "商品" if step.dimension == "product" else "客户",
            "amount": "本期金额",
            "previous_amount": "比较期金额",
            "delta": "变化金额",
        },
        totals=dict(
            amount=_amount(total),
            previous_amount=_amount(baseline),
            delta=_amount(total - baseline),
            change_rate=str((total - baseline) / baseline) if baseline else None,
            total_groups=len(rows),
            other_delta=_amount(sum((Decimal(r["delta"]) for r in rows[step.top_n :]), ZERO)),
        ),
        findings=[
            f"本期订单金额 {_amount(total)}，比较期 {_amount(baseline)}，"
            f"变化 {_amount(total - baseline)}。"
        ],
        notes=[
            "按变化金额绝对值排序；贡献拆解不代表因果。",
            "比较期金额为零时，变化率无定义。"
            if not baseline
            else "两个期间天数相同，分别按完整日期统计。",
        ],
    )


def _execute(orders, lines, step):
    if step.kind == "comparison":
        return _comparison(orders, lines, step)
    selected = _period(orders, step.start_date, step.end_date_exclusive)
    selected_lines = _period(lines, step.start_date, step.end_date_exclusive)
    total = sum(selected.amount, ZERO)
    result = AnalysisResult(
        kind=step.kind,
        title=TITLES[step.kind],
        columns={},
        rows=[],
        totals={"amount": _amount(total), "order_count": len(selected)},
        notes=["订单金额按暂定有效状态统计，不是支付成交额；证据最多展示前 100 张订单。"],
    )
    if step.kind == "summary":
        result = result.model_copy(
            update={
                "columns": {
                    "amount": "有效订单金额",
                    "order_count": "订单数",
                    "buyer_count": "采购企业数",
                    "average_order_amount": "平均订单金额",
                    "product_count": "商品种数",
                },
                "rows": [
                    dict(
                        amount=_amount(total),
                        order_count=len(selected),
                        buyer_count=int(selected.code.nunique()),
                        average_order_amount=_amount(total / len(selected))
                        if len(selected)
                        else None,
                        product_count=int(selected_lines.code.nunique()),
                        **_evidence(selected.order_id),
                    )
                ],
            }
        )
    elif step.kind in {"buyer_ranking", "product_ranking"}:
        rows = _groups(selected if step.kind == "buyer_ranking" else selected_lines)
        key = "amount" if step.metric == "amount" else "order_count"
        direction = 1 if step.order == "ascending" else -1
        rows.sort(key=lambda row: (direction * Decimal(row[key]), row["code"]))
        result = result.model_copy(
            update={
                "columns": {
                    "name": "名称",
                    "code": "编码",
                    "amount": "订单金额",
                    "order_count": "订单数",
                },
                "rows": rows[: step.top_n],
                "totals": dict(result.totals, total_groups=len(rows)),
                "notes": result.notes
                + [
                    f"按{'订单金额' if step.metric == 'amount' else '订单数'}"
                    f"{'从低到高' if step.order == 'ascending' else '从高到低'}排列，"
                    f"取前 {step.top_n} 项；同值按编码排序。",
                    "仅对所选期间有有效销售记录的对象排行，不包含无销售记录的对象。",
                ],
            }
        )
    else:
        rows = _daily(selected, step)
        columns = {
            "day": "日期",
            "amount": "订单金额",
            "order_count": "订单数",
            "buyer_count": "采购企业数",
        }
        if step.kind == "anomalies":
            flagged = []
            for previous, row in zip(rows, rows[1:], strict=False):
                before, now = Decimal(previous["amount"]), Decimal(row["amount"])
                rate = (now - before) / abs(before) if before else None
                if (rate is not None and abs(rate) >= Decimal("0.5")) or (not before and now):
                    flagged.append(
                        dict(
                            row,
                            previous_amount=previous["amount"],
                            change_rate=str(rate) if rate is not None else None,
                            reason="前日零基数" if rate is None else "较前日波动达到 50%",
                        )
                    )
            rows = flagged
            columns = dict(columns, previous_amount="前日金额", reason="触发规则")
            result = result.model_copy(
                update={
                    "notes": result.notes
                    + [METRICS[-1]["definition"], "区间首日缺少区间内前日基线，不参与检测。"]
                }
            )
        result = result.model_copy(update={"rows": rows, "columns": columns})
    return result.model_copy(
        update={
            "findings": [
                f"所选期间有效订单 {len(selected)} 张，金额 {_amount(total)}；"
                f"采购企业 {selected.code.nunique()} 家。",
                *(["没有满足条件的记录。"] if not result.rows else []),
            ]
        }
    )


def execute_analysis(report: SalesReport, plan: AnalysisPlan) -> AnalysisResponse:
    from app.analysis.operations import execute_operation, validate_operations

    # Revalidate even plans passed by in-process callers using model_copy.
    plan = AnalysisPlan.model_validate(plan.model_dump())
    validate_plan(report, plan)
    if any(step.domain == "sales" for step in plan.steps):
        _validate_evidence(report)
        orders, lines = _frames(report)
    if any(step.domain != "sales" for step in plan.steps):
        validate_operations(report)
    if report.operating is None:
        raise QueryUnavailable("快照缺少业务时区和货币口径，请重新生成。")
    policy = report.operating.policy
    results = []
    for step in plan.steps:
        selected_orders, selected_lines = (
            _frames(report, filtered_orders(report, step))
            if step.domain == "sales" and step.filters
            else (orders, lines)
            if step.domain == "sales"
            else (None, None)
        )
        result = (
            _execute(selected_orders, selected_lines, step)
            if step.domain == "sales"
            else execute_operation(report, step)
        )
        results.append(
            result.model_copy(
                update={
                    "chart": build_chart(result, step, policy.currency),
                    "notes": result.notes + filter_notes(step),
                }
            )
        )
    return AnalysisResponse(
        run_id=uuid4().hex,
        generated_at=datetime.now(timezone.utc),
        plan=plan,
        provenance=dict(
            source_as_of=report.metadata.source_as_of,
            snapshot_generated_at=report.metadata.generated_at,
            source_kind=report.metadata.source_kind,
            scope=report.metadata.scope,
            policy_id=policy.policy_id,
            policy_fingerprint=report.operating.policy_fingerprint,
            metric_version=report.metadata.metric_version,
            currency=policy.currency,
            business_timezone=policy.business_timezone,
            included_statuses=policy.included_statuses,
        ),
        results=results,
        warnings=report.metadata.warnings,
    )


def analysis_evidence(report, body):
    step = body.step
    if step.domain != "sales":
        raise QueryUnavailable("此证据接口只支持销售订单。")
    validate_plan(report, AnalysisPlan(steps=[step]))
    _validate_evidence(report)
    tz = ZoneInfo(report.operating.policy.business_timezone)
    for order in filtered_orders(report, step):
        if order.order_id != body.order_id:
            continue
        day = _local_date(order.created_at, tz)
        in_period = step.start_date <= day < step.end_date_exclusive
        if step.kind == "comparison":
            in_period |= step.comparison_start_date <= day < step.comparison_end_date_exclusive
        if not in_period or body.buyer_code and body.buyer_code != order.buyer_code:
            break
        lines = [
            line
            for line in order.lines
            if body.product_code is None or line.product_code == body.product_code
        ]
        if lines:
            return order.model_copy(
                update={
                    "lines": lines,
                    "amount": sum((line.amount for line in lines), ZERO),
                }
            )
    raise QueryUnavailable("该订单不属于本次筛选和日期范围。")
