"""Preview, explicitly send, or schedule yesterday's sales digest."""

import argparse
import hashlib
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.core.environment import configured_path
from app.core.reports import load_report
from app.notifications.daily import DigestUnavailable, build_digest, message_card
from app.notifications.feishu import (
    LOCAL_DIR,
    DeliveryError,
    FeishuSettings,
    delivery_lock,
    send_once,
    write_state,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


def scheduled_tick(
    report_path: Path,
    settings: FeishuSettings,
    now: datetime,
    *,
    sender=None,
) -> str:
    """One bounded attempt: 08:00 local, max 3 definite failures, 5 minutes apart."""
    if now.tzinfo is None:
        raise ValueError("调度时刻必须包含时区")
    local = now.astimezone(SHANGHAI)
    if local.hour < 8:
        return "waiting"
    key = hashlib.sha256(f"{report_path.resolve()}|{settings.webhook}".encode()).hexdigest()
    path = settings.state_dir / f"schedule-{key}.json"
    with delivery_lock(settings.state_dir / f"schedule-{key}.lock"):
        state = json.loads(path.read_bytes()) if path.exists() else {}
        if state.get("run_day") != local.date().isoformat():
            state = {"run_day": local.date().isoformat(), "attempts": 0}
        if state.get("status") in {"sent", "already_sent"}:
            return "already_sent"
        if state.get("status") == "unknown":
            return "unknown"
        if state["attempts"] >= 3:
            return "exhausted"
        if state.get("next_attempt") and now < datetime.fromisoformat(state["next_attempt"]):
            return "waiting"
        state.update(
            attempts=state["attempts"] + 1,
            status="running",
            next_attempt=(now + timedelta(minutes=5)).isoformat(),
        )
        write_state(path, state)
        try:
            # Load again on each attempt; API restarts are not required for scheduled delivery.
            report = load_report(report_path)
            digest = build_digest(report, local.date() - timedelta(days=1))
            result = send_once(settings, digest, sender=sender)
            state.update(status=result.status, message=result.message)
        except DeliveryError as exc:
            state.update(status="unknown" if exc.uncertain else "failed", message=str(exc))
        except DigestUnavailable as exc:
            state.update(status="failed", message=str(exc))
        except (OSError, ValueError):
            state.update(status="failed", message="快照或状态不可用，请检查本地配置")
        write_state(path, state)
        return state["status"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="飞书群销售日报：默认仅预览，不发送")
    parser.add_argument(
        "--report-path", type=Path, default=configured_path(
            "ERP_REPORT_PATH", LOCAL_DIR / "reports/platform-operating-20260916.json"
        )
    )
    parser.add_argument("--date", type=date.fromisoformat, help="单次报告日期，默认真实昨天")
    parser.add_argument("--demo", action="store_true", help="演示标签，允许备份当日部分数据")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--send", action="store_true", help="立即发送一次；成功发送不重复")
    mode.add_argument("--watch", action="store_true", help="常驻调度：每天北京时间 08:00")
    parser.add_argument("--output", type=Path, help="预览 JSON 保存位置，必须为新文件")
    args = parser.parse_args(argv)
    if args.watch and (args.date or args.demo or args.output):
        parser.error("定时模式使用真实昨天，不能固定日期、启用演示或指定预览输出")
    if args.send and args.output:
        parser.error("发送与保存预览请分别执行")
    if args.demo and not args.date:
        parser.error("演示模式必须显式指定 --date")
    try:
        if args.watch:
            FeishuSettings.from_env().validate()
            print("Feishu schedule: 08:00 Asia/Shanghai; keep this worker running.", flush=True)
            last_status = None
            while True:
                status = scheduled_tick(
                    args.report_path, FeishuSettings.from_env(), datetime.now(SHANGHAI)
                )
                if status != last_status:
                    print(f"{datetime.now(SHANGHAI).isoformat()} status={status}", flush=True)
                    last_status = status
                time.sleep(30)
        day = args.date or (datetime.now(SHANGHAI).date() - timedelta(days=1))
        digest = build_digest(load_report(args.report_path), day, demo=args.demo)
        if args.send:
            result = send_once(FeishuSettings.from_env(), digest)
            print(f"day={day} status={result.status}")
        else:
            output = args.output or (LOCAL_DIR / f"feishu-preview-{day}-{time.time_ns()}.json")
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8") as handle:
                json.dump(
                    {"digest": digest.model_dump(mode="json"), "card": message_card(digest)},
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
            print(f"Preview saved: {output.resolve()}")
        return 0
    except KeyboardInterrupt:
        return 0
    except (DeliveryError, DigestUnavailable) as exc:
        print(str(exc))
        return 1
    except (OSError, ValueError):
        print("日报未完成，请检查快照、配置或预览文件是否已存在；没有输出原始错误或凭据。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
