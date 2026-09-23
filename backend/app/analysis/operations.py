"""Deterministic calculations over reconciled business facts; no model or database calls."""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.analysis.object_filters import matches
from app.analysis.sales_query import QueryUnavailable
from app.capabilities.registry import DOMAIN_LABELS
from app.capabilities.registry import METRICS as METRICS
from app.capabilities.registry import OPERATIONS as OPERATIONS
from app.capabilities.registry import TARGETS as TARGETS
from app.capabilities.registry import target_supported as target_supported
from app.capabilities.view import capability_view, date_bounds, unavailable_message
from app.capabilities.view import executable_domains as executable_domains
from app.schemas.analytics import AnalysisResult
from app.schemas.operations import OperationsSnapshot

ZERO = Decimal(0)


def domain_dates(report, domain):
    entry = capability_view(report)["domains"][domain]
    if not entry["executable"]:
        raise QueryUnavailable(unavailable_message(entry))
    return date_bounds(report, domain)


def validate_operation_step(report, step):
    start, end = domain_dates(report, step.domain)
    if step.domain == "inventory":
        if (step.start_date, step.end_date_exclusive) != (start, end):
            raise QueryUnavailable(
                f"库存只有 {report.operations.inventory.as_of.isoformat()} 的备份时点快照，"
                "不是实时库存，也不代表该日结束库存；不能查询其他日期或跨天累加。"
            )
    elif step.start_date < start or step.end_date_exclusive > end:
        raise QueryUnavailable(
            f"{DOMAIN_LABELS[step.domain]}完整数据覆盖 {start} 至 {end - timedelta(days=1)}。"
        )


def validate_operations(report):
    try:
        facts = OperationsSnapshot.model_validate(report.operations.model_dump())
    except (ValidationError, AttributeError):
        raise QueryUnavailable("业务快照对账未通过，请重新生成。") from None
    scope = report.metadata.scope
    if (
        facts.source_as_of != report.metadata.source_as_of
        or facts.all_buyers != scope.all_buyers
        or set(facts.buyer_codes) != set(scope.buyer_codes)
    ):
        raise QueryUnavailable("业务快照的授权范围或数据水位不一致。")
    tz = ZoneInfo(
        report.operating.policy.business_timezone if report.operating else "Asia/Shanghai"
    )
    for domain in ("returns", "shipping"):
        events = getattr(facts, domain)
        if events and any(
            not events.start <= doc.occurred_at.astimezone(tz).date() < events.end_exclusive
            for doc in events.documents
        ):
            raise QueryUnavailable("业务记录超出已声明的数据日期范围。")


def _number(value):
    return format(value.quantize(Decimal("0.0001")), "f")


def _source_ids(domain, ids):
    values = sorted(set(ids))
    return {
        "source_ids": ", ".join(f"{domain}:{i}" for i in values[:20]),
        "source_count": len(values),
    }


def _limit(rows, limit, notes):
    if len(rows) > limit:
        notes.append(f"结果共 {len(rows)} 行，当前展示前 {limit} 行；未展示部分未从总计中扣除。")
    return rows[:limit]


def _ranking(records, step, notes):
    quantity = step.metric in {"quantity", "stock"}
    by_buyer = step.kind == "buyer_ranking"
    groups = defaultdict(list)
    for row in records:
        key = (row["buyer_code"] if by_buyer else row["code"], row["unit"] if quantity else "")
        groups[key].append(row)
    rows = []
    for (code, _), group in groups.items():
        units = {r["unit"] for r in group}
        value = dict(
            code=code,
            name=group[-1]["buyer_name"] if by_buyer else group[-1]["name"],
            unit=next(iter(units)) if len(units) == 1 else "多单位（见明细）",
        )
        if len(units) == 1:
            value["quantity"] = _number(sum((r["quantity"] for r in group), ZERO))
        if step.domain != "inventory":
            value.update(
                amount=_number(sum((r["amount"] for r in group), ZERO)),
                document_count=len({r["document_id"] for r in group}),
            )
        value.update(_source_ids(step.domain, [r["source_id"] for r in group]))
        rows.append(value)
    key = "quantity" if quantity else ("document_count" if step.metric == "orders" else "amount")
    units = sorted({r["unit"] for r in rows}) if quantity else [None]
    selected = []
    for unit in units:
        group = [row for row in rows if unit is None or row["unit"] == unit]
        sign = -1 if step.order == "descending" else 1
        group.sort(key=lambda row: (sign * Decimal(row[key]), row["code"]))
        selected.extend(dict(row, rank=i + 1) for i, row in enumerate(group[: step.top_n]))
    if quantity:
        notes.append(f"不同单位不混排；每个单位分别展示前 {step.top_n} 名，合计最多50行。")
    return _limit(selected, 50, notes), len(rows)


def _unit_rows(records, domain):
    groups = defaultdict(list)
    for row in records:
        groups[row["unit"]].append(row)
    rows = []
    for unit, group in sorted(groups.items()):
        value = dict(
            unit=unit,
            quantity=_number(sum((r["quantity"] for r in group), ZERO)),
            product_count=len({r["code"] for r in group}),
        )
        if domain != "inventory":
            value.update(
                amount=_number(sum((r["amount"] for r in group), ZERO)),
                document_count=len({r["document_id"] for r in group}),
            )
        value.update(_source_ids(domain, [r["source_id"] for r in group]))
        rows.append(value)
    return rows


def _trend(records, step):
    rows = []
    units = sorted({r["unit"] for r in records}) if step.metric == "quantity" else [None]
    for offset in range((step.end_date_exclusive - step.start_date).days):
        day = step.start_date + timedelta(days=offset)
        for unit in units:
            group = [r for r in records if r["day"] == day and (unit is None or r["unit"] == unit)]
            row = dict(
                day=day.isoformat(),
                amount=_number(sum((r["amount"] for r in group), ZERO)),
                document_count=len({r["document_id"] for r in group}),
            )
            if unit is not None:
                row.update(unit=unit, quantity=_number(sum((r["quantity"] for r in group), ZERO)))
            row.update(_source_ids(step.domain, [r["source_id"] for r in group]))
            rows.append(row)
    return rows


def execute_operation(report, step):
    facts = getattr(report.operations, step.domain)
    label = DOMAIN_LABELS[step.domain]
    tz = ZoneInfo(report.operating.policy.business_timezone)
    inventory = step.domain == "inventory"
    notes = [facts.time_basis + "。", "数量按单位分开统计，不跨单位求和；来源编号带业务域前缀。"]
    if inventory:
        records = [
            dict(
                code=r.product_code,
                name=r.product_name or r.product_code,
                unit=r.unit,
                quantity=r.quantity,
                source_id=r.record_id,
                batch_code=r.batch_code,
            )
            for r in facts.records
            if matches(step.filters, "product", r.product_code)
        ]
        totals = dict(
            product_count=len({r["code"] for r in records}),
            positive_product_count=len({r["code"] for r in records if r["quantity"] > 0}),
            batch_count=len(records),
            as_of=facts.as_of.isoformat(),
        )
        notes.append(f"库存时点：{facts.as_of.isoformat()}；不是实时或日末库存，未按仓库拆分。")
        findings = [
            f"该备份时点有库存商品 {totals['positive_product_count']} 种；数量见各单位分项。"
        ]
    else:
        documents = [
            doc
            for doc in facts.documents
            if step.start_date <= doc.occurred_at.astimezone(tz).date() < step.end_date_exclusive
        ]
        records = [
            dict(
                code=line.product_code,
                name=line.product_name or line.product_code,
                unit=line.unit,
                quantity=line.quantity,
                amount=line.amount,
                document_id=doc.document_id,
                document_number=doc.document_number,
                original_order_number=doc.original_order_number,
                source_id=doc.document_id,
                buyer_code=doc.buyer_code,
                buyer_name=doc.buyer_name or doc.buyer_code,
                day=doc.occurred_at.astimezone(tz).date(),
                line_id=line.line_id,
            )
            for doc in documents
            for line in doc.lines
            if matches(step.filters, "buyer", doc.buyer_code)
            and matches(step.filters, "product", line.product_code)
        ]
        totals = dict(
            amount=_number(sum((row["amount"] for row in records), ZERO)),
            document_count=len({row["document_id"] for row in records}),
            product_count=len({r["code"] for r in records}),
        )
        notes.append("纳入状态：" + "、".join(facts.included_statuses) + "。")
        notes.append("同一单据可能含多个商品或单位，各行单据数不能相加代替总单据数。")
        notes.append(
            f"整份快照另有 {facts.excluded_document_count} 张不符合当前纳入状态的单据，未计入。"
        )
        notes.append(
            "退货单金额不是成功退款，未从销售金额中扣减。"
            if step.domain == "returns"
            else "只统计已确认的销售出库；采购退出未计入，单据数不是商品件数。"
        )
        findings = [
            f"所选期间{label}单据 {totals['document_count']} 张，匹配金额 {totals['amount']}。"
        ]
    totals["unit_count"] = len({r["unit"] for r in records})
    columns = {
        "unit": "单位",
        "quantity": "库存数量" if inventory else "数量",
        "product_count": "商品种数",
    }
    if not inventory:
        columns.update(amount=f"{label}金额", document_count=f"{label}单据数")
    rows = []
    mode = {
        "summary": "概览",
        "trend": "每日趋势",
        "product_ranking": "商品排行",
        "buyer_ranking": "客户排行",
        "list": "明细",
        "existence": "记录查询",
    }[step.kind]
    if step.kind == "summary":
        rows = _unit_rows(records, step.domain)
    elif step.kind.endswith("ranking"):
        rows, totals["total_groups"] = _ranking(records, step, notes)
        columns = {
            "rank": "排名（数量按单位分组）" if step.metric in {"quantity", "stock"} else "排名",
            "name": "客户" if step.kind == "buyer_ranking" else "商品",
            "code": "编码",
            **{k: v for k, v in columns.items() if k != "product_count"},
        }
    elif step.kind == "trend":
        rows = _trend(records, step)
        columns = {"day": "日期", "amount": f"{label}金额", "document_count": "单据数"}
        if step.metric == "quantity":
            columns.update(unit="单位", quantity="数量")
        rows = _limit(rows, 2000, notes)
    elif step.kind == "existence":
        exists = any(r["quantity"] > 0 for r in records) if inventory else bool(records)
        rows = [{"exists": "有" if exists else "无", **totals}]
        columns = {
            "exists": "是否有库存" if inventory else f"是否有{label}记录",
            "product_count": "商品种数",
        }
        if not inventory:
            columns.update(amount=f"{label}金额", document_count="单据数")
    elif step.kind == "list" and step.dimension == "buyer":
        rows, totals["total_groups"] = _ranking(
            records, step.model_copy(update={"kind": "buyer_ranking"}), notes
        )
        if totals["total_groups"] > len(rows):
            group_label = "客户与单位分组" if step.metric == "quantity" else "客户"
            metric_label = {"amount": "单据金额", "orders": "单据数", "quantity": "各单位数量"}[
                step.metric
            ]
            direction = "从高到低" if step.order == "descending" else "从低到高"
            notes.append(
                f"共有 {totals['total_groups']} 个{group_label}，按{metric_label}{direction}选取，"
                f"当前按客户编码展示 {len(rows)} 行；未展示部分未从总计中扣除。"
            )
        rows.sort(key=lambda row: row["code"])
        for row in rows:
            row.pop("rank", None)
        columns = {
            "name": "客户",
            "code": "客户编码",
            "document_count": "单据数",
            "amount": f"{label}金额",
        }
        if step.metric == "quantity":
            columns.update(unit="单位", quantity="数量")
    else:
        ordered = sorted(
            records,
            key=lambda r: (r.get("day", step.start_date), r["source_id"], r.get("line_id", 0)),
            reverse=True,
        )
        rows = [
            dict(
                r,
                quantity=_number(r["quantity"]),
                **(
                    {"amount": _number(r["amount"]), "day": r["day"].isoformat()}
                    if not inventory
                    else {}
                ),
                **_source_ids(step.domain, [r["source_id"]]),
            )
            for r in ordered
        ]
        totals["total_rows"] = len(rows)
        rows = _limit(rows, step.top_n, notes)
        columns = {"name": "商品", "code": "商品编码", "unit": "单位", "quantity": "数量"}
        if inventory:
            columns.update(batch_code="批次")
        else:
            columns.update(
                day="业务日期",
                document_number=f"{label}单号",
                original_order_number="原销售单号",
                amount=f"{label}金额",
            )
    if not rows:
        findings.append("已覆盖的范围内没有满足条件的记录。")
    columns.update(source_ids="来源记录（最多20个）")
    notes.append(
        "来源："
        + " / ".join(facts.source_tables)
        + "；口径版本 "
        + report.operations.policy_version
        + "。"
    )
    return AnalysisResult(
        domain=step.domain,
        kind=step.kind,
        title=label + mode,
        rows=rows,
        columns=columns,
        totals=totals,
        findings=findings,
        notes=notes,
    )
