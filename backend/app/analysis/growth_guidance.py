"""Date suggestions derived from the same complete partitions as growth execution."""

from datetime import timedelta

from app.analysis.growth import LABELS, coverage, period_covered
from app.core.comparison_periods import growth_periods
from app.schemas.analytics import AnalysisStep
from app.semantic.dates import resolve_period


def _next_month(day):
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)


def _period_label(start, end):
    if start.day == 1 and _next_month(start) == end:
        return f"{start.year}年{start.month}月"
    return f"{start} 至 {end - timedelta(days=1)}"


def _periods(item, start, end):
    return growth_periods(
        AnalysisStep(
            domain="shipping",
            kind="growth",
            start_date=start,
            end_date_exclusive=end,
            growth_basis=item.growth_basis or "both",
        )
    )


def comparable_months(report, item, limit=3):
    intervals = coverage(report)
    months = set()
    for start, end in intervals:
        month = start.replace(day=1)
        while month < end:
            months.add(month)
            month = _next_month(month)
    choices = []
    for start in sorted(months, reverse=True):
        end = _next_month(start)
        try:
            periods = _periods(item, start, end)
        except (ValueError, OverflowError):
            continue
        if not all(period_covered(intervals, a, b) for a, b in periods.values()):
            continue
        comparison = []
        if "previous" in periods:
            comparison.append(f"环比{periods['previous'][0]:%Y-%m}")
        if "year_over_year" in periods:
            comparison.append(f"同比{periods['year_over_year'][0]:%Y-%m}")
        choices.append((start, end, f"查{start:%Y年%m月}（{'，'.join(comparison)}）"))
        if len(choices) == limit:
            break
    return choices


def date_gap_message(report, item, today):
    """Explain missing facts without replacing the requested month or business filters."""
    try:
        start, end = resolve_period(item.time, today)
        periods = _periods(item, start, end)
    except (TypeError, ValueError, OverflowError):
        return "请明确本期日期；本期及所选比较期均需有完整出库数据。"
    intervals = coverage(report)
    missing = [
        f"{LABELS[key]}（{_period_label(a, b)}）"
        for key, (a, b) in periods.items()
        if not period_covered(intervals, a, b)
    ]
    target = "指定品种内的客户" if item.target == "buyer" else "品种"
    metric = "出库数量" if item.metric == "quantity" else "出库金额"
    parts = [
        f"已理解：按{target}比较{metric}变化。",
        "；".join(f"{LABELS[key]}：{_period_label(a, b)}" for key, (a, b) in periods.items())
        + "。",
    ]
    if missing:
        parts.append("暂不能计算：" + "、".join(missing) + "的出库数据未完整覆盖。")
    parts.append(
        "下方月份已核对所需比较期；你也可以直接说其他日期。"
        if comparable_months(report, item, limit=1)
        else "当前没有满足这些比较条件的完整月份；可指定其他日期，或补充对应数据。"
    )
    return "\n".join(parts)
