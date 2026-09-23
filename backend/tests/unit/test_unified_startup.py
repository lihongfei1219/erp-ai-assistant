"""Unified launcher lifecycle, offline checks, and isolation from unrelated services."""

import importlib.util
import json
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest


@pytest.fixture
def launcher(tmp_path, monkeypatch, multi_report):
    path = Path(__file__).resolve().parents[3] / "start.py"
    spec = importlib.util.spec_from_file_location("unified_startup_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    (tmp_path / "frontend/dist").mkdir(parents=True)
    (tmp_path / "frontend/dist/index.html").write_text("synthetic", encoding="utf-8")
    report = tmp_path / "a snapshot.json"
    report.write_text(multi_report.model_dump_json(), encoding="utf-8")
    config = tmp_path / "app config.json"
    config.write_text(
        json.dumps(
            {
                "app_id": "cli_synthetic",
                "app_secret": "secret-never-output",
                "tenant_key": "tenant",
                "allowed_chat_ids": ["oc_test"],
                "user_access_mode": "all_group_members",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    monkeypatch.setattr("app.integrations.feishu_app.DEFAULT_STATE", tmp_path / "bot-state")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    args = SimpleNamespace(report_path=report, config=config, port=port, _service=None)
    return module, args


def test_check_is_offline_and_never_starts_processes(launcher, monkeypatch, capsys):
    module, args = launcher
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: pytest.fail("child started"))
    monkeypatch.setattr(
        "app.integrations.feishu_app.make_client", lambda *a: pytest.fail("network requested")
    )
    assert (
        module.main(
            [
                "--check",
                "--report-path",
                str(args.report_path),
                "--config",
                str(args.config),
                "--port",
                str(args.port),
            ]
        )
        == 0
    )
    assert "secret-never-output" not in capsys.readouterr().out


def test_busy_port_does_not_start_or_stop_existing_service(launcher):
    module, args = launcher
    with socket.socket() as existing:
        existing.bind(("127.0.0.1", args.port))
        existing.listen()
        with pytest.raises(ValueError, match="端口"):
            module.preflight(args)
        assert existing.getsockname()[1] == args.port


def test_existing_bot_lock_is_preserved(launcher):
    from app.notifications.feishu import DeliveryError, delivery_lock

    module, args = launcher
    with delivery_lock(module.ROOT / "bot-state/connection.lock"):
        with pytest.raises(DeliveryError):
            module.preflight(args)


def test_invalid_credentials_and_report_fail_without_disclosing_contents(launcher, capsys):
    module, args = launcher
    values = [
        "--check",
        "--report-path",
        str(args.report_path),
        "--config",
        str(args.config),
        "--port",
        str(args.port),
    ]
    valid_config = args.config.read_text(encoding="utf-8")
    args.config.write_text('{"app_secret":"secret-never-output"}', encoding="utf-8")
    assert module.main(values) == 1
    assert "secret-never-output" not in capsys.readouterr().out
    args.config.write_text(valid_config, encoding="utf-8")
    args.report_path.write_text("sensitive source rows", encoding="utf-8")
    assert module.main(values) == 1
    assert "sensitive source rows" not in capsys.readouterr().out


def test_both_children_share_exact_snapshot_and_current_interpreter(launcher):
    module, args = launcher
    for service in ("web", "feishu"):
        command = module.child_command(args, service)
        assert command[0] == sys.executable
        assert command[command.index("--report-path") + 1] == str(args.report_path)
        assert command[command.index("--config") + 1] == str(args.config)
        assert command[command.index("--_service") + 1] == service


@pytest.mark.parametrize("mode", ["failure", "interrupt", "spawn_failure"])
def test_supervisor_reaps_only_its_real_child_processes(launcher, monkeypatch, mode):
    module, args = launcher
    real_popen = subprocess.Popen
    children = []
    unrelated = real_popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    def spawn(command, **options):
        if mode == "spawn_failure" and children:
            raise OSError("cannot spawn second service")
        code = (
            "raise SystemExit(3)"
            if mode == "failure" and not children
            else ("import time; time.sleep(60)")
        )
        child = real_popen(
            [sys.executable, "-c", code],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **options,
        )
        children.append(child)
        return child

    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    if mode == "interrupt":

        def interrupt(_):
            raise KeyboardInterrupt

        monkeypatch.setattr(module.time, "sleep", interrupt)
    try:
        assert module.supervise(args) == {"failure": 3, "interrupt": 0, "spawn_failure": 1}[mode]
        assert children and all(child.poll() is not None for child in children)
        assert unrelated.poll() is None
    finally:
        for child in [unrelated, *children]:
            if child.poll() is None:
                for descendant in psutil.Process(child.pid).children(recursive=True):
                    try:
                        descendant.kill()
                    except psutil.NoSuchProcess:
                        pass
                child.kill()
            child.wait(timeout=5)


def test_stop_reaps_actual_workers_beneath_python_launchers(launcher, monkeypatch):
    module, _ = launcher
    monkeypatch.setattr(module, "GRACE_SECONDS", 0.3)
    ready = module.ROOT / "worker-ready"
    worker = f"from pathlib import Path; import time; Path({str(ready)!r}).touch(); time.sleep(60)"
    parent_code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {worker!r}]); time.sleep(60)"
    )
    options = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if module.os.name == "nt"
        else {"start_new_session": True}
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_code],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **options,
    )
    descendants = []
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            threading.Event().wait(0.02)
        descendants = psutil.Process(parent.pid).children(recursive=True)
        assert ready.exists() and descendants
        module.stop_children([("synthetic", parent)])
        assert parent.poll() is not None
        assert not psutil.wait_procs(descendants, timeout=1)[1]
    finally:
        for process in descendants:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=5)


@pytest.mark.parametrize("service", ["web", "feishu"])
def test_child_dispatch_retains_existing_entries_and_windows_signal_bridge(
    launcher,
    monkeypatch,
    service,
):
    module, args = launcher
    args._service = service
    captured = []
    handlers = {}
    raised = []
    monkeypatch.setattr(sys, "argv", [])
    monkeypatch.setattr(
        module.runpy, "run_path", lambda path, **kw: captured.append((path, sys.argv[:], kw))
    )
    monkeypatch.setattr(module.signal, "signal", lambda key, fn: handlers.update({key: fn}))
    monkeypatch.setattr(module.signal, "raise_signal", raised.append)
    module.run_child(args)
    assert captured[0][0].endswith(
        "start_backend.py" if service == "web" else "start_feishu_bot.py"
    )
    assert str(args.report_path) in captured[0][1]
    if service == "feishu":
        assert "--sales" in captured[0][1] and str(args.config) in captured[0][1]
    if module.os.name == "nt":
        handlers[signal.SIGBREAK](signal.SIGBREAK, None)
        assert raised == [signal.SIGINT]
