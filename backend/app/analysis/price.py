"""Quantity-weighted dispatch prices. No inferred costs, repricing or net revenue."""

from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.analysis.costs import cost_coverage, group_cost
from app.analysis.growth import LABELS, period_covered, selected_lines, validate_growth
from app.schemas.analytics import AnalysisResult

ZERO = Decimal(0)


def number(value):
    return (
        None
        if value is None
        else format(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP), "f")
    )


def _groups(report, step, window):
    groups = {}
    for doc, line in selected_lines(report, step, *window):
        identity = (line.product_code, line.specification, line.manufacturer, line.unit)
        code = doc.buyer_code if step.dimension == "buyer" else line.product_code
        row = groups.setdefault(
            (*identity, code),
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
                cost=ZERO,
                cost_known=True,
                quantity=ZERO,
                invalid=0,
                customers=set(),
            ),
        )
        row["amount"] += line.amount
        if line.purchase_unit_cost is None:
            row["cost_known"] = False
        else:
            row["cost"] += line.purchase_unit_cost * line.quantity
        row["quantity"] += line.quantity
        row["invalid"] += int(line.quantity == 0 and line.amount != 0)
        row["customers"].add(doc.buyer_code)
    return groups


def _price(group):
    if not group or group["invalid"] or group["quantity"] == 0:
        return None
    return group["amount"] / group["quantity"]


def execute_price(report, step, *, full=False):
    periods = validate_growth(report, step)
    groups = {key: _groups(report, step, window) for key, window in periods.items()}
    current = groups["current"]
    intervals = cost_coverage(report)
    cost_periods = {key: period_covered(intervals, *window) for key, window in periods.items()}
    output, summaries = [], []
    for basis, before in groups.items():
        if basis == "current":
            continue
        rows = []
        for key in sorted(current.keys() | before.keys()):
            now, old = current.get(key), before.get(key)
            price, previous = _price(now), _price(old)
            current_cost = group_cost(now, cost_periods["current"])
            previous_cost = group_cost(old, cost_periods[basis])
            unit_cost = (
                current_cost / now["quantity"]
                if current_cost is not None and now and now["quantity"]
                else None
            )
            old_unit_cost = (
                previous_cost / old["quantity"]
                if previous_cost is not None and old and old["quantity"]
                else None
            )
            spread = price - unit_cost if price is not None and unit_cost is not None else None
            old_spread = (
                previous - old_unit_cost
                if previous is not None and old_unit_cost is not None
                else None
            )
            delta = price - previous if price is not None and previous is not None else None
            rate = delta / previous if delta is not None and previous != 0 else None
            state = "可比较"
            if any(g and g["invalid"] for g in (now, old)):
                state = "存在零数量非零金额，均价待核实"
            elif now is None:
                state = "本期无出库，无法比较售价"
            elif old is None:
                state = "比较期无同规格出库，无法比较售价"
            elif price is None or previous is None:
                state = "出库数量为零，无法计算均价"
            direction = (
                "不可比"
                if delta is None
                else "下降"
                if delta < 0
                else "上涨"
                if delta > 0
                else "持平"
            )
            identity = {
                field: (now or old)[field]
                for field in (
                    "code",
                    "name",
                    "product_code",
                    "specification",
                    "manufacturer",
                    "unit",
                )
            }
            rows.append(
                dict(
                    **identity,
                    basis=basis,
                    comparison=LABELS[basis],
                    direction=direction,
                    current_price=number(price),
                    previous_price=number(previous),
                    delta=number(delta),
                    change_rate=number(rate),
                    change_percent=f"{rate * 100:.2f}%" if rate is not None else None,
                    current_amount=number(now["amount"] if now else ZERO),
                    previous_amount=number(old["amount"] if old else ZERO),
                    current_quantity=number(now["quantity"] if now else ZERO),
                    previous_quantity=number(old["quantity"] if old else ZERO),
                    current_customers=len(now["customers"]) if now else 0,
                    previous_customers=len(old["customers"]) if old else 0,
                    common_customers=len(now["customers"] & old["customers"]) if now and old else 0,
                    record_state=state,
                    current_unit_cost=number(unit_cost),
                    previous_unit_cost=number(old_unit_cost),
                    current_spread=number(spread),
                    previous_spread=number(old_spread),
                    spread_delta=number(spread - old_spread)
                    if spread is not None and old_spread is not None
                    else None,
                    cost_status="出库行采购成本口径，未扣退货、税额未拆分"
                    if current_cost is not None and previous_cost is not None
                    else "成本数据未覆盖，暂不计算利润空间",
                    _delta=delta,
                    _rate=rate,
                )
            )
        summary = dict(
            basis=basis,
            total=len(rows),
            **{
                name: sum(r["direction"] == direction for r in rows)
                for name, direction in (
                    ("decrease", "下降"),
                    ("increase", "上涨"),
                    ("unchanged", "持平"),
                    ("unavailable", "不可比"),
                )
            },
        )
        selected = []
        for direction, option in (
            ("下降", "decrease"),
            ("上涨", "increase"),
            ("持平", "both"),
            ("不可比", "both"),
        ):
            if step.growth_direction not in {"both", option}:
                continue
            candidates = [r for r in rows if r["direction"] == direction]
            if step.growth_sort == "rate":
                candidates = [r for r in candidates if r["_rate"] is not None]
            field = "_rate" if step.growth_sort == "rate" else "_delta"
            candidates.sort(
                key=lambda r: (
                    -abs(r[field] or ZERO),
                    r["product_code"],
                    r["specification"],
                    r["manufacturer"],
                    r["unit"],
                    r["code"],
                )
            )
            selected.extend(candidates if full else candidates[: step.top_n])
        summary["shown"] = len(selected)
        summaries.append(summary)
        output.extend({k: v for k, v in r.items() if not k.startswith("_")} for r in selected)
    return AnalysisResult(
        domain="shipping",
        kind="price",
        title="客户售价变化" if step.dimension == "buyer" else "品种售价变化",
        columns=dict(
            comparison="比较基准",
            name="客户" if step.dimension == "buyer" else "品种",
            specification="规格",
            manufacturer="厂家",
            unit="单位",
            direction="均价方向",
            previous_price="比较期均价",
            current_price="本期均价",
            delta="每单位价差",
            change_percent="变化率",
            previous_quantity="比较期数量",
            current_quantity="本期数量",
            record_state="可比说明",
            current_unit_cost="本期单位采购成本",
            previous_unit_cost="比较期单位采购成本",
            current_spread="本期单位毛利空间",
            previous_spread="比较期单位毛利空间",
            spread_delta="单位毛利空间变化",
        ),
        rows=output,
        totals=dict(
            groups=summaries,
            periods={
                key: dict(start=a.isoformat(), end_exclusive=b.isoformat())
                for key, (a, b) in periods.items()
            },
        ),
        findings=[
            f"{LABELS[key]}：{a} 至 {b - timedelta(days=1)}。" for key, (a, b) in periods.items()
        ]
        + [
            f"较{LABELS[s['basis']]}：均价下降{s['decrease']}组、上涨{s['increase']}组、持平{s['unchanged']}组，另有{s['unavailable']}组不可比较。"
            for s in summaries
        ],
        notes=[
            "已确认销售出库，按确认日期；加权平均售价＝出库金额合计÷出库数量合计。沿用ERP金额，未扣退货、税额未拆分。",
            "按商品编码、规格、厂家和单位分别计算，不跨品种或单位汇总均价。未按药品类别筛选。",
            "均价受客户采购占比、批次与成交结构影响；均价变化不等于实际调价，客户明细也仅反映该客户的加权均价。",
            "缺失比较期不会当作零价；数量为零不能计算均价，比较期均价为零不计算变化率。",
            f"概览计数覆盖全部分组；当前方向和排序下，每个比较基准的下降、上涨、持平及不可比各最多显示{step.top_n}组。变化率排序排除无有效变化率的分组。"
            if not full
            else "导出覆盖当前方向和排序条件下的所有分组。",
            "成本采用出库行保存的采购单价×数量；单位毛利空间为均价减去单位采购成本，未扣退货、税额未拆分，不等于净利润。缺成本不按零计算。",
        ],
    )
