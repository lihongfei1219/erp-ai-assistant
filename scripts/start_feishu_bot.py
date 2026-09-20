"""Run the Feishu application connector from the project root."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from workers.feishu_bot import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
