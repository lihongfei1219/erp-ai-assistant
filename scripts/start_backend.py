"""Start the loopback API with the saved report; no access token is required."""

import argparse
import os
import sys
from pathlib import Path


def main() -> None:
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root / "backend"))
    from app.core.environment import environment, project_path

    values = environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-path", type=Path, help="Analysis snapshot JSON path")
    parser.add_argument("--port", type=int, default=values.get("ERP_PORT", "8000"))
    parser.add_argument("--host", default=values.get("ERP_HOST", "127.0.0.1"))
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")

    report_path = args.report_path
    if report_path is None and values.get("ERP_REPORT_PATH"):
        report_path = project_path(values["ERP_REPORT_PATH"])
    if report_path is None:
        report_path = project_root / ".local/reports/platform-four-domain-20260922.json"
        if not report_path.is_file():
            report_path = project_root / ".local/reports/platform-operating-20260916.json"
        if not report_path.is_file():
            report_path = project_root / ".local/reports/platform-sales-20260916.json"
    report_path = report_path.resolve()
    if not report_path.is_file():
        parser.error(f"Report file not found: {report_path}")

    try:
        import uvicorn
    except ImportError:
        parser.error(
            "Run uv sync --locked, then uv run python scripts/start_backend.py"
        )

    os.environ["ERP_REPORT_PATH"] = str(report_path)
    sys.path.insert(0, str(project_root / "backend"))
    print(f"API documentation: http://{args.host}:{args.port}/docs", flush=True)
    print(f"Workspace listener: http://{args.host}:{args.port}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    uvicorn.run("app.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
