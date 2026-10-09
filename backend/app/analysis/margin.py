"""Purchase-cost gross profit and an explicit, reconciling volume/price/cost bridge."""

from datetime import timedelta
from decimal import Decimal

from app.analysis.costs import validate_margin
from app.analysis.growth import LABELS
from app.analysis.price import _groups, _price, number
from app.schemas.analytics import AnalysisResult

ZERO = Decimal(0)


def execute_margin(report, step, *, full=False):
    periods = validate_margin(report, step)
    groups = {key: _groups(report, step, window) for key, window in periods.items()}
    current = groups["current"]
    output, summaries = [], []
    for basis, before in groups.items():
        if basis == "current":
            continue
        rows = []
        for key in sorted(current.keys() | before.keys()):
            now, old = current.get(key), before.get(key)
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
            amount, prior_amount = (g["amount"] if g else ZERO for g in (now, old))
            cost, prior_cost = (g["cost"] if g else ZERO for g in (now, old))
            quantity, prior_quantity = (g["quantity"] if g else ZERO for g in (now, old))
            profit, prior_profit = amount - cost, prior_amount - prior_cost
            delta = profit - prior_profit
            price, prior_price = _price(now), _price(old)
            unit_cost = cost / quantity if quantity else None
            prior_unit_cost = prior_cost / prior_quantity if prior_quantity else None
            effects = dict(
                volume_effect=None, price_effect=None, cost_effect=None, rounding_adjustment=None
            )
            comparable = price is not None and prior_price is not None
            if comparable:
                effects.update(
                    volume_effect=number(
                        (quantity - prior_quantity) * (prior_price - prior_unit_cost)
                    ),
                    price_effect=number(quantity * (price - prior_price)),
                    cost_effect=number(-quantity * (unit_cost - prior_unit_cost)),
                )
                effects["rounding_adjustment"] = number(
                    delta
                    - sum(Decimal(v) for k, v in effects.items() if k != "rounding_adjustment")
                )
            rows.append(
                dict(
                    **identity,
                    basis=basis,
                    comparison=LABELS[basis],
                    direction="增长" if delta > 0 else "下降" if delta < 0 else "持平",
                    current_amount=number(amount),
                    previous_amount=number(prior_amount),
                    current_cost=number(cost),
                    previous_cost=number(prior_cost),
                    current_quantity=number(quantity),
                    previous_quantity=number(prior_quantity),
                    current_price=number(price),
                    previous_price=number(prior_price),
                    current_unit_cost=number(unit_cost),
                    previous_unit_cost=number(prior_unit_cost),
                    current_profit=number(profit),
                    previous_profit=number(prior_profit),
                    delta=number(delta),
                    current_margin=number(profit / amount) if amount else None,
                    previous_margin=number(prior_profit / prior_amount) if prior_amount else None,
                    current_margin_percent=f"{profit / amount * 100:.2f}%" if amount else None,
                    previous_margin_percent=f"{prior_profit / prior_amount * 100:.2f}%"
                    if prior_amount
                    else None,
                    change_rate=number(delta / prior_profit) if prior_profit > 0 else None,
                    change_percent=f"{delta / prior_profit * 100:.2f}%"
                    if prior_profit > 0
                    else None,
                    volume_up_profit_not_up=quantity > prior_quantity and delta <= 0,
                    record_state="三因素可拆解"
                    if comparable
                    else "单期无出库或数量异常，仅比较毛利额",
                    **effects,
                )
            )
        totals = {
            field: number(sum((Decimal(r[field]) for r in rows), ZERO))
            for field in (
                "current_amount",
                "previous_amount",
                "current_cost",
                "previous_cost",
                "current_profit",
                "previous_profit",
                "delta",
            )
        }
        summary = dict(
            basis=basis,
            total=len(rows),
            attention=sum(r["volume_up_profit_not_up"] for r in rows),
            **totals,
        )
        for key in ("current", "previous"):
            summary[key + "_margin"] = (
                number(Decimal(summary[key + "_profit"]) / Decimal(summary[key + "_amount"]))
                if Decimal(summary[key + "_amount"])
                else None
            )
        selected = []
        for direction, option in (("下降", "decrease"), ("增长", "increase"), ("持平", "both")):
            if step.growth_direction not in {"both", option}:
                continue
            candidates = [r for r in rows if r["direction"] == direction]
            field = "change_rate" if step.growth_sort == "rate" else "delta"
            if step.growth_sort == "rate":
                candidates = [r for r in candidates if r[field] is not None]
            candidates.sort(
                key=lambda r: (
                    -abs(Decimal(r[field])),
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
        output.extend(selected)
    return AnalysisResult(
        domain="shipping",
        kind="margin",
        title="客户销量与毛利" if step.dimension == "buyer" else "品种销量与毛利",
        columns=dict(
            name="客户" if step.dimension == "buyer" else "品种",
            comparison="比较基准",
            unit="单位",
            current_quantity="本期数量",
            previous_quantity="比较期数量",
            current_amount="本期收入",
            current_cost="本期采购成本",
            current_profit="本期毛利",
            previous_profit="比较期毛利",
            delta="毛利变化",
            volume_effect="销量影响",
            price_effect="售价影响",
            cost_effect="成本影响",
            rounding_adjustment="舍入调整",
            record_state="拆解说明",
        ),
        rows=output,
        totals=dict(
            groups=summaries,
            periods={
                k: dict(start=a.isoformat(), end_exclusive=b.isoformat())
                for k, (a, b) in periods.items()
            },
        ),
        findings=[f"{LABELS[k]}：{a} 至 {b - timedelta(days=1)}。" for k, (a, b) in periods.items()]
        + [
            f"较{LABELS[s['basis']]}：本期采购成本口径毛利{s['current_profit']}，变化{s['delta']}；销量增加而毛利未增{s['attention']}组。"
            for s in summaries
        ],
        notes=[
            "已确认销售出库金额减去出库行保存采购单价×数量。未扣退货、税额未拆分，仅为采购成本口径毛利，不等于财务净利润。",
            "分解顺序为先销量、再售价、再成本：销量影响=数量差×比较期单位毛利；售价影响=本期数量×均价差；成本影响=负本期数量×单位成本差。",
            "三因素及舍入调整之和等于毛利变化。这是算术分解，可能包含客户或批次结构影响，不是调价或经营行为的因果证明。",
            "同编码、规格、厂家和单位分别比较，不跨单位累计数量；单期无出库或数量异常不拆解三因素。完整覆盖下无出库的期间毛利为零，缺失成本不会补零。",
            "收入为零时毛利率无定义；比较期毛利小于或等于零时不计算毛利增长率。不同天数的期间毛利总额也受时间长度影响。",
            f"概览覆盖全部{len(groups['current'])}个本期分组；每个比较基准各方向最多展示{step.top_n}组，完整明细可下载。"
            if not full
            else "完整导出保留当前方向和排序条件。",
        ],
    )
