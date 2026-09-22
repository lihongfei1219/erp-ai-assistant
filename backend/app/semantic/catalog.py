"""Local versioned vocabulary, never populated from business records."""

import json
from pathlib import Path


def load_catalog() -> dict:
    path = Path(__file__).resolve().parents[2] / "config" / "semantic-catalog.json"
    return json.loads(path.read_text(encoding="utf-8"))
