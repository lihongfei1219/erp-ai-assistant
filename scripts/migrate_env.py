"""Consolidate local deployment configuration; never print configuration values."""

import argparse
import json
import os
import secrets
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai.model_client import load_model_settings  # noqa: E402
from app.core.environment import read_env_file  # noqa: E402
from app.core.settings import LOCAL_SOURCE_ODBC  # noqa: E402
from app.integrations.feishu_app import AppConfig  # noqa: E402
from app.integrations.feishu_jobs import DEFAULT_ASSISTANT_ODBC  # noqa: E402


def migrate(root: Path, *, apply: bool = False):
    root = root.resolve()
    target = root / ".env"
    values = read_env_file(target)
    originals = []
    config_path = root / ".local/feishu-app.json"
    if config_path.is_file():
        data = json.loads(config_path.read_text(encoding="utf-8-sig"))
        AppConfig.model_validate(data).require_authorized_groups()
        for key, value in data.items():
            values.setdefault(
                "FEISHU_" + key.upper(), ",".join(value) if isinstance(value, list) else str(value)
            )
        originals.append(config_path)
    for filename, key in (
        ("feishu-webhook.txt", "FEISHU_WEBHOOK_URL"),
        ("feishu-secret.txt", "FEISHU_WEBHOOK_SECRET"),
    ):
        path = root / ".local" / filename
        if path.is_file():
            values.setdefault(key, path.read_text(encoding="utf-8-sig").strip())
            originals.append(path)
    key_path = root / ".local/semantic-context.key"
    if key_path.is_file():
        raw = key_path.read_bytes()
        if len(raw) != 32:
            raise ValueError("Invalid legacy signing key")
        values.setdefault("ERP_CONTEXT_SIGNING_KEY", raw.hex())
        originals.append(key_path)
    values.setdefault("ERP_CONTEXT_SIGNING_KEY", secrets.token_hex(32))
    for key, alias in (
        ("ERP_AI_BASE_URL", "MAIN_VITE_PI_NORMALIZER_BASE_URL"),
        ("ERP_AI_MODEL", "MAIN_VITE_PI_NORMALIZER_MODEL"),
        ("ERP_AI_API_KEY", "DASHSCOPE_API_KEY"),
    ):
        if alias in values:
            values.setdefault(key, values[alias])
            del values[alias]
    defaults = {
        "ERP_HOST": "127.0.0.1",
        "ERP_PORT": "8001",
        "ERP_ANALYSIS_ENGINE": "langgraph",
        "ERP_SOURCE_ODBC": LOCAL_SOURCE_ODBC,
        "ERP_ASSISTANT_ODBC": DEFAULT_ASSISTANT_ODBC,
        "ERP_ASSISTANT_ADMIN_ODBC": DEFAULT_ASSISTANT_ODBC.replace(
            "DATABASE=ERP_AI_Assistant;", "DATABASE=master;"
        ),
        "ERP_GRAPH_STATE_DIR": ".local/langgraph",
        "ERP_FEISHU_STATE_DIR": ".local/feishu-app",
        "ERP_FEISHU_DELIVERY_DIR": ".local/feishu-delivery",
        "ERP_SERVICES_LOCK": ".local/services.lock",
        "FEISHU_USER_ACCESS_MODE": "allowlist",
        "FEISHU_ALLOWED_USER_OPEN_IDS": "",
    }
    for key, value in defaults.items():
        values.setdefault(key, value)
    if not values.get("ERP_REPORT_PATH"):
        for name in (
            "platform-four-domain-20260922.json",
            "platform-operating-20260916.json",
            "platform-sales-20260916.json",
        ):
            relative = Path(".local/reports") / name
            if (root / relative).is_file():
                values["ERP_REPORT_PATH"] = relative.as_posix()
                break
    for key in list(values):
        if key in os.environ:
            values[key] = os.environ[key]
    if len(bytes.fromhex(values["ERP_CONTEXT_SIGNING_KEY"])) != 32:
        raise ValueError("Invalid signing key")
    app_values = {}
    for key in AppConfig.model_fields:
        name = "FEISHU_" + key.upper()
        if name in values:
            value = values[name]
            app_values[key] = (
                [v.strip() for v in value.split(",") if v.strip()]
                if key in {"allowed_chat_ids", "allowed_user_open_ids"}
                else value
            )
    AppConfig.model_validate(app_values).require_authorized_groups()
    if not apply:
        return len(values), len(originals)
    # Validate the literal roundtrip before atomically replacing .env or removing old files.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=root, prefix=".env.", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write("# Private deployment configuration; never commit this file.\n")
            for key, value in values.items():
                escaped = value.replace("\\", "\\\\").replace("'", "\\'")
                stream.write(f"{key}='{escaped}'\n")
        temporary.chmod(0o600)
        if read_env_file(temporary) != values:
            raise ValueError("Configuration roundtrip failed")
        load_model_settings(temporary)
        os.replace(temporary, target)
        for path in originals:
            # Fixed filenames only, with explicit containment and no symlink traversal.
            if path.is_symlink() or not path.resolve().is_relative_to(root / ".local"):
                raise ValueError("Legacy configuration path outside workspace")
            path.unlink()
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return len(values), len(originals)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write .env and remove migrated files")
    args = parser.parse_args()
    try:
        keys, files = migrate(ROOT, apply=args.apply)
        print(
            f"Configuration validated: {keys} keys, {files} legacy files. "
            + ("Migration complete." if args.apply else "Preview only; use --apply to migrate.")
        )
        return 0
    except Exception:
        print("Migration failed. Check configuration format and permissions; values are hidden.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
