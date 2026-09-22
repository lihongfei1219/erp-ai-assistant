"""Labelled multi-turn acceptance against the real dialogue/compiler/executors."""

from collections import Counter
from datetime import date
from itertools import permutations
from math import ceil
from time import monotonic
from typing import Literal

from pydantic import Field, model_validator

from app.analysis.dialogue import converse
from app.schemas.analytics import AnalysisStep
from app.schemas.sales import StrictModel
from app.semantic.catalog import load_catalog
from app.semantic.dates import resolve_period
from app.semantic.dialogue_schemas import ConversationRequest
from app.semantic.provider import SemanticPlanner, SemanticProviderUnavailable
from app.semantic.schemas import SemanticIntent, SemanticRequest


class Expected(StrictModel):
    status: Literal["result", "needs_input", "capability_gap", "data_gap"]
    intents: list[dict] = Field(min_length=1, max_length=6)
    clarification_field: str | None = None
    plan: list[AnalysisStep] = Field(default_factory=list, max_length=6)
    ignore_plan_metric: bool = False

    @model_validator(mode="after")
    def check_contract(self):
        for intent in self.intents:
            SemanticIntent.model_validate(intent)
            if not intent.get("domain") or not intent.get("operation"):
                raise ValueError("Expected intents require domain and operation")
            if "id" in intent:
                raise ValueError("Expected goals must not depend on generated IDs")
        if (self.status == "result") != bool(self.plan):
            raise ValueError("Only result expectations require an exact execution plan")
        if (self.status == "result") == bool(self.clarification_field):
            raise ValueError("Guidance requires a clarification field; results do not")
        if self.ignore_plan_metric and (
            not self.plan
            or any(s.kind not in {"list", "existence"} for s in self.plan)
            or any("metric" in i for i in self.intents)
        ):
            raise ValueError("Only metric-unspecified lists/existence may ignore plan metrics")
        return self


class Turn(StrictModel):
    question: str = Field(min_length=1, max_length=1000)
    replay: SemanticRequest
    expected: Expected
    alternatives: list[Expected] = Field(default_factory=list, max_length=3)


class Case(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")
    source: Literal["user_screenshot", "synthetic"]
    tags: list[str] = Field(min_length=1)
    turns: list[Turn] = Field(min_length=1, max_length=12)


class Corpus(StrictModel):
    version: Literal["1"]
    today: date
    cases: list[Case] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Case IDs must be unique")
        return self


class ReplayProvider:
    """Supply labelled semantics, never interpret or keyword-match a user's question."""

    def __init__(self, case):
        self.turns = iter(case.turns)

    async def interpret(self, request):
        semantic = next(self.turns).replay.model_copy(deep=True)
        # Corpus references identify a unique existing domain, not unstable generated IDs.
        edits = []
        for edit in semantic.edits:
            if edit.intent_id.startswith("@"):
                matches = [
                    i
                    for i in (request.previous.intents if request.previous else [])
                    if i.domain == edit.intent_id[1:]
                ]
                if len(matches) != 1:
                    raise ValueError("Replay edit needs one matching previous goal")
                edit = edit.model_copy(update={"intent_id": matches[0].id})
            edits.append(edit)
        return semantic.model_copy(update={"edits": edits})


def _field_value(key, value, today):
    if key in {"time", "comparison_time"} and value is not None:
        try:
            return resolve_period(value, today)
        except ValueError:
            return value
    if key == "filters":
        return sorted((f["field"], f.get("operator", "equal"), f["value"]) for f in value)
    return value


def _intent_failures(actual, expected, today):
    if len(actual) != len(expected):
        return ["intent.count"]
    best = None
    domains = load_catalog()["domains"]
    # At most six goals. Exact bijection avoids greedy matching of similar goals.
    for ordering in permutations(actual):
        failures = set()
        for goal, label in zip(ordering, expected, strict=True):
            values = goal.model_dump(mode="json")
            for key, value in {"filters": [], **label}.items():
                if key == "target" and goal.operation == label["operation"] == "ranking":
                    value = "product" if value is None else value
                    values[key] = "product" if values[key] is None else values[key]
                if key == "metric" and goal.domain == label["domain"]:
                    default = domains.get(goal.domain, {}).get("default_metric")
                    value = default if value is None else value
                    values[key] = default if values[key] is None else values[key]
                    if goal.domain == "inventory":
                        value = "stock" if value == "quantity" else value
                        values[key] = "stock" if values[key] == "quantity" else values[key]
                if _field_value(key, values[key], today) != _field_value(key, value, today):
                    failures.add("intent." + key)
        if best is None or len(failures) < len(best):
            best = failures
        if not best:
            break
    return sorted(best)


def _plan_key(step, ignore_metric=False):
    # Inventory quantities have two equivalent public metric names.
    data = step.model_dump(mode="json")
    if data["domain"] == "inventory" and data["metric"] == "quantity":
        data["metric"] = "stock"
    if ignore_metric:
        data.pop("metric")
    return repr(sorted(data.items()))


def assess(outcome, expected, today):
    turn = outcome.turn
    failures = _intent_failures(outcome.context.intents, expected.intents, today)
    if turn.status != expected.status:
        failures.append("status")
    field = turn.clarification.field if turn.clarification else None
    if field != expected.clarification_field:
        failures.append("clarification_field")
    actual_plan = turn.result.plan.steps if turn.result else []
    if sorted(_plan_key(s, expected.ignore_plan_metric) for s in actual_plan) != sorted(
        _plan_key(s, expected.ignore_plan_metric) for s in expected.plan
    ):
        failures.append("plan")
    if turn.result:
        outputs = Counter((r.domain, r.kind) for r in turn.result.results)
        if outputs != Counter((s.domain, s.kind) for s in actual_plan):
            failures.append("result_coverage")
    if not turn.allow_free_text:
        failures.append("free_text")
    return failures


async def evaluate(corpus, report, *, provider_factory=None, progress=None):
    """No source I/O. Reports omit question text, responses, credentials and result rows."""
    if report.metadata.source_kind != "synthetic":
        raise ValueError("Evaluation requires a synthetic report")
    rows = []
    for case in corpus.cases:
        provider = provider_factory() if provider_factory else ReplayProvider(case)
        planner = SemanticPlanner(provider=provider)
        previous = None
        blocked = False
        turns = []
        for index, label in enumerate(case.turns):
            row = {"turn": index + 1, "expected_status": label.expected.status}
            if blocked:
                row.update(verdict="skipped", reason="prerequisite_failed")
                turns.append(row)
                continue
            started = monotonic()
            try:
                outcome = await converse(
                    ConversationRequest(question=label.question),
                    report,
                    planner,
                    corpus.today,
                    previous=previous,
                )
                variants = [label.expected, *label.alternatives]
                checks = [assess(outcome, expected, corpus.today) for expected in variants]
                variant = min(range(len(checks)), key=lambda index: len(checks[index]))
                failures = checks[variant]
                field = outcome.turn.clarification.field if outcome.turn.clarification else None
                known_fields = set(SemanticIntent.model_fields) | {"conditions"}
                row.update(
                    expected_status=variants[variant].status,
                    accepted_variant=variant if not failures else None,
                    verdict="failed" if failures else "passed",
                    failures=failures,
                    actual_status=outcome.turn.status,
                    repeated_clarification=outcome.turn.repeated_clarification,
                    actual_clarification_field=field
                    if field in known_fields or field is None
                    else "other",
                    actual_intents=[
                        {
                            "domain": i.domain,
                            "operation": i.operation,
                            "metric": i.metric,
                            "target": i.target
                            if i.target
                            in {
                                None,
                                "product",
                                "buyer",
                                "region",
                                "category",
                                "warehouse",
                                "supplier",
                            }
                            else "other",
                            "filters": [
                                {"field": f.field, "operator": f.operator} for f in i.filters
                            ],
                        }
                        for i in outcome.context.intents
                    ],
                )
                previous = outcome.context
            except SemanticProviderUnavailable as exc:
                known = {
                    "timeout",
                    "authentication",
                    "rate_limit",
                    "upstream",
                    "invalid_response",
                    "connection",
                    "unavailable",
                }
                row.update(
                    verdict="failed", error=exc.reason if exc.reason in known else "unavailable"
                )
            except Exception:  # noqa: BLE001 -- never expose exception payloads in reports.
                row.update(verdict="failed", error="evaluation_error")
            row["seconds"] = round(monotonic() - started, 3)
            turns.append(row)
            blocked = row["verdict"] != "passed"
            if progress:
                progress({"case": case.id, **row})
        rows.append(
            {
                "id": case.id,
                "source": case.source,
                "turns": turns,
                "passed": all(t["verdict"] == "passed" for t in turns),
            }
        )
    turns = [turn for case in rows for turn in case["turns"]]
    timings = sorted(t["seconds"] for t in turns if "seconds" in t)
    passed_cases = sum(case["passed"] for case in rows)
    guidance = sum(
        t.get("actual_status") in {"needs_input", "capability_gap", "data_gap"} for t in turns
    )
    completed = [t for t in turns if "actual_status" in t]
    summary = {
        "total_cases": len(rows),
        "passed_cases": passed_cases,
        "case_pass_rate": passed_cases / len(rows),
        "total_turns": len(turns),
        **{
            v + "_turns": sum(t["verdict"] == v for t in turns)
            for v in ("passed", "failed", "skipped")
        },
        "provider_errors": dict(Counter(t["error"] for t in turns if "error" in t)),
        "mean_guidance_turns": guidance / len(rows),
        "repeated_clarifications": sum(t.get("repeated_clarification", False) for t in turns),
        "unnecessary_guidance": sum(
            t["expected_status"] == "result" and t["actual_status"] != "result" for t in completed
        ),
        "intent_mismatch_turns": sum(
            any(f.startswith("intent.") for f in t.get("failures", [])) for t in turns
        ),
        "latency_p50_seconds": timings[ceil(len(timings) * 0.5) - 1] if timings else None,
        "latency_p95_seconds": timings[ceil(len(timings) * 0.95) - 1] if timings else None,
    }
    return {
        "mode": "cloud" if provider_factory else "replay",
        "measures_model_quality": provider_factory is not None,
        "summary": summary,
        "cases": rows,
    }
