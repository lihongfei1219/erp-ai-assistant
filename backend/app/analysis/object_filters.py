"""Local, authorized entity resolution and deterministic object predicates."""

import unicodedata
from decimal import Decimal

from app.analysis.sales_query import QueryUnavailable


def normalized(value):
    return unicodedata.normalize("NFKC", value).strip().casefold()


def entity_directory(report, domain, field):
    """Only names/codes present in this domain's authorized snapshot; never sent to a model."""
    entries = {}
    scope = report.metadata.scope

    def add(code, name):
        entries.setdefault(code, set()).add(name or code)

    if domain == "inventory":
        if field != "product" or not scope.all_buyers:
            return entries
        facts = report.operations.inventory if report.operations else None
        for record in facts.records if facts else []:
            add(record.product_code, record.product_name)
        return entries
    if domain == "sales":
        documents = report.evidence if report.operating else []
        statuses = report.operating.policy.included_statuses if report.operating else []
    else:
        facts = getattr(report.operations, domain, None) if report.operations else None
        documents = facts.documents if facts else []
        statuses = facts.included_statuses if facts else []
    for doc in documents:
        if doc.status not in statuses or (
            not scope.all_buyers and doc.buyer_code not in scope.buyer_codes
        ):
            continue
        if field == "buyer":
            add(doc.buyer_code, doc.buyer_name)
        elif field == "product":
            for line in doc.lines:
                add(line.product_code, line.product_name)
    return entries


def resolve_entity(directory, value):
    if value in directory:
        return value, []
    query = normalized(value)
    exact = [
        code
        for code, names in directory.items()
        if any(normalized(name) == query for name in names)
    ]
    if len(exact) == 1:
        return exact[0], []
    candidates = exact or [
        code
        for code, names in directory.items()
        if query and (query in normalized(code) or any(query in normalized(name) for name in names))
    ]
    return None, sorted(candidates)


def binding_key(intent, condition):
    return dict(
        intent_id=intent.id,
        constraint_id=condition.id,
        domain=intent.domain,
        field=condition.field,
        operator=condition.operator,
        value=condition.value,
    )


def binding_matches(binding, intent, condition):
    # Inclusion/exclusion changes the predicate, not the already confirmed identity.
    return all(
        binding.get(k) == v for k, v in binding_key(intent, condition).items() if k != "operator"
    )


def matches(filters, field, code):
    include = {f.code for f in filters if f.field == field and f.operator != "exclude"}
    exclude = {f.code for f in filters if f.field == field and f.operator == "exclude"}
    return code not in exclude and (not include or code in include)


def validate_filters(report, step):
    for condition in step.filters:
        if condition.code not in entity_directory(report, step.domain, condition.field):
            raise QueryUnavailable("筛选对象不在当前授权业务快照中，请重新选择。")


def filtered_orders(report, step):
    """Matched line amounts, one row per order; never mutate the reconciled source."""
    orders = []
    for order in report.evidence:
        if order.status not in report.operating.policy.included_statuses:
            continue
        if not matches(step.filters, "buyer", order.buyer_code):
            continue
        lines = [
            line for line in order.lines if matches(step.filters, "product", line.product_code)
        ]
        if lines:
            orders.append(
                order.model_copy(
                    update={
                        "lines": lines,
                        "amount": sum((line.amount for line in lines), Decimal(0)),
                    }
                )
            )
    return orders


def filter_notes(step):
    if not step.filters:
        return []
    labels = {"product": "商品", "buyer": "客户"}
    values = [
        f"{'排除' if f.operator == 'exclude' else '包含'}{labels[f.field]}编码 {f.code}"
        for f in step.filters
    ]
    return [
        "筛选范围：" + "；".join(values) + "。",
        "同字段包含条件取并集，排除优先，不同字段同时满足；金额仅计匹配明细，单据数去重。",
    ]
