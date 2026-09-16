"""Start the local API with the saved report and development token."""

import argparse
import os
import secrets
import sys
from pathlib import Path


def main() -> None:
    project_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-path", type=Path, help="Analysis snapshot JSON path")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")

    report_path = args.report_path
    if report_path is None:
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
            "Use the project .venv Python with backend/requirements.lock installed"
        )

    token_path = project_root / ".local/api-token.txt"
    token_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with token_path.open("x", encoding="utf-8") as token_file:
            token_file.write(secrets.token_urlsafe(32))
    except FileExistsError:
        pass
    token = token_path.read_text(encoding="utf-8-sig").strip()
    if len(token) < 32:
        parser.error(
            f"The development token in {token_path} must have at least 32 characters"
        )

    os.environ["ERP_API_TOKEN"] = token
    os.environ["ERP_REPORT_PATH"] = str(report_path)
    sys.path.insert(0, str(project_root / "backend"))
    print(f"API documentation: http://127.0.0.1:{args.port}/docs", flush=True)
    print(f"Bearer token file (local only): {token_path}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
