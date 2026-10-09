"""Bind semantic terms locally before a plan can reach any executor."""

from app.analysis.object_filters import (
    binding_key,
    binding_matches,
    entity_directory,
    resolve_entity,
)
from app.schemas.analytics import AnalysisPlan, ObjectFilter
from app.semantic.compiler import Compilation


def bind_entities(compiled, report):
    if compiled.plan is None:
        return compiled
    bindings, steps = [], []
    issue = None
    for intent, step in zip(compiled.context.intents, compiled.plan.steps, strict=True):
        filters = []
        for condition in intent.filters:
            directory = entity_directory(report, intent.domain, condition.field)
            prior = next(
                (
                    b
                    for b in compiled.context.entity_bindings
                    if binding_matches(b, intent, condition)
                ),
                None,
            )
            code = prior.get("code") if prior else None
            if code not in directory:
                code, candidates = resolve_entity(directory, condition.value)
            else:
                candidates = []
            if code is None:
                if issue is None:
                    issue = {
                        **binding_key(intent, condition),
                        "kind": "entity_ambiguous" if candidates else "entity_not_found",
                    }
                continue
            variant = prior.get("variant") if prior else None
            if variant and (
                step.kind not in {"growth", "price", "margin"} or condition.operator == "exclude"
            ):
                return Compilation(
                    "clarify",
                    compiled.context,
                    message="当前保留了品种规格限定，请重新说明新的完整分析问题。",
                )
            bindings.append(
                {
                    **binding_key(intent, condition),
                    "code": code,
                    **({"variant": variant} if variant else {}),
                }
            )
            filters.append(
                ObjectFilter(
                    field=condition.field, operator=condition.operator, code=code, variant=variant
                )
            )
        steps.append(step.model_copy(update={"filters": filters}))
    context = compiled.context.model_copy(
        update={"entity_bindings": bindings, "entity_issue": issue}
    )
    if issue:
        return Compilation("clarify", context, message="请确认筛选对象；其余条件已经保留。")
    return Compilation(compiled.status, context, AnalysisPlan(steps=steps), compiled.message)
