from datetime import date, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException

from app.notifications.daily import DigestUnavailable, build_digest
from app.notifications.feishu import DeliveryError, FeishuSettings, send_once
from app.schemas.sales import SalesReport, StrictModel


class SendDailyRequest(StrictModel):
    day: date
    demo: bool = False


def register_feishu_routes(router: APIRouter, get_report) -> None:
    Report = Annotated[SalesReport, Depends(get_report)]

    def settings_status():
        try:
            settings = FeishuSettings.from_env()
            settings.validate()
            return True, "群机器人已配置"
        except DeliveryError as exc:
            return False, str(exc)
        except OSError:
            return False, "无法读取本地飞书配置"

    def digest_for(report, day, demo):
        try:
            return build_digest(report, day, demo=demo)
        except DigestUnavailable as exc:
            raise HTTPException(409, str(exc)) from None

    @router.get("/feishu/daily")
    def preview(report: Report, day: date | None = None, demo: bool = False):
        day = day or (datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1))
        digest = digest_for(report, day, demo)
        configured, message = settings_status()
        return {
            "digest": digest,
            "configured": configured,
            "configuration_message": message,
            "schedule": "每天 08:00 · Asia/Shanghai",
            "schedule_requires_worker": True,
        }

    @router.post("/feishu/send")
    def send(body: SendDailyRequest, report: Report):
        digest = digest_for(report, body.day, body.demo)
        try:
            settings = FeishuSettings.from_env()
            settings.validate()
        except DeliveryError as exc:
            raise HTTPException(503, str(exc)) from None
        except OSError:
            raise HTTPException(503, "无法读取本地飞书配置") from None
        try:
            return send_once(settings, digest)
        except DeliveryError as exc:
            raise HTTPException(409 if exc.uncertain else 502, str(exc)) from None
        except OSError:
            raise HTTPException(503, "无法保存发送状态，请先核实群消息后再处理") from None
