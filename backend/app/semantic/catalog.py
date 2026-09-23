"""Local versioned vocabulary, never populated from business records."""

import json
from pathlib import Path

from app.capabilities.registry import (
    DEFAULT_ITEMS,
    DOMAINS,
    METRICS,
    OPERATIONS,
    TARGETS,
    registry_version,
)


def load_catalog() -> dict:
    path = Path(__file__).resolve().parents[2] / "config" / "semantic-catalog.json"
    catalog = json.loads(path.read_text(encoding="utf-8"))
    # Vocabulary may describe unavailable requests. Execution lists are generated only here.
    catalog["version"] += ":" + registry_version()
    catalog["defaults"]["ranking_limit"] = DEFAULT_ITEMS
    for domain, spec in DOMAINS.items():
        catalog["domains"][domain].update(
            executable_metrics=sorted(METRICS[domain]),
            executable_targets=sorted(TARGETS[domain]),
            executable_operations=sorted(OPERATIONS[domain]),
            default_metric=spec.default_metric,
        )
    return catalog
