"""Merge semantic slots and compile only supported, fully specified sales requests."""

import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import date, timedelta
from typing import Literal
from uuid import uuid4

from pydantic import ValidationError

from app.capabilities.registry import (
    FILTER_FIELDS,
    MAX_STEPS,
    TARGETS,
    semantic_metrics,
    target_supported,
)
from app.capabilities.registry import METRICS as EXECUTABLE_METRICS
from app.capabilities.registry import OPERATIONS as BUSINESS_OPERATIONS
from app.schemas.analytics import AnalysisPlan, AnalysisStep
from app.semantic.catalog import load_catalog
from app.semantic.dates import (
    canonical_period,
    explicit_periods,
    resolve_period,
)
from app.semantic.schemas import (
    SemanticContext,
    SemanticFilter,
    SemanticIntent,
    SemanticIssue,
    SemanticRequest,
)


@dataclass(frozen=True)
class Compilation:
    status: Literal["ready", "clarify", "unsupported", "data_unavailable"]
    context: SemanticContext
    plan: AnalysisPlan | None = None
    message: str = ""


def context_from_plan(plan: AnalysisPlan) -> SemanticContext:
    intents = []
    for step in plan.steps:
        operation = "ranking" if step.kind in {"product_ranking", "buyer_ranking"} else step.kind
        intents.append(
            SemanticIntent(
                domain=step.domain,
                operation=operation,
                target=("buyer" if step.kind == "buyer_ranking" else step.dimension)
                if step.domain == "sales"
                or step.kind in {"product_ranking", "buyer_ranking", "comparison", "list"}
                else None,
                metric=step.metric,
                time=canonical_period(step.start_date, step.end_date_exclusive),
                comparison_time=canonical_period(
                    step.comparison_start_date, step.comparison_end_date_exclusive
                )
                if step.kind == "comparison"
                else None,
                limit=step.top_n,
                order=step.order,
                id=_new_id("goal"),
                filters=[
                    SemanticFilter(field=f.field, operator=f.operator, value=f.code)
                    for f in step.filters
                ],
            )
        )
    return SemanticContext(intents=intents)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


_SCALAR_FIELDS = set(SemanticIntent.model_fields) - {"id", "filters", "unresolved"}


class _TargetUnclear(ValueError):
    """The proposed semantic change is known, but its target is not yet selected."""


@dataclass
class _Merged:
    intents: list[SemanticIntent]
    # Model-supplied temporary IDs only address goals in this same request.
    aliases: dict[str, str]
    explicit: dict[str, set[str]]
    sources: dict[str, dict[str, str]]
    issues: list[SemanticIssue] = dataclass_field(default_factory=list)
    added_ids: set[str] = dataclass_field(default_factory=set)


def _present(item: SemanticIntent) -> set[str]:
    return {
        key
        for key, value in item.model_dump().items()
        if key != "id" and value is not None and value != []
    }


def _filters(values: list[SemanticFilter], *, preserve=False) -> list[SemanticFilter]:
    result = []
    seen = set()
    for value in values:
        key = (value.field, value.operator, value.value)
        if key in seen:
            continue
        seen.add(key)
        result.append(
            value.model_copy(
                update={"id": value.id if preserve and value.id else _new_id("condition")}
            )
        )
    return result


def _identified_context(context: SemanticContext) -> SemanticContext:
    """Even a rejected provider edit must yield a displayable, addressable draft."""
    return context.model_copy(
        update={
            "intents": [
                item.model_copy(
                    update={
                        "id": item.id or _new_id("goal"),
                        "filters": _filters(item.filters, preserve=True),
                    }
                )
                for item in context.intents
            ]
        }
    )


def _merge_pair(old: SemanticIntent, patch: SemanticIntent) -> SemanticIntent:
    same_domain = patch.domain in {None, old.domain}
    updates = {
        key: value
        for key, value in patch.model_dump().items()
        if key != "id" and value is not None and value != []
    }
    if same_domain:
        updates["filters"] = _filters(old.filters + _filters(patch.filters), preserve=True)
        updates["unresolved"] = list(dict.fromkeys(old.unresolved + patch.unresolved))
        # A different ranking object does not inherit a metric specific to the old object.
        if old.target is not None and patch.target not in {None, old.target}:
            updates.setdefault("metric", None)
            updates.setdefault("limit", None)
    else:
        updates = {
            **patch.model_dump(),
            "id": old.id,
            "time": patch.time or old.time,
            "scope": patch.scope or old.scope,
            "filters": _filters(patch.filters),
        }
    return SemanticIntent.model_validate({**old.model_dump(), **updates})


def _has_pending_limit(previous: SemanticContext | None, item: SemanticIntent) -> bool:
    return bool(
        previous
        and "operation" in previous.pending
        and item.limit is not None
        and previous.field_sources.get(item.id, {}).get("limit") != "default"
    )


def _merge(request: SemanticRequest, previous: SemanticContext | None) -> _Merged:
    if request.add_intents and (request.mode != "followup" or previous is None):
        raise ValueError("请在已有分析草稿上明确追加目标，或重新说明完整的新问题。")
    if request.mode == "new" or previous is None:
        if request.edits or request.add_intents:
            raise ValueError("需要先有已确认的分析草稿，才能修改指定目标或条件。")
        intents, aliases, explicit, sources = [], {}, {}, {}
        for item in request.intents:
            identifier = _new_id("goal")
            if item.id:
                if item.id in aliases:
                    raise ValueError("多个分析目标的标识重复，请明确要修改的目标。")
                aliases[item.id] = identifier
            intents.append(
                item.model_copy(update={"id": identifier, "filters": _filters(item.filters)})
            )
            explicit[identifier] = _present(item)
            sources[identifier] = {field: "explicit" for field in explicit[identifier]}
        return _Merged(intents, aliases, explicit, sources)

    legacy = all(item.id is None for item in previous.intents)
    intents = [
        item.model_copy(
            update={
                "id": item.id or _new_id("goal"),
                "filters": _filters(item.filters, preserve=True),
            }
        )
        for item in previous.intents
    ]
    aliases = {item.id: item.id for item in intents}
    explicit = {item.id: set() for item in intents}
    sources = {
        item.id: {
            field: "default"
            if previous.field_sources.get(item.id, {}).get(field) == "default"
            else "inherited"
            for field in _present(item)
        }
        for item in intents
    }
    addressed = set()
    for index, patch in enumerate(request.intents):
        if (request.edits or request.add_intents) and not patch.id and not _present(patch):
            continue
        if patch.id:
            matches = [n for n, item in enumerate(intents) if item.id == patch.id]
        elif len(intents) == 1:
            matches = [0]
        elif legacy and len(request.intents) == len(intents):
            matches = [index]
        elif any(getattr(patch, field) for field in ("domain", "operation", "target")):
            matches = [
                n
                for n, item in enumerate(intents)
                if all(
                    getattr(patch, field) in {None, getattr(item, field)}
                    for field in ("domain", "operation", "target")
                )
            ]
        else:
            matches = []
        if len(matches) != 1:
            raise _TargetUnclear("已保留各分析目标；请说明这次要修改哪一项。")
        selected = matches[0]
        old = intents[selected]
        if old.id in addressed:
            raise ValueError("同一分析目标收到多个补丁，请合并说明这次的修改。")
        addressed.add(old.id)
        intents[selected] = _merge_pair(old, patch)
        if (
            _has_pending_limit(previous, old)
            and patch.limit is None
            and intents[selected].limit is None
            and patch.domain in {None, old.domain}
        ):
            intents[selected] = intents[selected].model_copy(update={"limit": old.limit})
        explicit[old.id].update(_present(patch))
        sources[old.id].update({field: "explicit" for field in _present(patch)})

    for edit in request.edits:
        matches = [n for n, item in enumerate(intents) if item.id == edit.intent_id]
        if len(matches) != 1:
            raise _TargetUnclear("要修改的分析目标已变化，请重新选择草稿中的目标。")
        index = matches[0]
        old = intents[index]
        values = old.model_dump()
        if edit.operation == "remove_intent":
            if len(intents) == 1:
                raise ValueError("请至少保留一个分析目标，或直接说明新的问题。")
            del intents[index]
            continue
        if edit.operation in {"set", "unset"}:
            if edit.field not in _SCALAR_FIELDS:
                raise ValueError("这个条件不能直接修改，请说明具体分析要求。")
            values[edit.field] = edit.value if edit.operation == "set" else None
            if edit.operation == "set" and edit.value is None:
                raise ValueError("请明确要改成什么条件，或明确取消该条件。")
            explicit[old.id].add(edit.field)
            sources[old.id][edit.field] = "explicit"
        elif edit.operation == "add_filter":
            if edit.filter is None:
                raise ValueError("请说明要增加的筛选条件。")
            values["filters"] = _filters(old.filters + _filters([edit.filter]), preserve=True)
            explicit[old.id].add("filters")
            sources[old.id]["filters"] = "explicit"
        else:
            constraints = [
                n for n, value in enumerate(old.filters) if value.id == edit.constraint_id
            ]
            if not edit.constraint_id or len(constraints) != 1:
                raise ValueError("要修改的筛选条件已变化，请重新选择草稿中的条件。")
            changed = list(old.filters)
            if edit.operation == "remove_filter":
                del changed[constraints[0]]
            else:
                if edit.filter is None:
                    raise ValueError("请说明筛选条件要改成什么。")
                changed[constraints[0]] = edit.filter.model_copy(update={"id": edit.constraint_id})
            values["filters"] = changed
            explicit[old.id].add("filters")
            sources[old.id]["filters"] = "explicit"
        try:
            intents[index] = SemanticIntent.model_validate(values)
        except ValidationError as exc:
            raise ValueError("修改后的条件无效；已保留原草稿，请重新说明该条件。") from exc
    if len(intents) + len(request.add_intents) > MAX_STEPS:
        raise ValueError("一次最多分析6个目标；已保留原草稿，请先移除不需要的目标再添加。")
    issues = []
    added_ids = set()
    shared = list(intents)
    for addition in request.add_intents:
        identifier = _new_id("goal")
        added_ids.add(identifier)
        if addition.id:
            if addition.id in aliases:
                raise ValueError("新增目标不能复用已有目标标识，请明确要新增还是修改。")
            aliases[addition.id] = identifier
        updates = {"id": identifier}
        explicit[identifier] = _present(addition)
        sources[identifier] = {name: "explicit" for name in explicit[identifier]}
        for name in ("time", "scope", "generic_product_scope"):
            if getattr(addition, name) is not None:
                continue
            inherited_values = {getattr(item, name) for item in shared}
            if len(inherited_values) == 1 and None not in inherited_values:
                updates[name] = next(iter(inherited_values))
                sources[identifier][name] = "inherited"
            elif name == "scope" and len(inherited_values) > 1:
                issues.append(
                    SemanticIssue(
                        id=_new_id("issue"),
                        intent_id=identifier,
                        field="scope",
                        question="已有目标的查询范围不同，新增目标希望沿用哪个范围？",
                    )
                )
        filter_groups = {
            tuple(sorted((f.field, f.operator, f.value) for f in item.filters)) for item in shared
        }
        additions_filters = _filters(addition.filters)
        if len(filter_groups) == 1:
            updates["filters"] = _filters(
                [*_filters(shared[0].filters), *additions_filters], preserve=True
            )
            if shared[0].filters and not addition.filters:
                sources[identifier]["filters"] = "inherited"
        else:
            updates["filters"] = additions_filters
            issues.append(
                SemanticIssue(
                    id=_new_id("issue"),
                    intent_id=identifier,
                    field="filters",
                    question="已有目标的筛选条件不同，新增目标希望沿用哪些条件？原条件已保留。",
                )
            )
        intents.append(SemanticIntent.model_validate({**addition.model_dump(), **updates}))
    return _Merged(intents, aliases, explicit, sources, issues, added_ids)


def _merge_issues(request: SemanticRequest, previous: SemanticContext | None, merged: _Merged):
    old_issues = previous.issues if previous and request.mode != "new" else []
    resolved = set(request.resolved_issue_ids)
    resolved_text = set(request.resolved_conditions)
    resolved_legacy = {
        (issue.intent_id, issue.question) for issue in old_issues if issue.id in resolved
    }
    valid_ids = {item.id for item in merged.intents}
    issues = []
    for issue in old_issues:
        if issue.id in resolved or issue.question in resolved_text:
            continue
        if issue.kind == "missing" and issue.field == "operation":
            goals = [
                goal
                for goal in merged.intents
                if goal.id == issue.intent_id
                or issue.intent_id is None
                and len(merged.intents) == 1
            ]
            if any(
                goal.limit is None
                and "limit" in merged.explicit.get(goal.id, set())
                and any(
                    old.id == goal.id and _has_pending_limit(previous, old)
                    for old in previous.intents
                )
                for goal in goals
            ):
                continue
        # Explicitly completed mandatory fields do not need a second confirmation.
        if issue.kind == "missing" and issue.intent_id in valid_ids:
            goal = next(item for item in merged.intents if item.id == issue.intent_id)
            value = getattr(goal, issue.field, None)
            if (
                issue.field in merged.explicit.get(goal.id, set())
                and value is not None
                and value != "unknown"
                and value != []
            ):
                continue
        if issue.intent_id not in valid_ids and issue.kind != "dependency":
            if issue.intent_id is not None:
                continue
        issues.append(issue)
    issues.extend(merged.issues)
    for issue in request.issues:
        intent_id = merged.aliases.get(issue.intent_id, issue.intent_id)
        if intent_id is None and len(merged.intents) == 1:
            intent_id = merged.intents[0].id
        if intent_id is not None and intent_id not in valid_ids:
            raise ValueError("待补充条件没有对应到明确的分析目标，请说明要修改哪一项。")
        matching = next((old for old in issues if issue.id and old.id == issue.id), None)
        matching = matching or next(
            (
                old
                for old in issues
                if (old.intent_id, old.field, old.kind) == (intent_id, issue.field, issue.kind)
                and (
                    issue.field in _SCALAR_FIELDS
                    and issue.kind != "dependency"
                    or old.question == issue.question
                )
            ),
            None,
        )
        incoming = issue.model_copy(
            update={"id": matching.id if matching else _new_id("issue"), "intent_id": intent_id}
        )
        if matching:
            issues[issues.index(matching)] = incoming
        else:
            issues.append(incoming)
    inherited = previous.unresolved if previous and request.mode != "new" else []
    unresolved = list(
        dict.fromkeys(
            s
            for s in inherited + request.unresolved
            if s not in resolved_text and (None, s) not in resolved_legacy
        )
    )
    intents = [
        item.model_copy(
            update={
                "unresolved": [
                    s
                    for s in item.unresolved
                    if s not in resolved_text and (item.id, s) not in resolved_legacy
                ]
            }
        )
        for item in merged.intents
    ]
    # Old integrations still send text-only constraints. Give them IDs without interpreting text.
    for intent_id, conditions in [(None, unresolved)] + [(i.id, i.unresolved) for i in intents]:
        for text in conditions:
            if not any(issue.question == text and issue.intent_id == intent_id for issue in issues):
                issues.append(
                    SemanticIssue(
                        id=_new_id("issue"), intent_id=intent_id, field="conditions", question=text
                    )
                )
    return intents, issues, unresolved


def _stated_periods(question: str, today: date, request: SemanticRequest):
    # Keep current-time evidence even when a follow-up incorrectly repeats an old date.
    # With an explicit historical period, the model distinguishes current-time goals from
    # conversational framing ("now please show September 15").
    periods = explicit_periods(question, today)
    proposed_dates = [
        getattr(item, field)
        for item in request.intents + request.add_intents
        for field in ("time", "comparison_time")
    ] + [
        edit.value
        for edit in request.edits
        if edit.operation == "set" and edit.field in {"time", "comparison_time"}
    ]
    include_current = not periods
    for value in proposed_dates:
        if isinstance(value, str):
            try:
                include_current |= resolve_period(value, today) == (
                    today,
                    today + timedelta(days=1),
                )
            except (ValueError, OverflowError):
                pass
    if include_current:
        periods = explicit_periods(question, today, include_current=True)
    if not periods and re.fullmatch(r"[\d零〇一二三四五六七八九十两]+天", question.strip()):
        periods = [resolve_period(question.strip(), today)]
    return periods


def _pending_dates(previous: SemanticContext | None, today: date):
    """Only canonical dates retained by the program may support a later addressed patch."""
    dates = {"time": set(), "comparison_time": set()}
    pending = previous.pending_request if previous else None
    if pending is None:
        return dates
    values = [
        (name, getattr(item, name))
        for item in pending.intents + pending.add_intents
        for name in dates
    ]
    values.extend(
        (edit.field, edit.value)
        for edit in pending.edits
        if edit.operation == "set" and edit.field in dates
    )
    for name, value in values:
        if not isinstance(value, str):
            continue
        try:
            period = resolve_period(value, today)
            if value == canonical_period(*period):
                dates[name].add(period)
        except (ValueError, OverflowError):
            continue
    return dates


def _shared_period(intents, field, today):
    periods = set()
    for intent in intents:
        value = getattr(intent, field)
        if value is None:
            return None
        try:
            periods.add(resolve_period(value, today))
        except (ValueError, OverflowError):
            return None
    return next(iter(periods)) if len(periods) == 1 else None


def _verified_pending_request(request, question, today, previous):
    """Remember semantic changes without laundering provider-invented dates into signed state."""
    try:
        periods = set(_stated_periods(question, today, request))
    except (ValueError, OverflowError):
        periods = set()
    pending_dates = _pending_dates(previous, today)

    def checked(name, value):
        if not isinstance(value, str):
            return None
        try:
            period = resolve_period(value, today)
            return canonical_period(*period) if period in periods | pending_dates[name] else None
        except (ValueError, OverflowError):
            return None

    def checked_intents(intents):
        return [
            item.model_copy(
                update={name: checked(name, getattr(item, name)) for name in pending_dates}
            )
            for item in intents
        ]

    edits = []
    for edit in request.edits:
        if edit.operation == "set" and edit.field in pending_dates:
            value = checked(edit.field, edit.value)
            if value is None:
                continue
            edit = edit.model_copy(update={"value": value})
        edits.append(edit)
    if not request.intents and not request.add_intents and not edits:
        return None
    return request.model_copy(
        update={
            "intents": checked_intents(request.intents),
            "add_intents": checked_intents(request.add_intents),
            "edits": edits,
        }
    )


def compile_request(
    request: SemanticRequest,
    *,
    question: str,
    today: date,
    previous: SemanticContext | None = None,
    trusted_dates: bool = False,
    domains: list[str] | tuple[str, ...] = ("sales",),
) -> Compilation:
    catalog = load_catalog()
    if previous and previous.requires_restatement and request.mode != "new" and not trusted_dates:
        return Compilation(
            "clarify",
            _identified_context(previous),
            message="上次问题有未核验的条件，请重新说明完整问题。",
        )
    try:
        merged = _merge(request, previous)
        intents, issues, unresolved = _merge_issues(request, previous, merged)
    except ValueError as exc:
        active_previous = previous if request.mode != "new" else None
        context = _identified_context(
            active_previous or SemanticContext(intents=request.intents or [SemanticIntent()])
        )
        issue = SemanticIssue(id=_new_id("issue"), field="target", question=str(exc))
        return Compilation(
            "clarify",
            context.model_copy(
                update={
                    "pending": ["target"],
                    "issues": [issue, *[i for i in context.issues if i.field != "target"]],
                    "pending_request": _verified_pending_request(
                        request, question, today, active_previous
                    )
                    if active_previous and isinstance(exc, _TargetUnclear)
                    else context.pending_request,
                }
            ),
            message=str(exc),
        )
    scope_note = any(i.generic_product_scope for i in intents)
    stated_periods = []
    invalid_date_expression = False
    if not trusted_dates:
        try:
            stated_periods = _stated_periods(question, today, request)
        except (ValueError, OverflowError):
            invalid_date_expression = True
    frozen = []
    verified_pending_dates = _pending_dates(previous if request.mode != "new" else None, today)
    for item in intents:
        updates = {}
        for field in ("time", "comparison_time"):
            value = getattr(item, field)
            if value:
                try:
                    period = resolve_period(value, today)
                    canonical = canonical_period(*period)
                    inherited = False
                    if previous and request.mode != "new":
                        candidates = [old for old in previous.intents if old.id == item.id]
                        if item.id in merged.added_ids:
                            # Models may repeat an already verified shared absolute date.
                            inherited = field == "time" and period == _shared_period(
                                previous.intents, field, today
                            )
                            if inherited and period not in stated_periods:
                                merged.sources[item.id][field] = "inherited"
                                merged.explicit[item.id].discard(field)
                        elif not candidates and all(old.id is None for old in previous.intents):
                            candidates = previous.intents
                        if (
                            not candidates
                            and field == "time"
                            and merged.sources[item.id].get(field) == "inherited"
                        ):
                            # A newly appended goal may share only the unique common prior date.
                            old_dates = {old.time for old in previous.intents}
                            if len(old_dates) == 1 and None not in old_dates:
                                candidates = previous.intents
                        inherited = inherited or any(
                            getattr(old, field)
                            and canonical_period(*resolve_period(getattr(old, field), today))
                            == canonical
                            for old in candidates
                        )
                    # Pending/unsupported drafts must not turn invented dates into trusted state.
                    updates[field] = (
                        canonical
                        if (
                            trusted_dates
                            or period in stated_periods
                            or inherited
                            or field in merged.explicit[item.id]
                            and period in verified_pending_dates[field]
                        )
                        else None
                    )
                except (ValueError, OverflowError):
                    pass
        frozen.append(item.model_copy(update=updates))
    intents = frozen
    context = SemanticContext(
        intents=frozen,
        product_scope_note=scope_note,
        catalog_version=catalog["version"],
        unresolved=unresolved,
        issues=issues,
        field_sources={i.id: merged.sources[i.id] for i in intents},
        clarification_key=previous.clarification_key
        if previous and request.mode != "new"
        else None,
        clarification_attempts=previous.clarification_attempts
        if previous and request.mode != "new"
        else 0,
        entity_bindings=previous.entity_bindings if previous and request.mode != "new" else [],
    )

    def stop(status, message, pending=(), *, restate=False, intent_id=None):
        current_issues = list(context.issues)
        if (
            status == "clarify"
            and pending
            and not any(issue.field in pending for issue in current_issues)
        ):
            current_issues.append(
                SemanticIssue(
                    id=_new_id("issue"),
                    intent_id=intent_id,
                    field=pending[0],
                    kind="ambiguous" if restate else "missing",
                    question=message,
                )
            )
        return Compilation(
            status,
            context.model_copy(
                update={
                    "pending": list(pending),
                    "requires_restatement": restate,
                    "issues": current_issues,
                }
            ),
            message=message,
        )

    missing_domains = [i for i in intents if i.domain in {None, "unknown"}]
    if missing_domains:
        return stop(
            "clarify",
            "你想了解销售、退货、出库还是库存？",
            ["domain"],
            intent_id=missing_domains[0].id,
        )
    unavailable = list(dict.fromkeys(i.domain for i in intents if i.domain not in domains))
    if unavailable:
        message = "\n".join(
            catalog["domains"][d].get(
                "unavailable_message", f"{catalog['domains'][d]['label']}当前不可执行。"
            )
            for d in unavailable
        )
        if len(intents) > 1:
            message += "\n本次组合问题未执行；你可以单独询问已支持的销售分析。"
        return stop("unsupported", message)
    unsupported_targets = list(
        dict.fromkeys(
            i.target for i in intents if i.target is not None and i.target not in TARGETS[i.domain]
        )
    )
    if unsupported_targets:
        labels = catalog.get("targets", {})
        dimensions = "、".join(labels.get(target, target) for target in unsupported_targets)
        return stop(
            "unsupported",
            f"已理解按{dimensions}分析的要求；销售、退货和销售出库支持商品和客户维度，库存支持商品维度，"
            "尚未接入请求的分组维度，不能替换维度后执行。",
        )
    unsupported_grouping = [
        i for i in intents if not target_supported(i.domain, i.operation, i.target)
    ]
    if unsupported_grouping:
        item = unsupported_grouping[0]
        return stop(
            "unsupported",
            "已保留按对象分组的要求；当前此分析方式未接入分组能力，可明确改看对象排行。",
            ["target"],
            intent_id=item.id,
        )
    if any(f.field not in FILTER_FIELDS[i.domain] for i in intents for f in i.filters):
        return stop(
            "unsupported",
            "已保留筛选条件；当前支持商品和客户筛选，库存仅支持商品，类别、仓库等尚未接入。",
        )
    unsupported_metrics = [
        i
        for i in intents
        if i.metric not in {None, "unknown"} and i.metric not in EXECUTABLE_METRICS[i.domain]
    ]
    if unsupported_metrics:
        item = unsupported_metrics[0]
        return stop(
            "unsupported",
            "已理解该指标；当前销售执行器支持有效订单金额和订单数，数量单位、利润及资金事件尚未接入。"
            if item.domain == "sales"
            else (
                f"已理解{catalog['domains'][item.domain]['label']}指标；当前支持的口径见业务说明，"
                "退货率、资金退款及库存周转尚未接入，不能替换后执行。"
            ),
        )
    missing_operations = [
        i
        for i in intents
        if i.operation in {None, "unknown"}
        or i.domain == "sales"
        and i.operation not in BUSINESS_OPERATIONS["sales"]
    ]
    if missing_operations:
        return stop(
            "clarify",
            "你希望先了解整体概况、变化趋势，还是商品表现？",
            ["operation"],
            intent_id=missing_operations[0].id,
        )
    if any(i.metric == "unknown" for i in intents):
        return stop(
            "clarify",
            "你想用什么指标衡量这项业务？",
            ["metric"],
            intent_id=next(i.id for i in intents if i.metric == "unknown"),
        )
    if context.issues:
        issue = context.issues[0]
        return stop("clarify", issue.question, [issue.field], intent_id=issue.intent_id)
    unsupported_operations = [
        i
        for i in intents
        if i.domain != "sales" and i.operation not in BUSINESS_OPERATIONS[i.domain]
    ]
    if unsupported_operations:
        item = unsupported_operations[0]
        return stop(
            "unsupported",
            f"{catalog['domains'][item.domain]['label']}目前不支持此分析方式；"
            + (
                "库存仅有单时点余额，未接入历史趋势或周转。"
                if item.domain == "inventory"
                else "可选择当前已接入的概览、趋势、排行或明细。"
            ),
            ["operation"],
            intent_id=item.id,
        )
    if any(i.order == "ascending" and i.operation != "ranking" for i in intents):
        item = next(i for i in intents if i.order == "ascending" and i.operation != "ranking")
        return stop("unsupported", "当前只有排行接受排序方向。", ["order"], intent_id=item.id)
    if invalid_date_expression:
        return stop("clarify", "日期无效或范围不明确，请提供有效的起止日期。", ["time"])
    steps, resolved = [], []
    for item in intents:
        if not item.time:
            return stop(
                "clarify", "你希望查看哪一天或哪段时间的日期范围？", ["time"], intent_id=item.id
            )
        try:
            start, end = resolve_period(item.time, today)
        except (ValueError, OverflowError):
            return stop("clarify", "请明确统计日期或天数，例如“九月十五号”或“最近三天”。", ["time"])
        inherited_time = bool(
            previous
            and request.mode != "new"
            and "time" not in merged.explicit[item.id]
            and item.time in {i.time for i in previous.intents}
        )
        if (
            not trusted_dates
            and stated_periods
            and (start, end) not in stated_periods
            and not inherited_time
        ):
            return stop(
                "clarify",
                "识别到的日期与原问题不一致，请重新说明完整问题。",
                ["time"],
                restate=True,
                intent_id=item.id,
            )
        if item.operation not in BUSINESS_OPERATIONS[item.domain]:
            return stop(
                "clarify",
                "销售目前支持概览、趋势、商品/客户排行、期间比较和日波动，请明确分析目标。",
                ["operation"],
                intent_id=item.id,
            )
        metric = item.metric or catalog["domains"][item.domain]["default_metric"]
        limit = item.limit or catalog["defaults"]["ranking_limit"]
        kind = (
            f"{item.target or 'product'}_ranking" if item.operation == "ranking" else item.operation
        )
        if metric not in semantic_metrics(item.domain, item.operation):
            return stop("unsupported", "当前该分析类型仅支持金额口径，不能忽略你明确指定的指标。")
        # Summary already returns order_count alongside amount; keep the requested semantic metric.
        values = dict(
            domain=item.domain,
            kind=kind,
            start_date=start,
            end_date_exclusive=end,
            metric="amount" if kind == "summary" and item.domain == "sales" else metric,
            order=item.order or "descending",
            filters=[dict(field=f.field, operator=f.operator, code=f.value) for f in item.filters],
        )
        if kind == "list" and item.domain != "sales":
            values["dimension"] = item.target or "product"
        if kind in {"product_ranking", "buyer_ranking", "comparison", "list"}:
            values["top_n"] = limit
        elif item.limit is not None and (
            request.mode == "new"
            or "limit" in merged.explicit[item.id]
            or previous
            and request.mode != "new"
            and any(
                old.id == item.id and _has_pending_limit(previous, old) for old in previous.intents
            )
        ):
            return stop(
                "clarify",
                "排行条数需要对应商品或客户排行，请明确分析目标。",
                ["operation"],
                intent_id=item.id,
            )
        comparison_time = item.comparison_time
        if kind == "comparison":
            if not comparison_time:
                return stop(
                    "clarify",
                    "请补充比较期的起止日期；两个期间需要等长且不重叠。",
                    ["comparison_time"],
                    intent_id=item.id,
                )
            try:
                before, until = resolve_period(comparison_time, today)
            except (ValueError, OverflowError):
                return stop("clarify", "比较期日期无效，请提供明确起止日期。", ["comparison_time"])
            inherited_comparison = bool(
                previous
                and request.mode != "new"
                and "comparison_time" not in merged.explicit[item.id]
                and item.comparison_time in {i.comparison_time for i in previous.intents}
            )
            if (
                not trusted_dates
                and stated_periods
                and (before, until) not in stated_periods
                and not inherited_comparison
            ):
                return stop(
                    "clarify", "比较期与原问题不一致，请重新说明两个期间。", ["comparison_time"]
                )
            values.update(
                comparison_start_date=before,
                comparison_end_date_exclusive=until,
                dimension=item.target or "product",
            )
            comparison_time = canonical_period(before, until)
        elif comparison_time:
            return stop("clarify", "问题包含比较期，请明确是否需要期间比较。", ["operation"])
        try:
            steps.append(AnalysisStep(**values))
        except ValidationError:
            return stop(
                "clarify", "日期范围须为1至90天；比较期须等长且不重叠，排行最多50项。", ["time"]
            )
        resolved.append(
            item.model_copy(
                update={
                    "time": canonical_period(start, end),
                    "comparison_time": comparison_time,
                    "metric": metric,
                    "limit": limit
                    if kind.endswith("ranking") or kind in {"comparison", "list"}
                    else None,
                    "target": item.target or "product" if kind.endswith("ranking") else item.target,
                }
            )
        )
    # One explicit period may be shared; multiple periods cannot disappear from a plan.
    used = {(s.start_date, s.end_date_exclusive) for s in steps}
    used.update(
        (s.comparison_start_date, s.comparison_end_date_exclusive)
        for s in steps
        if s.kind == "comparison"
    )
    if any(period not in used for period in stated_periods):
        return stop(
            "clarify", "问题包含多个期间，未能完整对应各分析目标，请明确对应关系。", ["time"]
        )
    context = context.model_copy(update={"intents": resolved, "pending": []})
    sources = {identifier: dict(fields) for identifier, fields in context.field_sources.items()}
    for before, after in zip(intents, resolved, strict=True):
        for field in ("metric", "target", "limit"):
            if getattr(before, field) is None and getattr(after, field) is not None:
                sources[after.id][field] = "default"
    context = context.model_copy(update={"field_sources": sources})
    return Compilation("ready", context, AnalysisPlan(steps=steps))
