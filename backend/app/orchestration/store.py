"""Local request ledger and result storage; checkpoints hold only structured state/references."""

import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from app.analysis.sales_query import QueryUnavailable
from app.semantic.schemas import GraphReference, SemanticContext

GRAPH_VERSION = "erp.graph.v1"
TTL = 1800


class GraphConflict(QueryUnavailable):
    def __init__(self, message, code="context_expired"):
        super().__init__(message)
        self.code = code


def state_directory(channel):
    if channel not in {"web", "feishu"}:
        raise ValueError("Unknown graph channel")
    root = Path(
        os.environ.get("ERP_GRAPH_STATE_DIR")
        or Path(__file__).resolve().parents[3] / ".local" / "langgraph"
    )
    path = root / channel
    path.mkdir(parents=True, exist_ok=True)
    return path


def owner_key(owner):
    return hashlib.sha256(owner.encode()).hexdigest()


class GraphStore:
    def __init__(self, channel="web"):
        self.channel = channel
        self.directory = state_directory(channel)
        self.path = self.directory / "requests.sqlite3"
        self.checkpoints = self.directory / "checkpoints.sqlite3"
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    thread_id TEXT PRIMARY KEY, owner TEXT NOT NULL, binding TEXT NOT NULL,
                    revision INTEGER NOT NULL, expires REAL NOT NULL,
                    busy_request TEXT, busy_since REAL
                );
                CREATE TABLE IF NOT EXISTS requests (
                    owner TEXT NOT NULL, request_id TEXT NOT NULL, thread_id TEXT NOT NULL,
                    base_revision INTEGER NOT NULL, fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL, outcome TEXT, expires REAL NOT NULL,
                    PRIMARY KEY(owner, request_id)
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, expires REAL NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def claim(self, owner, request_id, fingerprint, binding, previous=None):
        """Reserve once under a DB transaction, including callers from different event loops."""
        now = time.time()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute(
                "SELECT * FROM requests WHERE owner=? AND request_id=?", (owner, request_id)
            ).fetchone()
            if old:
                if old["fingerprint"] != fingerprint or old["expires"] <= now:
                    raise GraphConflict("请求标识已使用或过期，请重新提交。", "request_conflict")
                if old["status"] == "complete":
                    return dict(old), json.loads(old["outcome"])
                if old["status"] == "failed":
                    raise GraphConflict("上次请求未完成，请明确重试或重新提交。", "request_failed")
                active = conn.execute(
                    "SELECT busy_since FROM sessions WHERE thread_id=?", (old["thread_id"],)
                ).fetchone()
                if not active or active["busy_since"] is None or now - active["busy_since"] >= 60:
                    raise GraphConflict(
                        "上次请求的执行状态未能确认，请点击重试重新提交。", "request_failed"
                    )
                raise GraphConflict("这次请求正在处理或结果尚未确认，请稍后重试。", "request_busy")
            thread_id = previous.thread_id if previous else uuid4().hex
            session = conn.execute(
                "SELECT * FROM sessions WHERE thread_id=?", (thread_id,)
            ).fetchone()
            if previous:
                if (
                    not session
                    or session["owner"] != owner
                    or session["binding"] != binding
                    or session["expires"] <= now
                    or session["revision"] != previous.revision
                ):
                    raise GraphConflict("会话已更新或过期，请恢复当前草稿后继续。")
                if session["busy_request"]:
                    if now - session["busy_since"] < 60:
                        raise GraphConflict("上一条请求仍在处理，请稍后再提交。", "request_busy")
                    conn.execute(
                        "UPDATE requests SET status='failed' WHERE owner=? AND request_id=?",
                        (owner, session["busy_request"]),
                    )
            else:
                conn.execute(
                    "INSERT INTO sessions VALUES(?,?,?,?,?,?,?)",
                    (thread_id, owner, binding, 0, now + TTL, None, None),
                )
            revision = previous.revision if previous else 0
            conn.execute(
                "UPDATE sessions SET busy_request=?,busy_since=? WHERE thread_id=?",
                (request_id, now, thread_id),
            )
            conn.execute(
                "INSERT INTO requests VALUES(?,?,?,?,?,?,?,?)",
                (owner, request_id, thread_id, revision, fingerprint, "running", None, now + TTL),
            )
            return dict(
                owner=owner, request_id=request_id, thread_id=thread_id, base_revision=revision
            ), None

    def complete(self, ticket, payload):
        now = time.time()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute(
                "UPDATE sessions SET revision=?,expires=?,busy_request=NULL,busy_since=NULL "
                "WHERE thread_id=? AND busy_request=? AND revision=?",
                (
                    ticket["base_revision"] + 1,
                    now + TTL,
                    ticket["thread_id"],
                    ticket["request_id"],
                    ticket["base_revision"],
                ),
            ).rowcount
            if changed != 1:
                raise GraphConflict("该请求已被后续请求替代，请恢复最新草稿。")
            conn.execute(
                "UPDATE requests SET status='complete',outcome=?,expires=? "
                "WHERE owner=? AND request_id=?",
                (
                    json.dumps(payload, ensure_ascii=False),
                    now + TTL,
                    ticket["owner"],
                    ticket["request_id"],
                ),
            )

    def fail(self, ticket):
        with self.connect() as conn:
            conn.execute(
                "UPDATE requests SET status='failed' WHERE owner=? AND request_id=? "
                "AND status='running'",
                (ticket["owner"], ticket["request_id"]),
            )
            conn.execute(
                "UPDATE sessions SET busy_request=NULL,busy_since=NULL "
                "WHERE thread_id=? AND busy_request=?",
                (ticket["thread_id"], ticket["request_id"]),
            )

    def save_artifact(self, payload):
        identifier = uuid4().hex
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO artifacts VALUES(?,?,?)",
                (identifier, json.dumps(payload, ensure_ascii=False), time.time() + TTL),
            )
        return identifier

    def artifact(self, identifier):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload FROM artifacts WHERE id=? AND expires>?", (identifier, time.time())
            ).fetchone()
        if not row:
            raise GraphConflict("本次结果已过期，请重新提交。")
        return json.loads(row["payload"])

    def load_context(self, ref: GraphReference, binding, *, owner=None):
        if ref.channel != self.channel or ref.version != GRAPH_VERSION:
            raise GraphConflict("会话运行版本已变更，请恢复草稿。")
        with self.connect() as conn:
            session = conn.execute(
                "SELECT * FROM sessions WHERE thread_id=?", (ref.thread_id,)
            ).fetchone()
            row = conn.execute(
                "SELECT outcome FROM requests WHERE thread_id=? AND base_revision=? "
                "AND status='complete' AND expires>?",
                (ref.thread_id, ref.revision - 1, time.time()),
            ).fetchone()
        if (
            not session
            or session["binding"] != binding
            or session["expires"] <= time.time()
            or owner is not None
            and session["owner"] != owner
            or not row
        ):
            raise GraphConflict("会话已过期或数据范围已变化，请恢复草稿。")
        return SemanticContext.model_validate(json.loads(row["outcome"])["context"])

    def expired_threads(self):
        with self.connect() as conn:
            return [
                row["thread_id"]
                for row in conn.execute(
                    "SELECT thread_id FROM sessions WHERE expires<? "
                    "AND (busy_request IS NULL OR busy_since<?) LIMIT 50",
                    (time.time(), time.time() - 60),
                )
            ]

    def prune(self, threads):
        """Run after deleting these threads' checkpoints; no active sessions are removed."""
        with self.connect() as conn:
            for thread_id in threads:
                conn.execute("DELETE FROM requests WHERE thread_id=?", (thread_id,))
                conn.execute("DELETE FROM sessions WHERE thread_id=?", (thread_id,))
            conn.execute("DELETE FROM artifacts WHERE expires<?", (time.time(),))
            conn.execute(
                "DELETE FROM requests WHERE expires<? AND status!='running'", (time.time(),)
            )
