import hashlib
import json
from pathlib import Path

from app.schemas.sales import BusinessRules

DEFAULT_RULES_PATH = Path(__file__).resolve().parents[2] / "config" / "business-rules.json"


def load_business_rules(path: Path | None = None) -> BusinessRules:
    return BusinessRules.model_validate_json((path or DEFAULT_RULES_PATH).read_bytes())


def policy_fingerprint(rules: BusinessRules) -> str:
    canonical = json.dumps(
        rules.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
