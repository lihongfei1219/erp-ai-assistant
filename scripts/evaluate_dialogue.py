"""Run labelled synthetic dialogue evaluation; cloud calls require --cloud explicitly."""

# ruff: noqa: E402 -- standalone script bootstraps the backend import path.

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from probe_operations_model import synthetic_report

from app.ai.model_client import load_model_settings
from app.semantic.catalog import load_catalog
from app.semantic.prompt import PROMPT
from app.semantic.provider import CloudSemanticProvider
from evaluation.dialogue import Corpus, evaluate


def digest(value):
    return hashlib.sha256(value).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cloud", action="store_true", help="Evaluate configured cloud model")
    parser.add_argument(
        "--case", action="append", dest="case_ids", help="Select case ID (repeatable)"
    )
    parser.add_argument(
        "--output", type=Path, help="Save a new JSON report; existing files are refused"
    )
    args = parser.parse_args()
    corpus_bytes = (ROOT / "backend/evaluation/cases.json").read_bytes()
    corpus = Corpus.model_validate_json(corpus_bytes)
    if args.case_ids:
        missing = set(args.case_ids) - {c.id for c in corpus.cases}
        if missing:
            parser.error("Unknown case ID")
        corpus = corpus.model_copy(
            update={"cases": [c for c in corpus.cases if c.id in args.case_ids]}
        )
    if args.output and args.output.exists():
        parser.error("Output already exists; choose a new report path")
    settings = None
    if args.cloud:
        settings = load_model_settings()
        if not settings.enabled:
            parser.error("Cloud model is not configured or enabled")
    result = asyncio.run(
        evaluate(
            corpus,
            synthetic_report(),
            provider_factory=(lambda: CloudSemanticProvider(settings)) if settings else None,
            progress=lambda row: print(json.dumps(row, ensure_ascii=True), flush=True),
        )
    )
    result["metadata"] = {
        "run_at": datetime.now(UTC).isoformat(),
        "evaluation_date": corpus.today.isoformat(),
        "corpus_version": corpus.version,
        "corpus_sha256": digest(corpus_bytes),
        "prompt_sha256": digest(PROMPT.encode()),
        "catalog_sha256": digest(json.dumps(load_catalog(), sort_keys=True).encode()),
        "model": settings.model if settings else None,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    print(json.dumps({"mode": result["mode"], **result["summary"]}, ensure_ascii=True))
    return 0 if result["summary"]["passed_cases"] == len(corpus.cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
