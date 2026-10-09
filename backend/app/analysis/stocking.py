"""Read-only seasonal references and explicit, per-variant stocking scenarios."""

import calendar
from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.analysis.growth import coverage, partitions, period_covered
from app.analysis.inventory_risk import validate_inventory_risk
from app.analysis.object_filters import matches
from app.analysis.price import number
from app.analysis.sales_query import QueryUnavailable
from app.schemas.analytics import AnalysisResult

ZERO = Decimal(0)


def last_year(day):
    return date(
        day.year - 1, day.month, min(day.day, calendar.monthrange(day.year - 1, day.month)[1])
    )


def validate_stocking(report, step):
    facts = report.operations.inventory if report.operations else None
    if not facts:
        raise QueryUnavailable("旺季备货需要库存快照。")
    day = facts.as_of.astimezone(ZoneInfo(report.operating.policy.business_timezone)).date()
    day, recent_start = validate_inventory_risk(
        report,
        step.model_copy(update={"start_date": day, "end_date_exclusive": day + timedelta(days=1)}),
    )
    if not day < step.start_date <= day + timedelta(days=365):
        raise QueryUnavailable("备货目标开始日须晚于库存快照日期，且在其后365天内。")
    previous_start = last_year(step.start_date)
    previous_end = last_year(step.end_date_exclusive - timedelta(days=1)) + timedelta(days=1)
    if not period_covered(coverage(report), previous_start, previous_end):
        raise QueryUnavailable(
            f"去年同期 {previous_start} 至 {previous_end - timedelta(days=1)} "
            "完整出库数据未覆盖，不能当作零需求。"
        )
    return day, recent_start, previous_start, previous_end


def references(report, step):
    day, recent_start, previous_start, previous_end = validate_stocking(report, step)
    groups = {}

    def group(record):
        key = (record.product_code, record.specification, record.manufacturer, record.unit)
        return groups.setdefault(
            key,
            dict(
                identity=key,
                name=record.product_name or record.product_code,
                previous=ZERO,
                recent=ZERO,
                stock=ZERO,
                eligible=ZERO,
                excluded=ZERO,
                unknown_expiry=ZERO,
                record_ids=[],
                document_ids=set(),
            ),
        )

    tz = ZoneInfo(report.operating.policy.business_timezone)
    for part in partitions(report):
        for doc in part.documents:
            occurred = doc.occurred_at.astimezone(tz).date()
            period = (
                "previous"
                if previous_start <= occurred < previous_end
                else ("recent" if recent_start <= occurred < day else None)
            )
            if period is None:
                continue
            for line in doc.lines:
                if matches(step.filters, "product", line.product_code):
                    item = group(line)
                    item[period] += line.quantity
                    item["document_ids"].add(doc.document_id)
    for record in report.operations.inventory.records:
        if record.quantity <= 0 or not matches(step.filters, "product", record.product_code):
            continue
        item = group(record)
        item["stock"] += record.quantity
        item["record_ids"].append(record.record_id)
        if record.expiry_date is None:
            item["unknown_expiry"] += record.quantity
        elif record.expiry_date >= step.end_date_exclusive - timedelta(days=1):
            item["eligible"] += record.quantity
        else:
            item["excluded"] += record.quantity
    days = Decimal((step.end_date_exclusive - step.start_date).days)
    historical_days = Decimal((previous_end - previous_start).days)
    for item in groups.values():
        item["daily"] = item["recent"] / Decimal(step.lookback_days)
        item["baseline"] = item["previous"] / historical_days * days
        item["recent_reference"] = item["daily"] * days
        item["valid_identity"] = all(item["identity"])
    return (day, recent_start, previous_start, previous_end), groups


def execute_stocking(report, step, *, full=False):
    dates, groups = references(report, step)
    day, recent_start, previous_start, previous_end = dates
    rows = []
    for identity, item in sorted(groups.items(), key=lambda pair: str(pair[0])):
        code, specification, manufacturer, unit = identity
        rows.append(
            dict(
                code=code,
                name=item["name"],
                specification=specification,
                manufacturer=manufacturer,
                unit=unit,
                previous_quantity=number(item["previous"]),
                recent_quantity=number(item["recent"]),
                baseline_demand=number(item["baseline"]),
                recent_reference=number(item["recent_reference"]),
                daily_quantity=number(item["daily"]),
                stock_quantity=number(item["stock"]),
                eligible_stock=number(item["eligible"]),
                excluded_stock=number(item["excluded"]),
                unknown_expiry_stock=number(item["unknown_expiry"]),
                scenario_ready=item["valid_identity"] and item["unknown_expiry"] == 0,
                needs_manual_demand=item["baseline"] == 0,
                record_ids=item["record_ids"],
                document_ids=sorted(item["document_ids"]),
            )
        )
    return AnalysisResult(
        domain="inventory",
        kind="stocking",
        title="旺季备货情景测算",
        columns=dict(
            name="品种",
            unit="单位",
            previous_quantity="去年同期出库量",
            recent_quantity="近期出库量",
            stock_quantity="快照库存",
            eligible_stock="效期覆盖目标期库存",
        ),
        rows=rows if full else rows[: step.top_n],
        totals=dict(
            product_count=len(rows),
            snapshot_date=str(day),
            target_start=str(step.start_date),
            target_end_exclusive=str(step.end_date_exclusive),
            previous_start=str(previous_start),
            previous_end_exclusive=str(previous_end),
            recent_start=str(recent_start),
            recent_end_exclusive=str(day),
        ),
        findings=[f"共{len(rows)}个品种规格组合。选择具体品种，手动填写在途数量及供货周期后测算。"],
        notes=[
            "按商品编码、规格、厂家和单位分别测算；销量为已确认销售出库，未扣退货。不同单位不合计。",
            "默认目标需求沿用去年同期日均出库量×目标天数；近期日均×目标天数仅供对照，不自动推断增长。去年同期零出库需手动填写需求。",
            "库存来自备份时点。为保守估算，仅计入效期覆盖整个目标期间的批次；期内到期或更早到期库存排除，效期缺失先核实。",
            "目标开始前消耗按近期日均×快照至目标开始日天数估算，并从上述库存扣除。新在途视为独立于快照库存、效期覆盖目标期且专用于目标期间。",
            "手填在途只计入预计在目标开始日或之前到货的数量；晚到货单列，不抵减期初缺口。供货周期用于倒推最晚下单日。",
            "测算未考虑预留、锁库、安全库存、包装取整或供应商起订量，输出为情景缺口，不自动下单。完整明细可下载。",
        ],
    )


def calculate_scenario(report, body):
    dates, groups = references(report, body.step)
    key = (
        body.product_code,
        body.variant.specification,
        body.variant.manufacturer,
        body.variant.unit,
    )
    item = groups.get(key)
    if item is None or not item["valid_identity"]:
        raise QueryUnavailable("品种规格不在当前分析范围内或资料不完整。")
    if item["unknown_expiry"]:
        raise QueryUnavailable("该品种存在效期缺失库存，请先核实再测算。")
    if body.expected_demand is None and item["baseline"] == 0:
        raise QueryUnavailable("去年同期无出库，请手动填写目标需求，不能直接推定需求为零。")
    day = dates[0]
    if body.in_transit_quantity > 0 and (
        body.expected_arrival_date is None or body.expected_arrival_date < day
    ):
        raise QueryUnavailable("在途数量大于零时，请填写不早于库存快照日的预计到货日期。")
    demand = body.expected_demand if body.expected_demand is not None else item["baseline"]
    before = item["daily"] * Decimal((body.step.start_date - day).days)
    projected = max(ZERO, item["eligible"] - before)
    counted = (
        body.in_transit_quantity
        if (
            body.expected_arrival_date is not None
            and body.expected_arrival_date <= body.step.start_date
        )
        else ZERO
    )
    latest = body.step.start_date - timedelta(days=body.lead_time_days)
    return dict(
        product_code=body.product_code,
        variant=body.variant.model_dump(),
        snapshot_date=str(day),
        target_start=str(body.step.start_date),
        target_end_exclusive=str(body.step.end_date_exclusive),
        expected_demand=number(demand),
        demand_source="手动填写" if body.expected_demand is not None else "去年同期日均折算",
        eligible_stock=number(item["eligible"]),
        excluded_stock=number(item["excluded"]),
        consumption_before_target=number(before),
        projected_start_stock=number(projected),
        in_transit_quantity=number(body.in_transit_quantity),
        counted_in_transit=number(counted),
        late_in_transit=number(body.in_transit_quantity - counted),
        expected_arrival_date=body.expected_arrival_date,
        lead_time_days=body.lead_time_days,
        latest_order_date=str(latest),
        lead_time_tight=latest < day,
        estimated_gap=number(max(ZERO, demand - projected - counted)),
        assumptions=execute_stocking(report, body.step).notes,
    )
