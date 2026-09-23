"""Safe capability metadata intersected with the selected snapshot and scope."""

from datetime import timedelta
from zoneinfo import ZoneInfo

from app.analysis.sales_query import QueryUnavailable
from app.capabilities.registry import (
    DOMAINS,
    MAX_DAYS,
    MAX_ITEMS,
    MAX_STEPS,
    METRICS,
    OBJECT_FILTERS,
    OPERATIONS,
    REGISTRY,
    SALES_TITLES,
    TARGETS,
    registry_version,
)


def date_bounds(report, domain):
    if report.operating is None or report.metadata.source_as_of.tzinfo is None:
        raise QueryUnavailable("快照缺少经营规则或带时区的数据水位，请重新生成。")
    tz = ZoneInfo(report.operating.policy.business_timezone)
    cutoff = report.metadata.source_as_of.astimezone(tz).date()
    if domain == "sales":
        return report.metadata.window.start, min(report.metadata.window.end, cutoff)
    facts = getattr(report.operations, domain, None)
    if facts is None:
        raise QueryUnavailable(f"{DOMAINS[domain].label}事实未加载，请生成包含该业务的新快照。")
    if domain == "inventory":
        day = facts.as_of.astimezone(tz).date()
        return day, day + timedelta(days=1)
    return facts.start, min(facts.end_exclusive, cutoff)


def capability_view(report):
    """Contains no entity names, identities, credentials, rows or calculated totals."""
    domains = {}
    for domain, spec in DOMAINS.items():
        reason = None
        if spec.scope == "all_buyers" and not report.metadata.scope.all_buyers:
            reason = "permission_denied"
        elif domain != "sales" and getattr(report.operations, domain, None) is None:
            reason = "facts_missing"
        elif domain == "sales" and (
            not report.quality.sql_control_totals_match or report.quality.header_line_mismatch_count
        ):
            reason = "reconciliation_failed"
        elif domain != "sales" and (
            report.operations.source_as_of != report.metadata.source_as_of
            or report.operations.all_buyers != report.metadata.scope.all_buyers
            or set(report.operations.buyer_codes) != set(report.metadata.scope.buyer_codes)
        ):
            reason = "snapshot_scope_mismatch"
        start = end = None
        if reason is None:
            try:
                start, end = date_bounds(report, domain)
                if end <= start:
                    reason = "no_complete_dates"
            except (QueryUnavailable, ValueError):
                reason = "invalid_metadata"
        executable = reason is None
        item = {
            "label": spec.label,
            "executable": executable,
            "reason_code": reason,
            "fact": spec.fact,
            "time_basis": spec.time_basis,
            "grain": spec.grain,
            "required_scope": spec.scope,
            "metrics": sorted(METRICS[domain]) if executable else [],
            "targets": sorted(TARGETS[domain]) if executable else [],
            "operations": sorted(OPERATIONS[domain]) if executable else [],
            "capabilities": [
                {
                    "id": c.id, "kind": c.kind, "operation": c.operation,
                    "metrics": list(c.metrics), "semantic_metrics": list(c.semantic_metrics),
                    "targets": list(c.targets), "dimensions": list(c.dimensions),
                    "ordered": c.ordered, "limited": c.limited,
                }
                for c in REGISTRY.values()
                if c.domain == domain
            ]
            if executable
            else [],
            "quantity_policy": spec.quantity_policy,
            "amount_precision": spec.amount_precision,
            "count_policy": spec.count_policy,
        }
        if start is not None:
            item.update(available_start=start.isoformat(), available_end_exclusive=end.isoformat())
        if executable and domain == "inventory":
            item["snapshot_as_of"] = report.operations.inventory.as_of.isoformat()
        domains[domain] = item
    return {
        "version": registry_version(),
        "domains": domains,
        "max_days": MAX_DAYS,
        "max_steps": MAX_STEPS,
        "max_items": MAX_ITEMS,
        "object_filters": OBJECT_FILTERS,
    }


def executable_domains(report):
    return [key for key, value in capability_view(report)["domains"].items() if value["executable"]]


def unavailable_message(entry):
    reason = {
        "permission_denied": "当前授权范围不支持这项查询",
        "facts_missing": "当前快照尚未加载该业务数据",
        "reconciliation_failed": "快照对账未通过，需要重新生成",
        "snapshot_scope_mismatch": "业务快照与授权范围或数据水位不一致",
        "no_complete_dates": "当前快照没有完整日期可供分析",
        "invalid_metadata": "快照的日期或业务规则信息不完整",
    }.get(entry["reason_code"], "暂不可执行，请检查数据与授权范围")
    return f"{entry['label']}：{reason}。"


def model_capabilities(report):
    view = capability_view(report)
    start, end = date_bounds(report, "sales")
    sales = view["domains"]["sales"]
    return {
        "version": view["version"],
        "available_start": start.isoformat(),
        "available_end_exclusive": end.isoformat(),
        "all_buyers_authorized": report.metadata.scope.all_buyers,
        "executable_domains": [
            key for key, value in view["domains"].items() if value["executable"]
        ],
        "domain_capabilities": view["domains"],
        "metrics": sales["metrics"],
        "executable_targets": sales["targets"],
        "analyses": list(SALES_TITLES) if sales["executable"] else [],
        "object_filters": view["object_filters"],
        "max_days": view["max_days"],
        "max_steps": view["max_steps"],
        "max_items": view["max_items"],
    }
