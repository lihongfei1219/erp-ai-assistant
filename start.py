"""Start the web workspace and Feishu bot together; Ctrl+C stops both children."""

import argparse
import os
import runpy
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GRACE_SECONDS = 30


def arguments(argv=None):
    parser = argparse.ArgumentParser(description="统一启动网页和飞书问数，Ctrl+C 一起停止")
    parser.add_argument("--port", type=int, default=8001, help="网页端口，默认8001")
    parser.add_argument("--report-path", type=Path, help="两个服务共用的快照文件")
    parser.add_argument(
        "--config", type=Path, default=ROOT / ".local/feishu-app.json", help="飞书本地配置文件"
    )
    parser.add_argument("--check", action="store_true", help="仅离线检查，不启动或连接服务")
    parser.add_argument("--_service", choices=("web", "feishu"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("--port 必须在1024到65535之间")
    if args.report_path is None:
        args.report_path = next(
            (
                ROOT / ".local/reports" / name
                for name in (
                    "platform-four-domain-20260922.json",
                    "platform-operating-20260916.json",
                    "platform-sales-20260916.json",
                )
                if (ROOT / ".local/reports" / name).is_file()
            ),
            None,
        )
    if args.report_path is None or not args.report_path.is_file():
        parser.error("未找到分析快照，请通过 --report-path 指定已有快照")
    args.report_path = args.report_path.resolve()
    args.config = args.config.resolve()
    return args


def preflight(args):
    """Local checks only; no ERP/assistant SQL, model call or Feishu request."""
    from app.core.reports import load_report
    from app.integrations.feishu_app import DEFAULT_STATE, load_config
    from app.notifications.feishu import delivery_lock

    load_config(args.config).require_authorized_groups()
    try:
        report = load_report(args.report_path)
    except (OSError, ValueError):
        raise ValueError("快照读取或校验失败，请指定有效的已对账快照。") from None
    if report.operating is None or not report.metadata.scope.all_buyers:
        raise ValueError("飞书问数需要包含有效销售规则的全平台快照。")
    if not (ROOT / "frontend/dist/index.html").is_file():
        raise ValueError("缺少网页构建文件，请先在frontend目录运行 npm run build。")
    with socket.socket() as probe:
        if os.name == "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(("127.0.0.1", args.port))
        except OSError:
            raise ValueError(
                f"端口 {args.port} 已被占用或不可用，请先停止原服务或更换端口。"
            ) from None
    # Probe the existing bot's lock without deleting or altering its task state.
    with delivery_lock(DEFAULT_STATE / "connection.lock"):
        pass


def child_command(args, service):
    return [
        sys.executable,
        "-u",
        str(ROOT / "start.py"),
        "--_service",
        service,
        "--report-path",
        str(args.report_path),
        "--config",
        str(args.config),
        "--port",
        str(args.port),
    ]


def run_child(args):
    # Windows children use separate process groups. Route targeted Ctrl+Break through
    # SIGINT so uvicorn and the Feishu worker keep their existing graceful shutdown.
    if os.name == "nt":
        signal.signal(signal.SIGBREAK, lambda *_: signal.raise_signal(signal.SIGINT))
    if args._service == "web":
        script = ROOT / "scripts/start_backend.py"
        values = ["--report-path", str(args.report_path), "--port", str(args.port)]
    else:
        script = ROOT / "scripts/start_feishu_bot.py"
        values = ["--sales", "--report-path", str(args.report_path), "--config", str(args.config)]
    sys.argv = [str(script), *values]
    runpy.run_path(str(script), run_name="__main__")


def stop_children(children):
    """Only handles created by this invocation; never discover/kill unrelated PIDs."""
    import psutil

    active = [(name, child) for name, child in children if child.poll() is None]
    descendants = []
    # Windows venv launchers can exit before their actual Python worker does.
    # Capture descendants while the parent still exists; Process tracks PID reuse.
    for _, child in active:
        try:
            descendants.extend(psutil.Process(child.pid).children(recursive=True))
        except psutil.NoSuchProcess:
            pass
    for _, child in active:
        try:
            child.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
        except OSError:
            # Console delivery is unavailable in some redirected terminal hosts.
            if child.poll() is None:
                child.terminate()
    deadline = time.monotonic() + GRACE_SECONDS
    for name, child in active:
        try:
            child.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            print(f"{name}超过退出等待时间，正在结束本次启动的进程。", flush=True)
            child.kill()
            child.wait()
    _, remaining = psutil.wait_procs(descendants, timeout=max(0, deadline - time.monotonic()))
    for process in remaining:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(remaining, timeout=5)


def supervise(args):
    children = []
    result = 0
    environment = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    options = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {
            "start_new_session": True,
        }
    )
    try:
        for name, service in (("网页", "web"), ("飞书", "feishu")):
            child = subprocess.Popen(
                child_command(args, service), cwd=ROOT, env=environment, **options
            )
            children.append((name, child))
            print(f"已启动{name}进程，PID={child.pid}。", flush=True)
        print(f"网页地址：http://127.0.0.1:{args.port}；服务就绪以各自日志为准。", flush=True)
        print("按 Ctrl+C 一起停止网页和飞书。", flush=True)
        while True:
            for name, child in children:
                code = child.poll()
                if code is not None:
                    print(f"{name}进程退出（代码 {code}），正在关闭其余服务。", flush=True)
                    return code if code > 0 else 1
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("正在停止两个服务，请等待退出完成……", flush=True)
    except OSError:
        print("无法创建服务进程，请检查Python环境和文件权限。", flush=True)
        result = 1
    finally:
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            stop_children(children)
        finally:
            signal.signal(signal.SIGINT, previous)
    return result


def main(argv=None):
    args = arguments(argv)
    sys.path.insert(0, str(ROOT / "backend"))
    if args._service:
        try:
            run_child(args)
        except KeyboardInterrupt:
            return 0
        return 0
    try:
        from app.integrations.feishu_app import AppConfigError
        from app.notifications.feishu import DeliveryError, delivery_lock
    except ImportError:
        print("缺少依赖，请先执行 uv sync --locked。", flush=True)
        return 1
    try:
        with delivery_lock(ROOT / ".local/services.lock"):
            preflight(args)
            if args.check:
                print("离线检查通过；未启动服务，未连接数据库、模型或飞书。", flush=True)
                return 0
            return supervise(args)
    except (AppConfigError, DeliveryError, ValueError) as exc:
        print(f"启动检查未通过：{exc}", flush=True)
        return 1
    except OSError:
        print("无法读取启动所需文件或获取进程锁，请检查权限。", flush=True)
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
