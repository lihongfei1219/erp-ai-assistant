"""The local launcher serves loopback without recreating an access credential."""

import importlib.util
import sys
from pathlib import Path


def test_launcher_does_not_create_access_token(tmp_path, monkeypatch):
    import uvicorn

    root = Path(__file__).resolve().parents[3]
    launcher = tmp_path / "scripts" / "start_backend.py"
    launcher.parent.mkdir()
    launcher.write_bytes((root / "scripts" / "start_backend.py").read_bytes())
    report = tmp_path / "synthetic.json"
    report.write_text("{}", encoding="utf-8")
    spec = importlib.util.spec_from_file_location("local_launcher_test", launcher)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    called = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: called.append(kw))
    monkeypatch.setattr(
        sys, "argv", [str(launcher), "--report-path", str(report), "--port", "8001"]
    )
    monkeypatch.setattr(sys, "path", sys.path.copy())
    monkeypatch.delenv("ERP_API_TOKEN", raising=False)
    monkeypatch.setenv("ERP_REPORT_PATH", "")
    module.main()
    assert not (tmp_path / ".local" / "api-token.txt").exists()
    assert called == [dict(host="127.0.0.1", port=8001)]
