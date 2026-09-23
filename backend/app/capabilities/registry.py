"""Single immutable execution contract; no models, reports or executable user code."""

import hashlib
import json
from dataclasses import asdict, dataclass
from types import MappingProxyType

MAX_DAYS = 90
MAX_STEPS = 6
MAX_ITEMS = 50
DEFAULT_ITEMS = 10
OBJECT_FILTERS = True
FILTER_FIELDS = MappingProxyType(
    {
        "sales": ("product", "buyer"),
        "returns": ("product", "buyer"),
        "shipping": ("product", "buyer"),
        "inventory": ("product",),
    }
)


@dataclass(frozen=True)
class Domain:
    id: str
    label: str
    default_metric: str
    fact: str
    time_basis: str
    grain: str
    scope: str = "authorized_buyers"
    quantity_policy: str = "separate_by_unit"
    count_policy: str = "distinct_document"
    amount_precision: int = 4


@dataclass(frozen=True)
class Capability:
    domain: str
    kind: str
    operation: str
    metrics: tuple[str, ...]
    semantic_metrics: tuple[str, ...]
    targets: tuple[str, ...]
    title: str
    ordered: bool = False
    limited: bool = False
    dimensions: tuple[str, ...] = ("product",)

    @property
    def id(self):
        return f"{self.domain}.{self.kind}"


DOMAINS = MappingProxyType(
    {
        d.id: d
        for d in (
            Domain("sales", "销售", "amount", "sales_orders", "order_created_at", "order_and_line"),
            Domain(
                "returns",
                "退货",
                "amount",
                "completed_sales_returns",
                "return_created_at",
                "document_and_line",
            ),
            Domain(
                "shipping",
                "销售出库",
                "orders",
                "confirmed_sales_shipping",
                "shipping_confirmed_at",
                "document_and_line",
            ),
            Domain(
                "inventory",
                "库存",
                "stock",
                "batch_inventory",
                "snapshot_as_of",
                "product_batch_unit",
                "all_buyers",
                count_policy="batch_record",
            ),
        )
    }
)


def _capabilities():
    sales = (
        ("summary", "summary", ("amount",), ("amount", "orders"), ("product", "buyer"), "销售概览"),
        (
            "trend",
            "trend",
            ("amount", "orders"),
            ("amount", "orders"),
            ("product", "buyer"),
            "每日销售趋势",
        ),
        (
            "buyer_ranking",
            "ranking",
            ("amount", "orders"),
            ("amount", "orders"),
            ("buyer",),
            "客户排行",
        ),
        (
            "product_ranking",
            "ranking",
            ("amount", "orders"),
            ("amount", "orders"),
            ("product",),
            "商品排行",
        ),
        (
            "comparison",
            "comparison",
            ("amount",),
            ("amount",),
            ("product", "buyer"),
            "期间变化与贡献",
        ),
        ("anomalies", "anomalies", ("amount",), ("amount",), ("product", "buyer"), "日波动线索"),
    )
    for kind, operation, metrics, semantic, targets, title in sales:
        yield Capability(
            "sales",
            kind,
            operation,
            metrics,
            semantic,
            targets,
            title,
            ordered=operation == "ranking",
            limited=operation in {"ranking", "comparison"},
            dimensions=("product", "buyer") if operation == "comparison" else ("product",),
        )
    for domain in ("returns", "shipping", "inventory"):
        metrics = (
            ("stock", "quantity") if domain == "inventory" else ("amount", "orders", "quantity")
        )
        for kind, operation, targets, label in (
            ("summary", "summary", (), "概览"),
            ("trend", "trend", (), "趋势"),
            ("product_ranking", "ranking", ("product",), "商品排行"),
            ("buyer_ranking", "ranking", ("buyer",), "客户排行"),
            ("list", "list", ("product", "buyer"), "明细"),
            ("existence", "existence", (), "有无记录"),
        ):
            if domain == "inventory" and kind in {"trend", "buyer_ranking"}:
                continue
            if domain == "inventory" and kind == "list":
                targets = ("product",)
            yield Capability(
                domain,
                kind,
                operation,
                metrics,
                metrics,
                targets,
                DOMAINS[domain].label + label,
                ordered=operation == "ranking",
                limited=operation in {"ranking", "list"},
                dimensions=targets if kind == "list" else ("product",),
            )


REGISTRY = MappingProxyType({(c.domain, c.kind): c for c in _capabilities()})
DOMAIN_LABELS = MappingProxyType({key: d.label for key, d in DOMAINS.items()})
OPERATIONS = MappingProxyType(
    {
        domain: frozenset(c.operation for c in REGISTRY.values() if c.domain == domain)
        for domain in DOMAINS
    }
)
METRICS = MappingProxyType(
    {
        domain: frozenset(
            m for c in REGISTRY.values() if c.domain == domain for m in c.semantic_metrics
        )
        for domain in DOMAINS
    }
)
TARGETS = MappingProxyType(
    {
        domain: frozenset(t for c in REGISTRY.values() if c.domain == domain for t in c.targets)
        for domain in DOMAINS
    }
)
SALES_TITLES = {c.kind: c.title for c in REGISTRY.values() if c.domain == "sales"}


def semantic_metrics(domain, operation):
    return frozenset(
        m
        for c in REGISTRY.values()
        if c.domain == domain and (operation in {None, "unknown"} or c.operation == operation)
        for m in c.semantic_metrics
    )


def target_supported(domain, operation, target):
    if target is None:
        return True
    if operation not in OPERATIONS.get(domain, ()):
        # Preserve the target while separately diagnosing the unsupported operation.
        return target in TARGETS.get(domain, ())
    return any(
        c.domain == domain and c.operation == operation and target in c.targets
        for c in REGISTRY.values()
    )


def registry_version():
    value = {
        "domains": [asdict(d) for d in DOMAINS.values()],
        "capabilities": [asdict(c) for c in REGISTRY.values()],
        "limits": [MAX_DAYS, MAX_STEPS, MAX_ITEMS, DEFAULT_ITEMS],
        "object_filters": OBJECT_FILTERS,
        "filter_fields": dict(FILTER_FIELDS),
    }
    digest = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]
    return "erp.capabilities.v1." + digest


def validate_step(step):
    """Validate the registered matrix and cross-field constraints for public plans."""
    days = (step.end_date_exclusive - step.start_date).days
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"分析区间必须为 1 至 {MAX_DAYS} 个完整日")
    cap = REGISTRY.get((step.domain, step.kind))
    if cap is None:
        raise ValueError("该业务不支持此分析方式")
    if any(f.field not in FILTER_FIELDS[step.domain] for f in step.filters):
        raise ValueError("该业务不支持此对象筛选，库存不支持客户筛选")
    if step.metric not in cap.metrics:
        raise ValueError("该分析不支持此指标")
    if step.domain == "inventory" and days != 1:
        raise ValueError("库存必须指定单一时点日期")
    if step.dimension not in cap.dimensions:
        raise ValueError("该分析不支持此拆解维度")
    if not cap.ordered and step.order != "descending":
        raise ValueError("只有排行接受排序方向")
    if not cap.limited and step.top_n != DEFAULT_ITEMS:
        raise ValueError("该分析不接受排行条数")
    if cap.operation == "comparison":
        start, end = step.comparison_start_date, step.comparison_end_date_exclusive
        if start is None or end is None or (end - start).days != days:
            raise ValueError("比较期必须明确指定，且与分析期天数相同")
        if not (end <= step.start_date or start >= step.end_date_exclusive):
            raise ValueError("分析期与比较期不能重叠")
    elif step.comparison_start_date is not None or step.comparison_end_date_exclusive is not None:
        raise ValueError("只有期间比较接受比较期")
    return step


SALES_METRIC_DEFINITIONS = [
    {
        "id": "amount",
        "label": "有效订单金额",
        "definition": (
            "纳入快照有效状态的订单表头金额之和；商品按明细金额。不是支付成交额，不扣退款。"
        ),
    },
    {
        "id": "orders",
        "label": "订单数",
        "definition": "按订单 ID 去重；不同商品订单数不可相加作为总订单数。",
    },
    {"id": "buyers", "label": "采购企业数", "definition": "在所选期间有效订单中按企业编码去重。"},
    {
        "id": "change",
        "label": "期间变化率",
        "definition": "(本期金额－比较期金额) / 比较期金额；基数为零时无定义。",
    },
    {
        "id": "anomalies",
        "label": "日波动线索",
        "definition": (
            "与区间内前一日比较，金额变化绝对比例至少 50%；"
            "前日零、本日非零单独标记，不代表统计异常或因果。"
        ),
    },
]
