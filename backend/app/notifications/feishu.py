"""Feishu group webhook and conservative, local delivery ledger."""

import base64
import hashlib
import hmac
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.core.environment import configured_path, environment
from app.notifications.daily import DailyDigest, message_card
from app.schemas.sales import StrictModel

LOCAL_DIR = Path(__file__).resolve().parents[3] / ".local"


class DeliveryError(ValueError):
    def __init__(self, message: str, *, uncertain: bool = False):
        super().__init__(message)
        self.uncertain = uncertain


@dataclass(frozen=True)
class FeishuSettings:
    webhook: str = field(default="", repr=False)
    secret: str = field(default="", repr=False)
    state_dir: Path = field(default_factory=lambda: LOCAL_DIR / "feishu-delivery")

    @classmethod
    def from_env(cls):
        values = environment()
        return cls(
            webhook=values.get("FEISHU_WEBHOOK_URL", "").strip(),
            secret=values.get("FEISHU_WEBHOOK_SECRET", "").strip(),
            state_dir=configured_path("ERP_FEISHU_DELIVERY_DIR", LOCAL_DIR / "feishu-delivery"),
        )

    def validate(self):
        if not self.webhook:
            raise DeliveryError("尚未配置飞书群机器人 Webhook")
        if not re.fullmatch(
            r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[A-Za-z0-9-]+", self.webhook
        ):
            raise DeliveryError("飞书 Webhook 格式不正确，请使用官方群机器人地址")


class DeliveryResult(StrictModel):
    status: Literal["sent", "already_sent"]
    day: str
    message: str


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send_webhook(
    settings: FeishuSettings, digest: DailyDigest, *, opener=None, timestamp: int | None = None
) -> None:
    settings.validate()
    payload = {"msg_type": "interactive", "card": message_card(digest)}
    if settings.secret:
        stamp = str(int(time.time()) if timestamp is None else timestamp)
        signature = hmac.new(f"{stamp}\n{settings.secret}".encode(), b"", hashlib.sha256)
        payload.update(timestamp=stamp, sign=base64.b64encode(signature.digest()).decode())
    body = json.dumps(payload, ensure_ascii=False).encode()
    if len(body) > 28_000:
        raise DeliveryError("日报卡片过长，请缩短显示名称后重试")
    request = Request(
        settings.webhook,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    transport = opener or build_opener(NoRedirect()).open
    try:
        with transport(request, timeout=15) as response:
            result = json.loads(response.read(65537))
    except HTTPError as exc:
        raise DeliveryError("飞书 HTTP 请求失败", uncertain=exc.code >= 500) from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise DeliveryError("发送结果不确定，请在飞书群核实后处理", uncertain=True) from None
    if not isinstance(result, dict):
        raise DeliveryError("飞书返回无法识别，请在群内核实", uncertain=True)
    code = result.get("code", result.get("StatusCode"))
    if type(code) is not int:
        raise DeliveryError("飞书返回无法识别，请在群内核实", uncertain=True)
    if code != 0:
        raise DeliveryError(f"飞书拒绝消息（错误码 {code}），请检查机器人安全设置")


@contextmanager
def delivery_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise DeliveryError("已有发送任务正在处理，请稍后查看结果") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def write_state(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(value, ensure_ascii=False).encode())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def send_once(settings: FeishuSettings, digest: DailyDigest, *, sender=None) -> DeliveryResult:
    settings.validate()
    key = hashlib.sha256(
        f"{settings.webhook}|{digest.day}|{digest.demo}|{digest.scope_key}".encode()
    ).hexdigest()
    path = settings.state_dir / f"{key}.json"
    with delivery_lock(settings.state_dir / f"{key}.lock"):
        if path.exists():
            try:
                previous = json.loads(path.read_bytes())
                status = previous["status"]
            except (ValueError, KeyError, TypeError):
                raise DeliveryError("发送记录损坏，请先在飞书群核实，避免重复发送") from None
            if status == "sent":
                return DeliveryResult(
                    status="already_sent", day=str(digest.day), message="该日报已发送，未重复推送"
                )
            if status != "failed":
                raise DeliveryError("上次发送结果不确定，请先在飞书群核实", uncertain=True)
        state = {
            "day": str(digest.day),
            "demo": digest.demo,
            "status": "sending",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        # Record intent before the network call, so a process crash never causes blind retry.
        write_state(path, state)
        try:
            (sender or send_webhook)(settings, digest)
        except Exception as exc:
            uncertain = not isinstance(exc, DeliveryError) or exc.uncertain
            write_state(path, {**state, "status": "unknown" if uncertain else "failed"})
            if isinstance(exc, DeliveryError):
                raise
            raise DeliveryError("发送结果不确定，请先在飞书群核实", uncertain=True) from None
        write_state(path, {**state, "status": "sent"})
        return DeliveryResult(status="sent", day=str(digest.day), message="日报已发送到飞书群")
