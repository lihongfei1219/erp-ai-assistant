"""Shared guided conversation: model understands, bounded program decides and executes."""

from dataclasses import dataclass
from datetime import date, timedelta
from uuid import uuid4

from starlette.concurrency import run_in_threadpool

from app.analysis.analytics import available_dates, execute_analysis
from app.analysis.operations import (
    METRICS as DOMAIN_METRICS,
)
from app.analysis.operations import (
    OPERATIONS as DOMAIN_OPERATIONS,
)
from app.analysis.operations import (
    TARGETS,
    domain_dates,
    executable_domains,
    target_supported,
)
from app.analysis.planning import finish_compilation, resolve_question
from app.analysis.sales_query import QueryUnavailable
from app.schemas.analytics import AnalysisQuestion
from app.semantic.catalog import load_catalog
from app.semantic.compiler import compile_request, context_from_plan
from app.semantic.dates import canonical_period, resolve_period
from app.semantic.dialogue_schemas import (
    AvailableDates,
    ChoiceAction,
    Clarification,
    ConversationRequest,
    DialogueChoice,
    DialogueDraft,
    DialogueTurn,
    DraftIntent,
)
from app.semantic.schemas import SemanticContext, SemanticRequest

DOMAINS = {"sales": "销售", "returns": "退货", "shipping": "出库", "inventory": "库存"}
OPERATIONS = {
    "summary": "概览",
    "trend": "趋势",
    "ranking": "排行",
    "comparison": "期间比较",
    "anomalies": "日波动",
    "list": "明细",
    "existence": "是否发生",
}
METRICS = {
    "amount": "有效订单金额",
    "orders": "订单数",
    "quantity": "商品数量",
    "return_rate": "退货率",
    "stock": "库存",
    "turnover": "周转率",
    "payment": "支付金额",
    "profit": "利润",
}
FILTERS = {
    "product": "商品",
    "buyer": "客户",
    "category": "类别",
    "warehouse": "仓库",
    "supplier": "供应商",
    "region": "地区",
    "status": "状态",
    "other": "其他",
}
FIELD_VALUES = {
    "domain": DOMAINS,
    "operation": OPERATIONS,
    "metric": METRICS,
    "target": {"product": "商品", "buyer": "客户"},
    "order": {"descending": "从高到低", "ascending": "从低到高"},
    "scope": {"authorized": "当前授权范围", "all_buyers": "全平台"},
}
FIELD_LABELS = {
    "domain": "业务",
    "operation": "分析目标",
    "target": "对象",
    "metric": "指标",
    "time": "日期",
    "comparison_time": "比较期",
    "limit": "条数",
    "order": "排序",
    "scope": "范围",
    "generic_product_scope": "商品范围",
}


class DialogueChoiceUnavailable(QueryUnavailable):
    pass


@dataclass(frozen=True)
class DialogueOutcome:
    turn: DialogueTurn
    context: SemanticContext


def _label(item):
    return DOMAINS.get(item.domain, "待明确业务") + OPERATIONS.get(item.operation, "分析")


def _target_label(target):
    return load_catalog().get("targets", {}).get(target, target)


def _metric_label(item, metric):
    if item.domain in {"returns", "shipping"} and metric in {"amount", "orders"}:
        return DOMAINS[item.domain] + ("单据金额" if metric == "amount" else "单据数")
    return METRICS.get(metric, "指标待明确")


def _dates_for(report, item):
    if item.domain != "sales" and item.domain in executable_domains(report):
        return domain_dates(report, item.domain)
    return available_dates(report)


def _draft(context):
    items, summaries, defaults = [], [], []
    for item in context.intents:
        fields = {
            k: v
            for k, v in item.model_dump().items()
            if k not in {"id", "filters", "unresolved"} and v is not None
        }
        constraints = [
            {
                "id": f.id,
                "label": (
                    f"{FILTERS[f.field]}{'排除' if f.operator == 'exclude' else '限定'}：{f.value}"
                ),
            }
            for f in item.filters
        ]
        sources = context.field_sources.get(item.id, {})
        items.append(
            DraftIntent(
                id=item.id,
                label=_label(item),
                fields=fields,
                constraints=constraints,
                field_sources=sources,
            )
        )
        parts = [_label(item)]
        if item.target:
            parts.append(_target_label(item.target))
        if item.metric:
            parts.append(_metric_label(item, item.metric))
        if item.time:
            parts.append(item.time)
        if item.comparison_time:
            parts.append("比较期 " + item.comparison_time)
        if item.scope:
            parts.append(FIELD_VALUES["scope"][item.scope])
        if item.order:
            parts.append(FIELD_VALUES["order"][item.order])
        if item.limit:
            parts.append(f"前 {item.limit} 项")
        parts.extend(c["label"] for c in constraints)
        if item.unresolved:
            parts.append("待确认：" + "、".join(item.unresolved))
        summaries.append("，".join(parts))
        for field, source in sources.items():
            if source != "default":
                continue
            value = getattr(item, field, None)
            label = FIELD_VALUES.get(field, {}).get(value, str(value))
            if field == "metric":
                label = _metric_label(item, value)
            if value is not None:
                field_label = {"metric": "指标", "limit": "条数", "target": "对象"}.get(
                    field, field
                )
                defaults.append(f"{_label(item)}默认{field_label}：{label}")
    if context.product_scope_note:
        defaults.append("药品泛称按当前商品范围分析，未按药品类别筛选，可能包含其他商品。")
    # This visible summary is also the user's explicit recovery source after token expiry.
    # Keep unresolved dependencies; otherwise recovery could silently turn them independent.
    summaries.extend("尚待确认、未解除的条件：" + issue.question for issue in context.issues)
    summaries.extend("尚待确认、未解除的条件：" + text for text in context.unresolved)
    if context.pending_request:
        changes = []
        for patch in context.pending_request.intents:
            for field, value in patch.model_dump().items():
                if field in FIELD_LABELS and value is not None:
                    changes.append(
                        FIELD_LABELS[field]
                        + "改为"
                        + str(FIELD_VALUES.get(field, {}).get(value, value))
                    )
            changes.extend(f"保留筛选：{f.value}" for f in patch.filters)
        for edit in context.pending_request.edits:
            if edit.field in FIELD_LABELS:
                changes.append(
                    ("取消" if edit.operation == "unset" else "修改")
                    + FIELD_LABELS[edit.field]
                    + (
                        "：" + str(FIELD_VALUES.get(edit.field, {}).get(edit.value, edit.value))
                        if edit.value is not None
                        else ""
                    )
                )
        if changes:
            summaries.append("等待确认适用目标的修改（尚未应用）：" + "、".join(changes))
    return DialogueDraft(intents=items), "；".join(summaries), defaults


def _set(intent_id, field, value):
    return {"intent_id": intent_id, "operation": "set", "field": field, "value": value}


def _valid_date_choice(item, field, value, start, end, today):
    try:
        a, b = resolve_period(value, today)
        if a < start or b > end or not 1 <= (b - a).days <= 90:
            return None
        if item.operation == "comparison":
            other = item.comparison_time if field == "time" else item.time
            if other:
                c, d = resolve_period(other, today)
                if b - a != d - c or not (b <= c or a >= d):
                    return None
        return canonical_period(a, b)
    except (ValueError, OverflowError):
        return None


def _build_guidance(resolution, report, today):
    """Choose a single relevant question and validate suggestions against factual capabilities."""
    context = resolution.semantic.context
    start, end = available_dates(report)
    candidates = []
    item = context.intents[0]
    start, end = _dates_for(report, item)
    domains = executable_domains(report)
    field, kind, question = "conditions", "ambiguous", resolution.semantic.message
    issue_id = None

    def offer(label, edits=None, *, action="patch", target=None, slot=None):
        candidates.append(
            {
                "label": label,
                "kind": action,
                "intent_id": target or item.id,
                "field": slot or field,
                "edits": edits or [],
            }
        )

    def values(slot, pairs):
        for value, label in pairs:
            offer(label, [_set(item.id, slot, value)], slot=slot)

    def dates():
        nonlocal start, end
        start, end = _dates_for(report, item)
        offer("自己选择日期", action="date_range")
        if field == "time":
            day = end - timedelta(days=1)
            value = _valid_date_choice(item, field, canonical_period(day, end), start, end, today)
            if value:
                offer(
                    f"改看库存快照 {day}"
                    if item.domain == "inventory"
                    else f"改看最近完整数据日 {day}",
                    [_set(item.id, field, value)],
                )
            value = _valid_date_choice(item, field, canonical_period(start, end), start, end, today)
            if value and start < day:
                offer(
                    f"查看已覆盖的 {start} 至 {day}",
                    [_set(item.id, field, value)],
                )

    if resolution.semantic.status == "data_unavailable":
        field, kind = "time", "data_gap"
        for current in context.intents:
            current_start, current_end = _dates_for(report, current)
            found = False
            for slot in ("time", "comparison_time"):
                if getattr(current, slot):
                    try:
                        a, b = resolve_period(getattr(current, slot), today)
                        if a < current_start or b > current_end:
                            item, field, found = current, slot, True
                            break
                    except (ValueError, OverflowError):
                        continue
            if found:
                break
        question = f"{resolution.semantic.message} 要为“{_label(item)}”选择哪个日期区间？"
        dates()
    elif (
        any(i.scope == "all_buyers" for i in context.intents)
        and not report.metadata.scope.all_buyers
    ):
        item = next(i for i in context.intents if i.scope == "all_buyers")
        field, kind = "scope", "capability_gap"
        question = "当前数据只包含已授权企业。是否改看当前授权范围？"
        values("scope", [("authorized", "改看当前授权范围")])
    elif any(i.domain in {None, "unknown"} for i in context.intents):
        item = next(i for i in context.intents if i.domain in {None, "unknown"})
        field, kind = "domain", "missing"
        question = "你目前最想了解哪方面的经营情况？也可以直接描述遇到的问题。"
        values(
            "domain", [("sales", "销售表现"), ("returns", "退货情况"), ("inventory", "库存情况")]
        )
    elif any(i.domain not in domains for i in context.intents):
        item = next(i for i in context.intents if i.domain not in domains)
        field, kind = "domain", "capability_gap"
        question = resolution.semantic.message + " 你可以保留需求继续补充，或选择当前可用的分析。"
        independent = (
            not context.unresolved
            and not context.issues
            and not any(i.unresolved for i in context.intents)
        )
        if independent and any(i.domain == "sales" for i in context.intents):
            offer(
                "只保留销售目标，其他目标本次不执行",
                [
                    {"intent_id": i.id, "operation": "remove_intent"}
                    for i in context.intents
                    if i.domain != "sales"
                ],
            )
        offer("结束本次需求，另建销售概览", action="new_sales")
    elif any(not target_supported(i.domain, i.operation, i.target) for i in context.intents):
        item = next(
            i for i in context.intents if not target_supported(i.domain, i.operation, i.target)
        )
        field, kind = "target", "capability_gap"
        question = (
            f"已理解为按{_target_label(item.target)}分析，但当前尚未接入该维度的数据与执行能力。"
            "原日期和指标已保留。你可以继续说明需求，或明确改用以下维度。"
        )
        if item.target in TARGETS[item.domain]:
            question = (
                "已保留按"
                + _target_label(item.target)
                + "分组的要求，但这种分析方式尚不支持分组。是否改看同一对象的排行？"
            )
        if item.operation in {"ranking", "comparison"}:
            values(
                "target",
                [
                    (v, label)
                    for v, label in [("buyer", "改按客户分析"), ("product", "改按商品分析")]
                    if v in TARGETS[item.domain]
                ],
            )
        else:
            for target, label in [("buyer", "客户"), ("product", "商品")]:
                if target not in TARGETS[item.domain]:
                    continue
                if item.target in TARGETS[item.domain] and target != item.target:
                    continue
                offer(
                    f"改看{label}排行（保留日期与指标）",
                    [_set(item.id, "target", target), _set(item.id, "operation", "ranking")],
                )
    elif any(i.filters for i in context.intents):
        item = next(i for i in context.intents if i.filters)
        field, kind = "filters", "capability_gap"
        question = (
            "已保留你的筛选条件，但当前执行器尚未支持对象筛选。"
            "是否移除某个条件后继续？其余条件会保留。"
        )
        for constraint in item.filters[:3]:
            offer(
                f"移除{FILTERS[constraint.field]}条件：{constraint.value}",
                [
                    {
                        "intent_id": item.id,
                        "operation": "remove_filter",
                        "constraint_id": constraint.id,
                    }
                ],
            )
    elif any(
        i.metric is not None and i.metric not in DOMAIN_METRICS[i.domain] for i in context.intents
    ):
        item = next(
            i
            for i in context.intents
            if i.metric is not None and i.metric not in DOMAIN_METRICS[i.domain]
        )
        field = "metric"
        kind = "ambiguous" if item.metric == "unknown" else "capability_gap"
        question = (
            "你想用什么衡量这项分析？"
            if item.metric == "unknown"
            else f"已理解你要看{METRICS.get(item.metric, item.metric)}，该指标尚未接入。"
            "是否改用当前支持的口径？"
        )
        values(
            "metric",
            [
                (m, "改按" + _metric_label(item, m))
                for m in ("amount", "orders", "stock", "quantity")
                if m in DOMAIN_METRICS[item.domain]
                and (item.operation not in {"comparison", "anomalies"} or m == "amount")
            ][:3],
        )
    elif any(
        i.domain == "sales" and i.metric == "orders" and i.operation in {"comparison", "anomalies"}
        for i in context.intents
    ):
        item = next(
            i
            for i in context.intents
            if i.domain == "sales"
            and i.metric == "orders"
            and i.operation in {"comparison", "anomalies"}
        )
        field, kind = "metric", "capability_gap"
        question = resolution.semantic.message + " 是否改用金额口径，或保留订单数换一个分析目标？"
        values("metric", [("amount", "改按有效订单金额")])
        offer(
            "保留订单数，仅看统计期整体趋势（不再比较）",
            [
                _set(item.id, "operation", "trend"),
                {"intent_id": item.id, "operation": "unset", "field": "comparison_time"},
                {"intent_id": item.id, "operation": "unset", "field": "target"},
            ],
            slot="operation",
        )
    elif "order" in context.pending and any(
        i.order == "ascending" and i.operation != "ranking" for i in context.intents
    ):
        item = next(
            i for i in context.intents if i.order == "ascending" and i.operation != "ranking"
        )
        field, kind = "order", "capability_gap"
        question = "当前分析目标不支持排序。是否取消排序，或重新说明想比较哪些对象？"
        offer(
            "取消排序，保留当前分析目标",
            [{"intent_id": item.id, "operation": "unset", "field": "order"}],
            slot="order",
        )
    elif any(
        i.domain != "sales"
        and i.operation not in {None, "unknown"}
        and i.operation not in DOMAIN_OPERATIONS[i.domain]
        for i in context.intents
    ):
        item = next(
            i
            for i in context.intents
            if i.domain != "sales"
            and i.operation not in {None, "unknown"}
            and i.operation not in DOMAIN_OPERATIONS[i.domain]
        )
        field, kind = "operation", "capability_gap"
        question = resolution.semantic.message
        target_label = _target_label(item.target or "product")
        for op, label in [
            ("summary", "改看整体概览"),
            ("ranking", f"改看{target_label}排行"),
            ("list", f"改看{target_label}明细"),
        ]:
            # Preserve domain, metric, dates and object unless the chosen label explicitly
            # removes a grouping or comparison which the replacement cannot express.
            edits = [
                _set(item.id, "operation", op),
                {"intent_id": item.id, "operation": "unset", "field": "comparison_time"},
            ]
            changes = []
            if op != "ranking" and item.order is not None:
                edits.append({"intent_id": item.id, "operation": "unset", "field": "order"})
                changes.append("不再排序")
            if op == "summary":
                if item.target:
                    edits.append({"intent_id": item.id, "operation": "unset", "field": "target"})
                    changes.append(f"不再按{target_label}分组")
                if item.limit is not None:
                    edits.append({"intent_id": item.id, "operation": "unset", "field": "limit"})
                    changes.append(f"取消前{item.limit}项限制")
            if item.operation == "comparison" or item.comparison_time:
                changes.append("不再比较")
            if changes:
                label += "（" + "，".join(changes) + "）"
            offer(label, edits)
    else:
        pending = context.pending[0] if context.pending else "conditions"
        matching = [i for i in context.issues if i.field == pending]
        issue = (matching or context.issues or [None])[0]
        targeted_issue = issue is not None and any(i.id == issue.intent_id for i in context.intents)
        if issue is not None:
            issue_id = issue.id
            item = next((i for i in context.intents if i.id == issue.intent_id), item)
            start, end = _dates_for(report, item)
            field, kind, question = issue.field, issue.kind, issue.question
            # Only bounded, meaning-preserving single-field choices become buttons.
            if field in {"time", "comparison_time"}:
                offer("自己选择日期", action="date_range")
            for candidate in issue.choices[:3]:
                value = candidate.value
                if field in {"time", "comparison_time"}:
                    value = _valid_date_choice(item, field, value, start, end, today)
                    if value:
                        values(field, [(value, "改看 " + value)])
                    continue
                permitted = FIELD_VALUES.get(field, {})
                if value not in permitted:
                    continue
                if field == "metric" and (
                    value not in DOMAIN_METRICS[item.domain]
                    or (item.operation in {"comparison", "anomalies"} and value != "amount")
                ):
                    continue
                values(field, [(value, permitted[value])])
        else:
            field = pending
        only_date_picker = len(candidates) == 1 and candidates[0]["kind"] == "date_range"
        if (not candidates or only_date_picker) and kind != "dependency":
            if field in {"time", "comparison_time"}:
                candidates.clear()
                if not targeted_issue:
                    item = next((i for i in context.intents if not getattr(i, field)), item)
                start, end = _dates_for(report, item)
                question = (
                    question
                    if issue
                    else (
                        f"你想查看“{_label(item)}”的哪段时间？"
                        f"完整数据覆盖 {start} 至 {end - timedelta(days=1)}。"
                        if field == "time"
                        else "你想和哪段时间比较？两个区间需要等长且不重叠。"
                    )
                )
                if item.domain == "inventory" and field == "time":
                    question = (
                        f"当前仅有 {report.operations.inventory.as_of.isoformat()} 的库存时点快照。"
                        "要查看这份快照，还是指定其他时间？"
                    )
                dates()
            elif field == "operation":
                item = (
                    item
                    if targeted_issue
                    else next(
                        (
                            i
                            for i in context.intents
                            if i.operation
                            not in {"summary", "trend", "ranking", "comparison", "anomalies"}
                        ),
                        item,
                    )
                )
                if issue is None:
                    question = f"关于{DOMAINS.get(item.domain, '经营')}，你最想先了解哪个方面？"
                if item.limit is not None and item.operation not in {"ranking", "comparison"}:
                    question = f"已保留前 {item.limit} 项的要求，你想给商品还是客户排个名？"
                    for target, label in FIELD_VALUES["target"].items():
                        if target not in TARGETS.get(item.domain, {"product", "buyer"}):
                            continue
                        offer(
                            f"看{label}前 {item.limit} 名",
                            [
                                _set(item.id, "operation", "ranking"),
                                _set(item.id, "target", target),
                            ],
                        )
                else:
                    values(
                        "operation",
                        [
                            (op, label)
                            for op, label in [
                                ("summary", "先看整体概览"),
                                (
                                    "ranking",
                                    "看看"
                                    + ("客户" if item.target == "buyer" else "商品")
                                    + "排行",
                                ),
                                ("trend", "看看每日变化"),
                            ]
                            if item.domain == "sales" or op in DOMAIN_OPERATIONS[item.domain]
                        ],
                    )
            elif field == "metric":
                question = "你想用什么指标衡量这项业务？也可以说明你关心的衡量方式。"
                values(
                    "metric",
                    [
                        (m, _metric_label(item, m))
                        for m in ("amount", "orders", "stock", "quantity")
                        if m in DOMAIN_METRICS[item.domain]
                    ][:3],
                )
            elif field == "target":
                if targeted_issue:
                    question = issue.question
                    values("target", [("product", "看商品表现"), ("buyer", "看客户表现")])
                else:
                    question = "这次想修改哪个目标？可以说目标名称和需要调整的条件。"
            elif issue is None:
                known = context.unresolved + [s for i in context.intents for s in i.unresolved]
                question = (
                    f"还需要确认：{known[0]}。你希望怎样理解这个条件？" if known else question
                )

    # A choice can resolve its own field or an explicitly cancelled field on the same goal.
    # Cross-goal, unscoped cancellation and dependency issues require further user input.
    for candidate in candidates:
        cancelled = {
            edit["field"]
            for edit in candidate["edits"]
            if edit["operation"] == "unset" and edit["intent_id"] == candidate["intent_id"]
        }
        candidate["resolved_issue_ids"] = [
            issue.id
            for issue in context.issues
            if issue.id
            and (
                issue.field == candidate["field"]
                and issue.intent_id in {None, candidate["intent_id"]}
                or issue.field in cancelled
                and issue.intent_id == candidate["intent_id"]
            )
            and issue.kind != "dependency"
        ]
    key = f"{item.id}:{field}:{kind}"
    return item.id, field, kind, question, issue_id, key, candidates[:3]


def _apply_choice(body, previous, today, *, domains=("sales",)):
    if previous is None:
        raise DialogueChoiceUnavailable("这条建议已失效，请重新说明需求。")
    choice = next((c for c in previous.dialogue_choices if c.get("id") == body.choice_id), None)
    if choice is None or choice.get("dialogue_id") != previous.dialogue_id:
        raise DialogueChoiceUnavailable("这条建议不属于当前对话，请使用当前建议或直接补充文字。")
    if body.date_range and choice["kind"] != "date_range":
        raise DialogueChoiceUnavailable("这条建议不接受日期修改。")
    if choice["kind"] == "new_sales":
        request = SemanticRequest(intents=[{"domain": "sales", "operation": "summary"}])
        return compile_request(request, question="", today=today, trusted_dates=True)
    edits = choice["edits"]
    if choice["kind"] == "date_range":
        if body.date_range is None:
            raise DialogueChoiceUnavailable("请选择起止日期后继续。")
        edits = [
            _set(
                choice["intent_id"],
                choice["field"],
                canonical_period(
                    body.date_range.start,
                    body.date_range.end_exclusive,
                ),
            )
        ]
    request = SemanticRequest(
        mode="answer",
        edits=edits,
        resolved_issue_ids=choice.get("resolved_issue_ids", []),
    )
    return compile_request(
        request, question="", today=today, previous=previous, trusted_dates=True, domains=domains
    )


async def legacy_converse(
    body: ConversationRequest, report, planner, today: date, *, previous=None
):
    start, end = available_dates(report)
    analysis_body = AnalysisQuestion(question=body.question or "应用已选择的条件")
    if body.choice_id:
        compiled = _apply_choice(body, previous, today, domains=executable_domains(report))
        resolution = finish_compilation(compiled, analysis_body, report)
    else:
        resolution = await resolve_question(
            analysis_body, report, planner, today, conversation=previous
        )
    result = None
    if resolution.plan is not None:
        result = await run_in_threadpool(execute_analysis, report, resolution.plan)
        result = result.model_copy(update={"interpretation": resolution.interpretation})
    return build_turn(resolution, report, today, previous=previous, result=result)


def build_turn(resolution, report, today, *, previous=None, result=None):
    context = (
        resolution.semantic.context if resolution.semantic else context_from_plan(resolution.plan)
    )
    draft, summary, defaults = _draft(context)
    clarification, choices, stored = None, [], []
    key, attempts = None, 0
    if resolution.plan is not None:
        if result is None:
            raise ValueError("Executable resolution requires a computed result")
        status = "result"
    else:
        status = {
            "clarify": "needs_input",
            "unsupported": "capability_gap",
            "data_unavailable": "data_gap",
        }[resolution.semantic.status]
        intent_id, field, kind, question, issue_id, key, stored = _build_guidance(
            resolution, report, today
        )
        if kind == "capability_gap":
            status = "capability_gap"
        attempts = (
            previous.clarification_attempts + 1
            if previous and previous.clarification_key == key
            else 1
        )
        if attempts >= 2:
            question = "已保留上面的条件。你可以选择下方建议，或直接说明要修改的部分。 " + question
        clarification = Clarification(
            id=issue_id or "clarification_" + uuid4().hex[:12],
            intent_id=intent_id,
            field=field,
            kind=kind,
            question=question,
        )
    dialogue_id = "turn_" + uuid4().hex[:12]
    for candidate in stored:
        candidate.update(id="choice_" + uuid4().hex[:12], dialogue_id=dialogue_id)
        choices.append(
            DialogueChoice(
                id=candidate["id"],
                label=candidate["label"],
                action=ChoiceAction(
                    kind=candidate["kind"],
                    intent_id=candidate["intent_id"],
                    field=candidate["field"],
                ),
            )
        )
    context = context.model_copy(
        update={
            "dialogue_id": dialogue_id,
            "dialogue_choices": stored,
            "clarification_key": key,
            "clarification_attempts": attempts,
        }
    )
    selected = next(
        (i for i in context.intents if clarification and i.id == clarification.intent_id),
        context.intents[0],
    )
    start, end = _dates_for(report, selected)
    return DialogueOutcome(
        DialogueTurn(
            status=status,
            understood_summary=summary,
            draft=draft,
            clarification=clarification,
            choices=choices,
            applied_defaults=defaults,
            result=result,
            repeated_clarification=attempts >= 2,
            available_dates=AvailableDates(start=start, end_exclusive=end),
        ),
        context,
    )


async def converse(
    body, report, planner, today, *, previous=None, channel="web", owner="local-workspace"
):
    from app.orchestration.runtime import run_dialogue

    return await run_dialogue(
        body, report, planner, today, previous=previous, channel=channel, owner=owner
    )
