"""Snapshot stock age and FEFO estimates over complete, comparable dispatch coverage."""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.analysis.growth import coverage, partitions, period_covered
from app.analysis.object_filters import matches
from app.analysis.price import number
from app.analysis.sales_query import QueryUnavailable
from app.schemas.analytics import AnalysisResult

ZERO = Decimal(0)


def validate_inventory_risk(report, step):
    facts = report.operations.inventory if report.operations else None
    if not report.metadata.scope.all_buyers or not facts or not facts.risk_ready:
        raise QueryUnavailable("库存积压需要全平台授权和包含库龄、效期、采购成本的新快照。")
    day = facts.as_of.astimezone(ZoneInfo(report.operating.policy.business_timezone)).date()
    if (step.start_date, step.end_date_exclusive) != (day, day + timedelta(days=1)):
        raise QueryUnavailable(f"库存只能分析 {day} 的备份时点，不能使用今天代替历史库存时点。")
    start = day - timedelta(days=step.lookback_days)
    if not period_covered(coverage(report), start, day):
        raise QueryUnavailable(
            f"库存销售速度所需的 {start} 至 {day - timedelta(days=1)} 完整出库数据未覆盖。"
        )
    return day, start


def execute_inventory_risk(report, step, *, full=False):
    day, start = validate_inventory_risk(report, step)
    quantities, variants = defaultdict(Decimal), defaultdict(set)
    tz = ZoneInfo(report.operating.policy.business_timezone)
    for part in partitions(report):
        for doc in part.documents:
            if not start <= doc.occurred_at.astimezone(tz).date() < day:
                continue
            for line in doc.lines:
                identity = (line.product_code, line.specification, line.manufacturer, line.unit)
                quantities[identity] += line.quantity
                variants[line.product_code].add(identity)
    batches = {}
    for record in report.operations.inventory.records:
        if record.quantity <= 0 or not matches(step.filters, "product", record.product_code):
            continue
        identity = (record.product_code, record.specification, record.manufacturer, record.unit)
        key = (
            identity,
            record.batch_code or record.record_id,
            record.received_date,
            record.expiry_date,
            record.purchase_unit_cost,
        )
        group = batches.setdefault(
            key,
            dict(
                identity=identity,
                name=record.product_name or record.product_code,
                batch=record.batch_code,
                quantity=ZERO,
                received=record.received_date,
                expiry=record.expiry_date,
                unit_cost=record.purchase_unit_cost,
                record_ids=[],
            ),
        )
        group["quantity"] += record.quantity
        group["record_ids"].append(record.record_id)
    by_product = defaultdict(list)
    for batch in batches.values():
        by_product[batch["identity"]].append(batch)
    rows = []
    for identity, group in sorted(by_product.items(), key=lambda item: str(item[0])):
        code, specification, manufacturer, unit = identity
        identity_valid = bool(specification and manufacturer)
        mismatch = identity not in quantities and bool(variants[code])
        sales = quantities[identity] if identity_valid and not mismatch else None
        daily = sales / Decimal(step.lookback_days) if sales is not None else None
        missing_expiry = any(b["expiry"] is None for b in group)
        cumulative = ZERO
        saleable_quantity = sum(
            (b["quantity"] for b in group if b["expiry"] and b["expiry"] >= day), ZERO
        )
        stock_days = saleable_quantity / daily if daily and not missing_expiry else None
        for batch in sorted(
            group,
            key=lambda b: (
                b["expiry"] or date.max,
                b["received"] or date.max,
                str(b["batch"]),
                b["record_ids"][0],
            ),
        ):
            expired = batch["expiry"] is not None and batch["expiry"] < day
            if not expired:
                cumulative += batch["quantity"]
            clear_days = (
                cumulative / daily if daily and not expired and not missing_expiry else None
            )
            remaining = (batch["expiry"] - day).days if batch["expiry"] else None
            age = (
                (day - batch["received"]).days
                if batch["received"] and batch["received"] <= day
                else None
            )
            aged = age is not None and age >= step.age_threshold_days
            near = remaining is not None and 0 <= remaining <= step.expiry_threshold_days
            no_sales = sales == 0
            expiry_mismatch = (
                remaining is not None and clear_days is not None and clear_days > max(remaining, 0)
            )
            issues = []
            if age is None:
                issues.append("入库日期缺失或晚于快照")
            if missing_expiry:
                issues.append("同品种存在效期缺失批次，暂停售完估算")
            if not identity_valid or mismatch:
                issues.append("规格厂家或历史可比性不足")
            if batch["unit_cost"] is None:
                issues.append("采购成本缺失")
            if not batch["batch"]:
                issues.append("批号缺失")
            occupied = (
                batch["quantity"] * batch["unit_cost"] if batch["unit_cost"] is not None else None
            )
            tags = [
                label
                for flag, label in (
                    (expired, "已过期"),
                    (remaining == 0, "今日到期"),
                    (near, "临期"),
                    (aged, "库龄较长"),
                    (no_sales, "近期无出库"),
                    (expiry_mismatch, "预计售完晚于到期"),
                    (bool(issues), "资料待核实"),
                )
                if flag
            ]
            rows.append(
                dict(
                    code=code,
                    name=batch["name"],
                    specification=specification,
                    manufacturer=manufacturer,
                    unit=unit,
                    batch_code=batch["batch"],
                    quantity=number(batch["quantity"]),
                    received_date=str(batch["received"]) if batch["received"] else None,
                    expiry_date=str(batch["expiry"]) if batch["expiry"] else None,
                    age_days=age,
                    expiry_remaining_days=remaining,
                    purchase_unit_cost=number(batch["unit_cost"]),
                    occupied_amount=number(occupied),
                    recent_quantity=number(sales),
                    daily_quantity=number(daily),
                    product_stock_days=number(stock_days),
                    estimated_clear_days=number(clear_days),
                    aged=aged,
                    near_expiry=near,
                    no_recent_sales=no_sales,
                    expired=expired,
                    expiry_mismatch=expiry_mismatch,
                    data_issue=bool(issues),
                    risk_tags="、".join(tags) or "正常观察",
                    data_note="；".join(issues),
                    source_ids=", ".join(f"inventory:{i}" for i in batch["record_ids"]),
                    record_ids=batch["record_ids"],
                )
            )
    rows.sort(
        key=lambda r: (
            not r["expired"],
            not r["expiry_mismatch"],
            not r["near_expiry"],
            not r["no_recent_sales"],
            not r["aged"],
            -(r["age_days"] or 0),
            r["code"],
            r["batch_code"],
            r["record_ids"][0],
        )
    )
    unit_totals = defaultdict(Decimal)
    for row in rows:
        unit_totals[row["unit"]] += Decimal(row["quantity"])
    known = sum(
        (Decimal(row["occupied_amount"]) for row in rows if row["occupied_amount"] is not None),
        ZERO,
    )
    unknown = sum(row["occupied_amount"] is None for row in rows)
    totals = dict(
        batch_count=len(rows),
        known_occupied_amount=number(known),
        occupied_amount=None if unknown else number(known),
        unknown_cost_batches=unknown,
        quantities={unit: number(q) for unit, q in unit_totals.items()},
        **{
            key: sum(bool(row[key]) for row in rows)
            for key in (
                "aged",
                "near_expiry",
                "no_recent_sales",
                "expired",
                "expiry_mismatch",
                "data_issue",
            )
        },
        as_of=report.operations.inventory.as_of.isoformat(),
        lookback_start=start.isoformat(),
        lookback_end_exclusive=day.isoformat(),
        lookback_days=step.lookback_days,
        age_threshold_days=step.age_threshold_days,
        expiry_threshold_days=step.expiry_threshold_days,
    )
    return AnalysisResult(
        domain="inventory",
        kind="inventory_risk",
        title="库存积压与效期关注",
        columns=dict(
            name="品种",
            batch_code="批次",
            unit="单位",
            quantity="库存数量",
            age_days="库龄天数",
            occupied_amount="采购成本占用",
            expiry_date="到期日",
            estimated_clear_days="先到期先售出预计售完天数",
            risk_tags="关注项",
        ),
        rows=rows if full else rows[: step.top_n],
        totals=totals,
        findings=[
            f"截至{totals['as_of']}，有库存批次分组{len(rows)}组；库龄较长{totals['aged']}组、临期{totals['near_expiry']}组、已过期{totals['expired']}组。"
        ],
        notes=[
            "库存为备份时点的KCSL余额，不是实时库存或可用库存；占用金额=该批次采购单价×数量，不是实际借款余额。",
            "库龄从批次入库日期算起，以备份时点计算。库存单位和出库规格、厂家、单位必须可比，不跨单位累计销量。",
            "销售速度采用备份日前所选完整日期的确认售出量，未扣退货。预计售完天数按同品种先到期先售出累计库存÷日均出库量计算，属于假设估算。",
            "已过期批次单独提示并排除可售估算；无近期出库不表示0天售完。效期缺失时暂停同品种售完估算；本报告不自动报废、调拨或下采购单。",
            f"各关注项可重叠。概览覆盖全部分组，列表优先展示过期、预计滞留至到期、临期、无近期出库及久存批次，最多{step.top_n}组，完整明细可下载。"
            if not full
            else "完整导出当前商品筛选范围的全部正库存分组。",
        ],
    )
