"""Evaluation must detect semantic regressions, not merely valid model output."""

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from app.semantic.provider import SemanticProviderUnavailable
from evaluation.dialogue import Case, Corpus, evaluate

CORPUS_PATH = Path(__file__).resolve().parents[2] / "evaluation/cases.json"
SEED_CORPUS = Corpus.model_validate_json(CORPUS_PATH.read_bytes())


@pytest.fixture
def anyio_backend():
    return "asyncio"


def example():
    return {
        "id": "returns_date",
        "source": "synthetic",
        "tags": ["returns", "followup"],
        "turns": [
            {
                "question": "帮我看看退货金额",
                "replay": {
                    "intents": [{"domain": "returns", "operation": "summary", "metric": "amount"}]
                },
                "expected": {
                    "status": "needs_input",
                    "clarification_field": "time",
                    "intents": [
                        {
                            "domain": "returns",
                            "operation": "summary",
                            "metric": "amount",
                            "time": None,
                        }
                    ],
                },
            },
            {
                "question": "九月二号",
                "replay": {"mode": "answer", "intents": [{"time": "九月二号"}]},
                "expected": {
                    "status": "result",
                    "intents": [
                        {
                            "domain": "returns",
                            "operation": "summary",
                            "metric": "amount",
                            "time": "2026-09-02至2026-09-02",
                        }
                    ],
                    "plan": [
                        {
                            "domain": "returns",
                            "kind": "summary",
                            "metric": "amount",
                            "start_date": "2026-09-02",
                            "end_date_exclusive": "2026-09-03",
                        }
                    ],
                },
            },
        ],
    }


def corpus(raw=None):
    return Corpus(
        version="1", today=date(2026, 9, 22), cases=[Case.model_validate(raw or example())]
    )


@pytest.mark.anyio
async def test_replay_runs_real_dialogue_and_labels_it_as_not_model_quality(multi_report):
    result = await evaluate(corpus(), multi_report)
    assert result["mode"] == "replay"
    assert result["measures_model_quality"] is False
    assert result["summary"]["passed_cases"] == 1
    assert result["summary"]["passed_turns"] == 2
    assert result["summary"]["mean_guidance_turns"] == 1
    assert "帮我看看" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.anyio
async def test_wrong_metric_fails_even_when_model_returns_valid_schema(multi_report):
    raw = example()
    raw["turns"][0]["replay"]["intents"][0]["metric"] = "orders"
    result = await evaluate(corpus(raw), multi_report)
    assert result["summary"]["failed_turns"] == 1
    assert result["summary"]["skipped_turns"] == 1
    assert result["summary"]["passed_cases"] == 0
    assert "intent.metric" in result["cases"][0]["turns"][0]["failures"]


@pytest.mark.anyio
async def test_changed_dates_in_execution_plan_fail(multi_report):
    raw = example()
    raw["turns"][1]["expected"]["plan"][0].update(
        start_date="2026-09-01", end_date_exclusive="2026-09-02"
    )
    result = await evaluate(corpus(raw), multi_report)
    assert result["summary"]["failed_turns"] == 1
    assert "plan" in result["cases"][0]["turns"][1]["failures"]


@pytest.mark.anyio
async def test_provider_timeout_is_reported_and_dependent_turn_skipped(multi_report):
    class Broken:
        async def interpret(self, request):
            raise SemanticProviderUnavailable(
                "private-provider-body-and-credential", reason="timeout"
            )

    result = await evaluate(corpus(), multi_report, provider_factory=Broken)
    assert result["mode"] == "cloud"
    assert result["summary"]["provider_errors"] == {"timeout": 1}
    assert result["summary"]["skipped_turns"] == 1
    assert result["summary"]["case_pass_rate"] == 0
    assert "private-provider" not in json.dumps(result)


def test_corpus_rejects_duplicate_ids_and_empty_or_invalid_expectations():
    with pytest.raises(ValueError):
        Corpus(version="1", today=date(2026, 9, 22), cases=[example(), example()])
    for mutation in (
        {"intents": []},
        {"status": "result"},
        {"intents": [{"domain": "returns", "operaton": "summary"}]},
    ):
        raw = example()
        raw["turns"][0]["expected"].update(mutation)
        with pytest.raises(ValueError):
            corpus(raw)


@pytest.mark.anyio
async def test_unlabelled_filters_cannot_slip_through(multi_report):
    raw = example()
    raw["turns"][0]["replay"]["intents"][0]["filters"] = [
        {"field": "product", "value": "SYNTHETIC-PRODUCT"}
    ]
    result = await evaluate(corpus(raw), multi_report)
    assert result["summary"]["passed_cases"] == 0
    assert "intent.filters" in result["cases"][0]["turns"][0]["failures"]


@pytest.mark.anyio
@pytest.mark.parametrize("case", SEED_CORPUS.cases, ids=lambda c: c.id)
async def test_labelled_corpus_replay(case, monkeypatch):
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "scripts"))
    from probe_operations_model import synthetic_report

    result = await evaluate(SEED_CORPUS.model_copy(update={"cases": [case]}), synthetic_report())
    assert result["summary"]["passed_cases"] == 1, result["cases"]


@pytest.mark.anyio
async def test_inventory_stock_and_quantity_are_semantically_equivalent(monkeypatch):
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "scripts"))
    from probe_operations_model import synthetic_report

    raw = next(c for c in SEED_CORPUS.cases if c.id == "historical_stock").model_dump()
    raw["turns"][0]["replay"]["intents"][0]["metric"] = "quantity"
    result = await evaluate(corpus(raw), synthetic_report())
    assert result["summary"]["passed_cases"] == 1, result["cases"]


@pytest.mark.anyio
async def test_evaluation_rejects_real_report_before_calling_provider(multi_report):
    report = multi_report.model_copy(
        update={"metadata": multi_report.metadata.model_copy(update={"source_kind": "erp"})}
    )
    with pytest.raises(ValueError, match="synthetic"):
        await evaluate(corpus(), report, provider_factory=lambda: pytest.fail("provider called"))


@pytest.mark.anyio
async def test_single_best_product_cannot_expand_to_fifty(multi_report):
    raw = next(c for c in SEED_CORPUS.cases if c.id == "screenshot_best_product").model_dump()
    raw["turns"][0]["replay"]["intents"][0]["limit"] = 50
    result = await evaluate(corpus(raw), multi_report)
    assert result["summary"]["passed_cases"] == 0
    assert "intent.limit" in result["cases"][0]["turns"][0]["failures"]


@pytest.mark.anyio
async def test_unspecified_metric_is_not_forced_for_existence(monkeypatch):
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "scripts"))
    from probe_operations_model import synthetic_report

    raw = next(c for c in SEED_CORPUS.cases if c.id == "screenshot_returns_exist").model_dump()
    raw["turns"][0]["replay"]["intents"][0]["metric"] = None
    result = await evaluate(corpus(raw), synthetic_report())
    assert result["summary"]["passed_cases"] == 1, result["cases"]


def test_cli_defaults_to_offline_and_does_not_overwrite_report(tmp_path):
    root = Path(__file__).resolve().parents[3]
    output = tmp_path / "evaluation.json"
    command = [
        sys.executable,
        str(root / "scripts/evaluate_dialogue.py"),
        "--case",
        "historical_stock",
        "--output",
        str(output),
    ]
    # Deliberately invalid cloud configuration: default mode must never load it.
    env = {**os.environ, "ERP_AI_ENABLED": "not-a-boolean"}
    first = subprocess.run(command, cwd=root, env=env, capture_output=True, timeout=30)
    assert first.returncode == 0, first.stderr.decode(errors="replace")
    saved = output.read_bytes()
    result = json.loads(saved)
    assert result["mode"] == "replay" and result["metadata"]["model"] is None
    assert len(result["metadata"]["corpus_sha256"]) == 64
    assert result["summary"]["total_cases"] == 1
    assert "question" not in result["cases"][0]["turns"][0]
    second = subprocess.run(command, cwd=root, env=env, capture_output=True, timeout=30)
    assert second.returncode == 2
    assert output.read_bytes() == saved


def test_ignoring_metrics_is_not_allowed_for_rankings_or_explicit_metrics():
    for explicit in (True, False):
        raw = example()
        expected = raw["turns"][1]["expected"]
        expected["ignore_plan_metric"] = True
        if not explicit:
            expected["intents"][0].pop("metric")
            expected["plan"][0]["kind"] = "product_ranking"
        with pytest.raises(ValueError, match="metric-unspecified"):
            corpus(raw)


@pytest.mark.anyio
async def test_missing_inventory_default_before_date_is_equivalent(multi_report):
    raw = next(c for c in SEED_CORPUS.cases if c.id == "screenshot_high_stock").model_dump()
    raw["turns"][0]["replay"]["intents"][0]["metric"] = None
    result = await evaluate(corpus(raw), multi_report)
    assert result["summary"]["passed_cases"] == 1, result["cases"]


@pytest.mark.anyio
async def test_implicit_default_cannot_satisfy_explicit_nondefault_metric(multi_report):
    raw = example()
    raw["turns"][0]["expected"]["intents"][0]["metric"] = "quantity"
    raw["turns"][0]["replay"]["intents"][0]["metric"] = None
    result = await evaluate(corpus(raw), multi_report)
    assert result["summary"]["passed_cases"] == 0
    assert "intent.metric" in result["cases"][0]["turns"][0]["failures"]


@pytest.mark.anyio
async def test_implicit_ranking_product_target_is_equivalent_before_filter_guidance(multi_report):
    raw = next(c for c in SEED_CORPUS.cases if c.id == "category_filter").model_dump()
    raw["turns"][0]["replay"]["intents"][0]["target"] = None
    result = await evaluate(corpus(raw), multi_report)
    assert result["summary"]["passed_cases"] == 1, result["cases"]


@pytest.mark.anyio
async def test_default_product_target_cannot_replace_explicit_region(multi_report):
    raw = next(c for c in SEED_CORPUS.cases if c.id == "screenshot_region").model_dump()
    raw["turns"][0]["replay"]["intents"][0]["target"] = None
    result = await evaluate(corpus(raw), multi_report)
    assert result["summary"]["passed_cases"] == 0
    assert "intent.target" in result["cases"][0]["turns"][0]["failures"]


@pytest.mark.anyio
async def test_reviewed_alternative_accepts_useful_target_clarification(multi_report):
    raw = example()
    first = raw["turns"][0]
    first["replay"]["intents"][0]["operation"] = "list"
    first["replay"]["issues"] = [{"field": "target", "question": "要看商品还是客户？"}]
    alternative = {
        "status": "needs_input",
        "clarification_field": "target",
        "intents": [{"domain": "returns", "operation": "list", "time": None}],
    }
    first["alternatives"] = [alternative]
    raw["turns"] = [first]
    result = await evaluate(corpus(raw), multi_report)
    assert result["summary"]["passed_cases"] == 1
    assert result["cases"][0]["turns"][0]["accepted_variant"] == 1
    first["alternatives"][0]["intents"][0]["operation"] = "ranking"
    result = await evaluate(corpus(raw), multi_report)
    assert result["summary"]["passed_cases"] == 0


@pytest.mark.anyio
async def test_explicit_exclusion_cannot_be_dropped_as_redundant(multi_report):
    raw = next(c for c in SEED_CORPUS.cases if c.id == "category_filter").model_dump()
    raw["turns"][0]["replay"]["intents"][0]["filters"].pop()
    result = await evaluate(corpus(raw), multi_report)
    assert result["summary"]["passed_cases"] == 0
    assert "intent.filters" in result["cases"][0]["turns"][0]["failures"]
