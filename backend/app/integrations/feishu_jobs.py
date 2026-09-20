"""Transactional task/result/outbox storage in the separate assistant database."""

import hashlib
import json
import os
import uuid
from contextlib import contextmanager

import pyodbc

ASSISTANT_DATABASE = "ERP_AI_Assistant"
DEFAULT_ASSISTANT_ODBC = (
    r"DRIVER={ODBC Driver 18 for SQL Server};SERVER=lpc:.\ERPLOCAL;"
    "DATABASE=ERP_AI_Assistant;Trusted_Connection=yes;Encrypt=yes;TrustServerCertificate=yes"
)


class JobStoreError(ValueError):
    pass


class SqlJobStore:
    def __init__(self, app_id: str, tenant_key: str):
        if not app_id or not tenant_key:
            raise JobStoreError("助手任务必须绑定应用与租户")
        self.app_id = app_id
        self.tenant_key = tenant_key

    @contextmanager
    def connection(self):
        connection = pyodbc.connect(
            os.getenv("ERP_ASSISTANT_ODBC", DEFAULT_ASSISTANT_ODBC), timeout=5, autocommit=False
        )
        try:
            connection.timeout = 15
            if connection.execute("SELECT DB_NAME()").fetchone()[0] != ASSISTANT_DATABASE:
                raise JobStoreError("助手连接必须指向独立 ERP_AI_Assistant 数据库")
            yield connection
        finally:
            connection.close()

    def check(self):
        with self.connection() as connection:
            connection.execute("SELECT TOP (0) job_id FROM erp_ai.feishu_sales_jobs")

    def latest_analysis_context(self, job: dict) -> dict | None:
        """Only this person's successfully delivered plan, in this group, for 30 minutes."""
        with self.connection() as connection:
            row = connection.execute(
                "SELECT TOP (1) payload_json FROM erp_ai.feishu_sales_jobs "
                "WHERE app_id=? AND tenant_key=? AND chat_id=? AND user_open_id=? "
                "AND created_at <= ? AND job_id<>? "
                "AND ((JSON_VALUE(payload_json,'$.kind')='analysis' AND status='sent' "
                "AND JSON_VALUE(result_json,'$.run_id') IS NOT NULL "
                "AND updated_at <= ? AND updated_at >= DATEADD(minute,-30,CAST(? AS datetime2))) "
                "OR (JSON_VALUE(payload_json,'$.kind')='clear_context' "
                "AND status IN ('queued','analyzing','ready','sending','sent','unknown'))) "
                "ORDER BY created_at DESC, job_id DESC",
                self.app_id,
                self.tenant_key,
                job["chat_id"],
                job["user_open_id"],
                job["created_at"],
                job["job_id"],
                job["created_at"],
                job["created_at"],
            ).fetchone()
        payload = json.loads(row[0]) if row else None
        return payload if payload and payload.get("kind") == "analysis" else None

    def enqueue(self, identity: dict, payload: dict) -> str:
        values = {**identity, "app_id": self.app_id, "tenant_key": self.tenant_key}
        for field in ("chat_id", "user_open_id", "message_id", "event_id"):
            if not isinstance(values.get(field), str) or not 0 < len(values[field]) <= 200:
                raise JobStoreError("消息标识无效")
        key = hashlib.sha256(
            json.dumps([self.app_id, self.tenant_key, identity["message_id"]]).encode()
        ).hexdigest()
        lock_key = hashlib.sha256(f"{self.app_id}|{self.tenant_key}".encode()).hexdigest()
        with self.connection() as connection:
            lock = connection.execute(
                "DECLARE @r int; EXEC @r = sys.sp_getapplock @Resource=?, "
                "@LockMode='Exclusive', @LockOwner='Transaction', @LockTimeout=5000; SELECT @r",
                "feishu-enqueue-" + lock_key,
            ).fetchone()[0]
            if lock < 0:
                raise JobStoreError("任务队列暂忙")
            if connection.execute(
                "SELECT TOP (1) job_id FROM erp_ai.feishu_sales_jobs "
                "WHERE app_id=? AND tenant_key=? AND (message_id=? OR event_id=?)",
                self.app_id,
                self.tenant_key,
                identity["message_id"],
                identity["event_id"],
            ).fetchone():
                connection.commit()
                return "duplicate"
            active = connection.execute(
                "SELECT COUNT(*) FROM erp_ai.feishu_sales_jobs WHERE app_id=? AND tenant_key=? "
                "AND status IN ('queued','analyzing','ready','sending')",
                self.app_id,
                self.tenant_key,
            ).fetchone()[0]
            if active >= 200:
                raise JobStoreError("任务队列已满，请稍后重试")
            busy = (
                connection.execute(
                    "SELECT TOP (1) job_id FROM erp_ai.feishu_sales_jobs "
                    "WHERE app_id=? AND tenant_key=? "
                    "AND user_open_id=? AND status IN ('queued','analyzing')",
                    self.app_id,
                    self.tenant_key,
                    identity["user_open_id"],
                ).fetchone()
                is not None
            )
            reply = "你有一个查询正在处理，请等结果返回后再提问。" if busy else None
            connection.execute(
                "INSERT INTO erp_ai.feishu_sales_jobs "
                "(job_id,app_id,tenant_key,chat_id,user_open_id,message_id,event_id,status,"
                "payload_json,reply_text) VALUES (?,?,?,?,?,?,?,?,?,?)",
                key,
                self.app_id,
                self.tenant_key,
                identity["chat_id"],
                identity["user_open_id"],
                identity["message_id"],
                identity["event_id"],
                "ready" if busy else "queued",
                json.dumps({"kind": "busy"} if busy else payload, ensure_ascii=False),
                reply,
            )
            connection.commit()
            return "busy" if busy else "queued"

    @staticmethod
    def _job(cursor):
        row = cursor.fetchone()
        if row is None:
            return None
        result = dict(zip((column[0] for column in cursor.description), row, strict=True))
        result["payload"] = json.loads(result.pop("payload_json"))
        result["result"] = json.loads(result.pop("result_json") or "{}")
        result["request_id"] = result["job_id"][:32]
        return result

    def claim(self) -> dict | None:
        token = uuid.uuid4().hex
        with self.connection() as connection:
            connection.execute(
                "UPDATE erp_ai.feishu_sales_jobs SET status='queued', lease_token=NULL "
                "WHERE app_id=? AND tenant_key=? AND status='analyzing' "
                "AND lease_until < SYSUTCDATETIME()",
                self.app_id,
                self.tenant_key,
            )
            cursor = connection.execute(
                ";WITH candidate AS (SELECT TOP (1) * FROM erp_ai.feishu_sales_jobs "
                "WITH (UPDLOCK,READPAST,ROWLOCK) WHERE app_id=? AND tenant_key=? "
                "AND status='queued' ORDER BY created_at,job_id) "
                "UPDATE candidate SET status='analyzing', lease_token=?, "
                "lease_until=DATEADD(second,120,SYSUTCDATETIME()), updated_at=SYSUTCDATETIME() "
                "OUTPUT inserted.*",
                self.app_id,
                self.tenant_key,
                token,
            )
            job = self._job(cursor)
            connection.commit()
            return job

    def replace_payload(self, job: dict, payload: dict) -> bool:
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE erp_ai.feishu_sales_jobs SET payload_json=?, "
                "updated_at=SYSUTCDATETIME() WHERE job_id=? AND app_id=? AND tenant_key=? "
                "AND status='analyzing' AND lease_token=?",
                json.dumps(payload, ensure_ascii=False),
                job["job_id"],
                self.app_id,
                self.tenant_key,
                job["lease_token"],
            )
            updated = cursor.rowcount == 1
            connection.commit()
            return updated

    def finish_analysis(self, job: dict, result: dict, reply_text: str) -> bool:
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE erp_ai.feishu_sales_jobs SET status='ready',result_json=?,reply_text=?, "
                "payload_json=JSON_MODIFY(payload_json,'$.question',NULL), "
                "lease_until=NULL,updated_at=SYSUTCDATETIME() WHERE job_id=? AND app_id=? "
                "AND tenant_key=? AND status='analyzing' AND lease_token=?",
                json.dumps(result, ensure_ascii=False),
                reply_text,
                job["job_id"],
                self.app_id,
                self.tenant_key,
                job["lease_token"],
            )
            updated = cursor.rowcount == 1
            connection.commit()
            return updated

    def next_reply(self) -> dict | None:
        with self.connection() as connection:
            cursor = connection.execute(
                ";WITH candidate AS (SELECT TOP (1) * FROM erp_ai.feishu_sales_jobs "
                "WITH (UPDLOCK,READPAST,ROWLOCK) WHERE app_id=? AND tenant_key=? "
                "AND status='ready' "
                "ORDER BY created_at,job_id) UPDATE candidate SET status='sending', "
                "updated_at=SYSUTCDATETIME() OUTPUT inserted.*",
                self.app_id,
                self.tenant_key,
            )
            job = self._job(cursor)
            connection.commit()
            return job

    def mark(self, job_id: str, status: str, error_code: int | None = None):
        if status not in {"sent", "rejected", "unknown", "revoked", "failed"}:
            raise JobStoreError("非法任务状态")
        if error_code is not None and type(error_code) is not int:
            raise JobStoreError("错误码必须为整数")
        with self.connection() as connection:
            connection.execute(
                "UPDATE erp_ai.feishu_sales_jobs SET status=?,error_code=?, "
                "payload_json=JSON_MODIFY(payload_json,'$.question',NULL), "
                "updated_at=SYSUTCDATETIME() WHERE job_id=? AND app_id=? AND tenant_key=? "
                "AND status IN ('analyzing','sending')",
                status,
                error_code,
                job_id,
                self.app_id,
                self.tenant_key,
            )
            connection.commit()

    def recover(self):
        """Only call under the single connector lock, before starting its worker."""
        with self.connection() as connection:
            connection.execute(
                "UPDATE erp_ai.feishu_sales_jobs SET status='unknown',updated_at=SYSUTCDATETIME() "
                "WHERE app_id=? AND tenant_key=? AND status='sending'",
                self.app_id,
                self.tenant_key,
            )
            connection.commit()
