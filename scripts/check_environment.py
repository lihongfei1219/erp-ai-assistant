"""Check local runtime dependencies without connecting to ERP, a model or Feishu."""

import importlib
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


def main():
    sys.path.insert(0, str(ROOT / "backend"))
    failed = False
    for name in (
        "fastapi",
        "uvicorn",
        "pandas",
        "plotly",
        "pyodbc",
        "sqlalchemy",
        "psutil",
        "pydantic",
        "pydantic_ai",
        "httpx",
        "httpx2",
        "openai",
        "lark_oapi",
        "langsmith",
        "langgraph.graph",
        "langgraph.checkpoint.sqlite.aio",
        "app.orchestration.runtime",
    ):
        try:
            importlib.import_module(name)
            print(f"[OK] {name}")
        except Exception as exc:
            # Exception payloads may contain local configuration: print the type only.
            print(f"[FAIL] {name}: {type(exc).__name__}")
            failed = True
    try:
        ZoneInfo("Asia/Shanghai")
        print("[OK] Asia/Shanghai timezone data")
    except Exception as exc:
        print(f"[FAIL] timezone data: {type(exc).__name__}")
        failed = True
    if not failed:
        import pyodbc

        if "ODBC Driver 18 for SQL Server" in pyodbc.drivers():
            print("[OK] SQL Server ODBC Driver 18")
        else:
            print(
                "[NOTICE] ODBC Driver 18 missing; needed for SQL extraction, not saved snapshots."
            )
    if (ROOT / "frontend/dist/index.html").is_file():
        print("[OK] Built frontend")
    else:
        print("[FAIL] Built frontend missing; run npm run build in frontend.")
        failed = True
    reports = [
        ROOT / ".local/reports" / name
        for name in (
            "platform-four-domain-20260922.json",
            "platform-operating-20260916.json",
            "platform-sales-20260916.json",
        )
    ]
    if not any(path.is_file() for path in reports):
        print("[NOTICE] Default data snapshot missing. Restore it or start with --report-path.")
    elif not reports[0].is_file():
        print("[NOTICE] Default four-domain snapshot missing; startup will use a sales snapshot.")
    else:
        print("[OK] Default four-domain snapshot file present (contents not validated)")
    from app.integrations.feishu_app import AppConfigError, load_config

    try:
        load_config().require_authorized_groups()
    except AppConfigError:
        print(
            "[NOTICE] Feishu configuration is not ready; web startup does not start the bot. "
            "Run uv run python scripts/start_feishu_bot.py --check for details."
        )
    else:
        print("[OK] Feishu local configuration valid (connection and bot process not checked)")
    print("[INFO] No ERP connection, model request or message send was performed.")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
