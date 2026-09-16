"""Deterministic pandas calculations; no database or model calls."""

from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from numbers import Integral
from time import perf_counter
from zoneinfo import ZoneInfo

import pandas as pd
import psutil

from app.connectors.qy import SalesExtract, SourceDataError
from app.core.business_rules import policy_fingerprint
from app.schemas.sales import (
    AnalysisWindow,
    Breakdown,
    BusinessRules,
    DailyPoint,
    DataScope,
    EvidenceLine,
    OperatingAnalysis,
    OrderEvidence,
    Quality,
    ReportMetadata,
    RunStats,
    SalesReport,
    StatusPoint,
    Summary,
)

ZERO = Decimal("0.0000")
PRECISION = Decimal("0.0001")


def decimal_value(value) -> Decimal:
    if isinstance(value, Decimal) and value.is_finite():
        return value
    if isinstance(value, Integral) and not isinstance(value, bool):
        return Decimal(int(value))
    raise SourceDataError("数值必须为有限 Decimal 或整数，不能含空值或浮点转换")


def _total(values) -> Decimal:
    return sum(values, ZERO)


def _validate_frame(frame: pd.DataFrame, required: set[str], keys: list[str], label: str):
    if not required.issubset(frame.columns):
        raise SourceDataError(f"{label}缺少必需字段")
    if frame[list(required)].isna().any().any():
        raise SourceDataError(f"{label}存在空字段，不能发布完整分析")
    if frame.duplicated(keys).any():
        raise SourceDataError(f"{label}存在重复主键，不能重复累计")
    for key in keys:
        if not frame[key].map(lambda x: isinstance(x, Integral) and not isinstance(x, bool)).all():
            raise SourceDataError(f"{label}的主键必须为整数")


def _codes(frame: pd.DataFrame, fields: list[str]):
    for field in fields:
        if not frame[field].map(lambda x: isinstance(x, str) and bool(x.strip())).all():
            raise SourceDataError("业务编码、单号或订单状态缺失")
        frame[field] = frame[field].str.strip()


def _breakdown(frame: pd.DataFrame, column: str) -> list[Breakdown]:
    name_column = "buyer_name" if column == "buyer_code" else "product_name"
    points = [
        Breakdown(
            code=str(code),
            order_amount=_total(group.amount),
            order_count=group.order_id.nunique(),
            name=(
                group[name_column].dropna().iloc[-1]
                if name_column in group and not group[name_column].dropna().empty
                else None
            ),
        )
        for code, group in frame.groupby(column, sort=True)
    ]
    return sorted(points, key=lambda point: (-point.order_amount, point.code))


def _summary(orders: pd.DataFrame, lines: pd.DataFrame) -> Summary:
    amount = _total(orders.amount)
    completed = orders.loc[orders.status == "订单完成"]
    return Summary(
        order_amount=amount,
        order_count=len(orders),
        buyer_count=orders.buyer_code.nunique(),
        average_order_amount=(amount / len(orders)).quantize(PRECISION, ROUND_HALF_UP)
        if len(orders)
        else None,
        line_count=len(lines),
        product_count=lines.product_code.nunique(),
        completed_status_order_count=len(completed),
        completed_status_order_amount=_total(completed.amount),
    )


def _daily(
    orders: pd.DataFrame, window: AnalysisWindow, source_local: datetime
) -> list[DailyPoint]:
    observed_end = min(
        window.end, (pd.Timestamp(source_local.date()) + pd.Timedelta(days=1)).date()
    )
    by_day = {day: group for day, group in orders.groupby(orders.created_at.dt.date)}
    points = []
    for day in pd.date_range(window.start, observed_end, inclusive="left"):
        group = by_day.get(day.date())
        points.append(
            DailyPoint(
                day=day.date(),
                order_amount=_total(group.amount) if group is not None else ZERO,
                order_count=len(group) if group is not None else 0,
                buyer_count=group.buyer_code.nunique() if group is not None else 0,
            )
        )
    return points


def analyze_sales(
    extract: SalesExtract,
    window: AnalysisWindow,
    scope: DataScope,
    *,
    source_as_of: datetime,
    generated_at: datetime | None = None,
    synthetic: bool = False,
    rules: BusinessRules | None = None,
) -> SalesReport:
    started = perf_counter()
    generated_at = generated_at or datetime.now(timezone.utc)
    if source_as_of.tzinfo is None or generated_at.tzinfo is None:
        raise SourceDataError("来源截至时间和生成时间必须包含时区")
    if source_as_of > generated_at:
        raise SourceDataError("来源截至时间不能晚于生成时间")
    business_timezone = rules.business_timezone if rules else "Asia/Shanghai"
    source_local = source_as_of.astimezone(ZoneInfo(business_timezone)).replace(tzinfo=None)
    if window.start > source_local.date():
        raise SourceDataError("查询开始日期超出来源快照覆盖时间")
    orders, lines = extract.orders.copy(deep=True), extract.lines.copy(deep=True)
    _validate_frame(
        orders,
        {
            "order_id",
            "order_number",
            "buyer_code",
            "created_at",
            "status",
            "amount",
        },
        ["order_id"],
        "销售订单",
    )
    _validate_frame(
        lines,
        {
            "order_id",
            "line_id",
            "product_code",
            "quantity",
            "unit_price",
            "amount",
        },
        ["order_id", "line_id"],
        "销售明细",
    )
    _codes(orders, ["order_number", "buyer_code", "status"])
    _codes(lines, ["product_code"])
    for frame, name_column in [(orders, "buyer_name"), (lines, "product_name")]:
        if name_column in frame:
            names = frame[name_column].map(
                lambda value: value.strip() if isinstance(value, str) and value.strip() else None
            )
            frame[name_column] = names.astype(object).where(names.notna(), None)
    for frame, columns in [(orders, ["amount"]), (lines, ["amount", "unit_price", "quantity"])]:
        for column in columns:
            frame[column] = frame[column].map(decimal_value).astype(object)
    try:
        orders["created_at"] = pd.to_datetime(orders.created_at, errors="raise", format="ISO8601")
    except (ValueError, TypeError) as exc:
        raise SourceDataError("订单日期无法解析") from exc
    if orders.created_at.dt.tz is not None:
        raise SourceDataError("源订单日期应为 SQL Server 原始无时区日期")
    if not (
        (orders.created_at >= pd.Timestamp(window.start))
        & (orders.created_at < pd.Timestamp(window.end))
        & (orders.created_at <= pd.Timestamp(source_local))
    ).all():
        raise SourceDataError("订单日期超出指定查询范围或来源截至时间")
    if not scope.all_buyers and not orders.buyer_code.isin(scope.buyer_codes).all():
        raise SourceDataError("源数据包含授权企业范围之外的订单")
    if not lines.order_id.isin(orders.order_id).all():
        raise SourceDataError("存在没有对应表头的明细")
    if not orders.order_id.isin(lines.order_id).all():
        raise SourceDataError("存在没有明细的订单")
    # Aggregate each order before joining, preserving the one-row-per-order grain.
    line_totals = lines.groupby("order_id", sort=False)["amount"].agg(_total)
    if not orders.empty:
        expected = orders.order_id.map(line_totals)
        if not (orders.amount == expected).all():
            raise SourceDataError("订单表头金额与明细金额不一致，已阻止发布")
    order_amount = _total(orders.amount)
    if len(orders) != extract.sql_order_count or order_amount != decimal_value(
        extract.sql_order_amount
    ):
        raise SourceDataError("pandas 结果与独立 SQL 控制总数／金额不一致")
    price_mismatches = sum(
        row.amount != row.quantity * row.unit_price for row in lines.itertuples(index=False)
    )
    summary = _summary(orders, lines)
    daily = _daily(orders, window, source_local)
    statuses = [
        StatusPoint(status=str(state), order_count=len(group), order_amount=_total(group.amount))
        for state, group in orders.groupby("status", sort=True)
    ]
    line_groups = {key: group for key, group in lines.groupby("order_id", sort=False)}
    evidence = [
        OrderEvidence(
            order_id=row.order_id,
            order_number=row.order_number,
            buyer_code=row.buyer_code,
            buyer_name=getattr(row, "buyer_name", None),
            created_at=row.created_at.to_pydatetime(),
            status=row.status,
            amount=row.amount,
            lines=[
                EvidenceLine(
                    line_id=item.line_id,
                    product_code=item.product_code,
                    product_name=getattr(item, "product_name", None),
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    amount=item.amount,
                )
                for item in line_groups[row.order_id].sort_values("line_id").itertuples(index=False)
            ],
        )
        for row in orders.sort_values("order_id").itertuples(index=False)
    ]
    metadata = ReportMetadata(
        source_as_of=source_as_of,
        generated_at=generated_at,
        window=window,
        scope=scope,
        source_kind="synthetic" if synthetic else "backup_snapshot",
        time_basis=f"ERP 原库创建日期；按 {business_timezone} 解释，业务时区暂定",
    )
    operating = None
    if rules is not None:
        included = orders.loc[orders.status.isin(rules.included_statuses)]
        included_lines = lines.loc[lines.order_id.isin(included.order_id)]
        operating = OperatingAnalysis(
            policy=rules,
            policy_fingerprint=policy_fingerprint(rules),
            summary=_summary(included, included_lines),
            daily=_daily(included, window, source_local),
            buyers=_breakdown(included, "buyer_code"),
            products=_breakdown(included_lines, "product_code"),
            excluded_order_count=len(orders) - len(included),
            excluded_order_amount=order_amount - _total(included.amount),
            unknown_statuses=[
                state for state in statuses if state.status not in rules.known_statuses
            ],
        )
    if window.end > source_local.date():
        metadata.warnings.append("所选范围包含备份当日或之后的日期，仅截至备份时刻有数据。")
    memory = psutil.Process().memory_info()
    return SalesReport(
        metadata=metadata,
        summary=summary,
        daily=daily,
        buyers=_breakdown(orders, "buyer_code"),
        products=_breakdown(lines, "product_code"),
        statuses=statuses,
        quality=Quality(
            reconciled_orders=len(orders),
            price_quantity_mismatch_count=price_mismatches,
            sql_control_totals_match=True,
        ),
        stats=RunStats(
            source_read_seconds=round(extract.read_seconds, 4),
            compute_seconds=round(perf_counter() - started, 4),
            dataframe_bytes=int(
                orders.memory_usage(deep=True).sum() + lines.memory_usage(deep=True).sum()
            ),
            process_peak_memory_bytes=getattr(memory, "peak_wset", memory.rss),
        ),
        evidence=evidence,
        operating=operating,
    )
