"""Reconciled dispatch comparisons and within-product customer contributions."""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.analysis.object_filters import matches
from app.analysis.sales_query import QueryUnavailable
from app.core.comparison_periods import growth_periods
from app.schemas.analytics import AnalysisResult

ZERO = Decimal(0)
LABELS = {"current": "本期", "previous": "上期", "year_over_year": "去年同期"}


def number(value):
    return format(value.quantize(Decimal("0.0001")), "f")


def partitions(report):
    if not report.operations:
        return []
    return ([report.operations.shipping] if report.operations.shipping else []) + list(
        report.operations.shipping_history
    )


def coverage(report):
    cutoff = report.metadata.source_as_of.astimezone(
        ZoneInfo(report.operating.policy.business_timezone)
    ).date()
    return [
        (p.start, min(p.end_exclusive, cutoff))
        for p in partitions(report)
        if p.comparison_ready and p.start < cutoff
    ]


def period_covered(intervals, start, end):
    cursor = start
    for begin, until in sorted(intervals):
        if begin <= cursor < until:
            cursor = until
    return cursor >= end


def validate_growth(report, step):
    periods = growth_periods(step)
    intervals = coverage(report)
    for key, (start, end) in periods.items():
        if not period_covered(intervals, start, end):
            raise QueryUnavailable(
                f"{LABELS[key]} {start} 至 {end - timedelta(days=1)} 的完整出库数据未覆盖；"
                "请生成含对应历史分区及规格单位的新快照，不能用缺失数据计算零增长。"
            )
    if step.dimension == "buyer":
        products = {
            f.code for f in step.filters if f.field == "product" and f.operator != "exclude"
        }
        if len(products) != 1:
            raise QueryUnavailable("客户贡献需要先明确一个品种；请补充商品名称或编码。")
    return periods


def selected_lines(report, step, start, end):
    tz = ZoneInfo(report.operating.policy.business_timezone)
    for part in partitions(report):
        if part.end_exclusive <= start or part.start >= end:
            continue
        for doc in part.documents:
            if not start <= doc.occurred_at.astimezone(tz).date() < end:
                continue
            if not matches(step.filters, "buyer", doc.buyer_code):
                continue
            for line in doc.lines:
                if matches(step.filters, "product", line.product_code):
                    included = [
                        f for f in step.filters if f.field == "product" and f.operator != "exclude"
                    ]
                    if included and not any(
                        f.code == line.product_code
                        and (
                            f.variant is None
                            or (f.variant.specification, f.variant.manufacturer, f.variant.unit)
                            == (line.specification, line.manufacturer, line.unit)
                        )
                        for f in included
                    ):
                        continue
                    if not line.specification or not line.manufacturer:
                        raise QueryUnavailable("出库明细缺少规格或厂家，暂不能证明品种跨期可比。")
                    yield doc, line


def _groups(report, step, window):
    result = {}
    for doc, line in selected_lines(report, step, *window):
        identity = (line.product_code, line.specification, line.manufacturer, line.unit)
        code = doc.buyer_code if step.dimension == "buyer" else line.product_code
        key = (*identity, code)
        row = result.setdefault(
            key,
            dict(
                code=code,
                name=(doc.buyer_name or code)
                if step.dimension == "buyer"
                else (line.product_name or code),
                product_code=line.product_code,
                specification=line.specification,
                manufacturer=line.manufacturer,
                unit=line.unit,
                amount=ZERO,
                quantity=ZERO,
            ),
        )
        row["amount"] += line.amount
        row["quantity"] += line.quantity
    return result


def execute_growth(report, step, *, full=False):
    periods = validate_growth(report, step)
    groups = {key: _groups(report, step, window) for key, window in periods.items()}
    current = groups["current"]
    output, summaries, notes = (
        [],
        [],
        [
            "销售出库口径：按已确认售出单确认日期；未扣退货，不是终端销售或回款。",
            "按当前商品范围分析，未按药品类别筛选，可能包含其他商品。",
            "同编码不同规格、厂家或单位分组比较；变化是出库构成的算术解释，不代表因果。",
            "比较期为零时不计算增长率，不据此判断新品或新客户。",
            "完整月份对上一自然月；其他区间对紧邻等长区间，同比按去年相同月日，闰日截齐。",
        ],
    )
    current_days = Decimal((periods["current"][1] - periods["current"][0]).days)
    for basis, baseline in groups.items():
        if basis == "current":
            continue
        old_days = Decimal((periods[basis][1] - periods[basis][0]).days)
        current_codes = {k[0] for k in current}
        baseline_codes = {k[0] for k in baseline}
        current_variants = {k[:4] for k in current}
        baseline_variants = {k[:4] for k in baseline}
        buckets = defaultdict(list)
        rows = []
        for key in sorted(current.keys() | baseline.keys()):
            n, b = current.get(key, {}), baseline.get(key, {})
            identity = n or b
            amount, before_amount = n.get("amount", ZERO), b.get("amount", ZERO)
            qty, before_qty = n.get("quantity", ZERO), b.get("quantity", ZERO)
            value, before = (
                (amount, before_amount) if step.metric == "amount" else (qty, before_qty)
            )
            delta = value - before
            changed_variant = (key[:4] not in current_variants and key[0] in current_codes) or (
                key[:4] not in baseline_variants and key[0] in baseline_codes
            )
            state = "增长" if delta > 0 else "下降" if delta < 0 else "持平"
            row = {k: v for k, v in identity.items() if k not in {"amount", "quantity"}}
            row.update(
                basis=basis,
                comparison=LABELS[basis],
                direction=state,
                current_amount=number(amount),
                previous_amount=number(before_amount),
                current_quantity=number(qty),
                previous_quantity=number(before_qty),
                delta=number(delta),
                change_rate=str(delta / before) if before and not changed_variant else None,
                change_percent="不可比"
                if changed_variant
                else (f"{delta / before * 100:.2f}%" if before else "无基数"),
                comparability="另期有同编码其他规格／厂家／单位，仅展示分组增减"
                if changed_variant
                else "同一品种规格单位",
                amount_delta=number(amount - before_amount),
                quantity_delta=number(qty - before_qty),
                daily_current=number(value / current_days),
                daily_previous=number(before / old_days),
                record_state="比较期无出库" if not b else "本期无出库" if not n else "两期有出库",
            )
            rows.append(row)
            buckets[line_unit(identity, step)].append(row)
        for unit, members in sorted(buckets.items()):
            positive = sum((Decimal(r["delta"]) for r in members if Decimal(r["delta"]) > 0), ZERO)
            negative = sum((-Decimal(r["delta"]) for r in members if Decimal(r["delta"]) < 0), ZERO)
            summary = dict(
                basis=basis,
                unit=unit,
                positive=number(positive),
                negative=number(negative),
                delta=number(positive - negative),
                groups=len(members),
            )
            for field in (
                "current_amount",
                "previous_amount",
                "current_quantity",
                "previous_quantity",
            ):
                if "quantity" in field and step.metric == "amount":
                    continue
                summary[field] = number(sum((Decimal(r[field]) for r in members), ZERO))
            for direction, sign in (("increase", 1), ("decrease", -1)):
                candidates = [r for r in members if Decimal(r["delta"]) * sign > 0]
                if step.growth_sort == "rate":
                    candidates = [r for r in candidates if r["change_rate"] is not None]
                candidates.sort(
                    key=lambda r: (
                        -abs(
                            Decimal(r["change_rate"] if step.growth_sort == "rate" else r["delta"])
                        ),
                        r["code"],
                        r["specification"],
                        r["manufacturer"],
                    )
                )
                shown = (
                    (candidates if full else candidates[: step.top_n])
                    if (step.growth_direction in {"both", direction})
                    else []
                )
                subtotal = sum((Decimal(r["delta"]) for r in shown), ZERO)
                summary[f"{direction}_shown"] = number(subtotal)
                summary[f"{direction}_other"] = number(
                    (positive if sign == 1 else -negative) - subtotal
                )
                for row in shown:
                    denominator = positive if sign == 1 else negative
                    row["contribution_percent"] = (
                        f"{abs(Decimal(row['delta'])) / denominator * 100:.2f}%"
                    )
                    row["contribution_basis"] = "占增加部分" if sign == 1 else "占减少部分"
                output.extend(shown)
            summary["unchanged"] = sum(Decimal(r["delta"]) == 0 for r in members)
            summaries.append(summary)
        if not rows:
            summaries.append(dict(basis=basis, unit="", groups=0, delta="0.0000"))
    columns = dict(
        comparison="比较基准",
        direction="变化方向",
        name="客户" if step.dimension == "buyer" else "品种",
        code="编码",
        specification="规格",
        manufacturer="厂家",
        unit="单位",
        current_amount="本期金额",
        previous_amount="比较期金额",
        current_quantity="本期数量",
        previous_quantity="比较期数量",
        delta="金额差额" if step.metric == "amount" else "数量差额",
        change_percent="变化率",
        record_state="记录情况",
        comparability="可比性说明",
        daily_current="本期日均",
        daily_previous="比较期日均",
        contribution_percent="贡献占比",
        contribution_basis="贡献分母",
    )
    findings = []
    for key, (start, end) in periods.items():
        findings.append(
            f"{LABELS[key]}：{start} 至 {end - timedelta(days=1)}，共{(end - start).days}天。"
        )
    for total in summaries:
        metric = "amount" if step.metric == "amount" else "quantity"
        findings.append(
            f"比较{LABELS[total['basis']]}（{total['unit'] or '金额'}）："
            f"本期{total.get('current_' + metric, '0')}，"
            f"比较期{total.get('previous_' + metric, '0')}，"
            f"净变化{total['delta']}；"
            f"其余增长{total.get('increase_other', '0')}，"
            f"其余下降{total.get('decrease_other', '0')}，"
            f"持平{total.get('unchanged', 0)}组。"
        )
    if not output:
        findings.append("当前范围没有符合排序条件的增长或下降记录；持平与零基数规则见汇总。")
    notes.append(
        "当前指标、比较基准、方向和排序条件下的全量变化明细；持平仅计入汇总。"
        if full
        else (
            f"各比较基准、单位和方向最多展示{step.top_n}项；"
            "其余变化保留在汇总，可下载完整变化明细。"
        )
    )
    if step.growth_sort == "rate":
        notes.append("变化率排序排除零基数和不可比记录，未列出的差额保留在其余变化中。")
    return AnalysisResult(
        domain="shipping",
        kind="growth",
        title="品种内客户贡献" if step.dimension == "buyer" else "品种增长与下降",
        columns=columns,
        rows=output,
        findings=findings,
        notes=notes,
        totals={
            "periods": {
                k: {"start": s.isoformat(), "end_exclusive": e.isoformat()}
                for k, (s, e) in periods.items()
            },
            "groups": summaries,
        },
    )


def line_unit(identity, step):
    return identity["unit"] if step.metric == "quantity" else ""
