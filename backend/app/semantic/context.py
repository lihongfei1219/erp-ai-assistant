"""Signed web conversations and the shared snapshot/scope binding."""

import base64
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

from app.analysis.sales_query import QueryUnavailable
from app.capabilities.view import capability_view
from app.semantic.catalog import load_catalog
from app.semantic.schemas import SemanticContext


def signing_key(principal: str = "local-workspace", *, path: Path | None = None) -> str:
    """Server-only persistent key; the local workspace does not require a login credential."""
    path = path or Path(__file__).resolve().parents[3] / ".local" / "semantic-context.key"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(secrets.token_bytes(32))
    except FileExistsError:
        pass
    secret = path.read_bytes()
    if len(secret) != 32:
        raise QueryUnavailable("会话签名配置无效，请联系管理员。")
    return hmac.new(
        secret, ("semantic-principal:" + principal).encode(), hashlib.sha256
    ).hexdigest()


def snapshot_key(report) -> str:
    value = {
        "metadata": report.metadata.model_dump(mode="json"),
        "policy": report.operating.policy_fingerprint if report.operating else None,
        "capabilities": capability_view(report),
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _signature(payload: str, secret: str) -> str:
    return hmac.new(
        secret.encode(), ("semantic.v1:" + payload).encode(), hashlib.sha256
    ).hexdigest()


def encode_context(
    context: SemanticContext, report, secret: str, *, now: float | None = None
) -> str:
    if len(secret) < 32:
        raise QueryUnavailable("会话签名未配置，请检查服务端会话密钥。")
    value = {
        **(
            {"graph": context.runtime_ref.model_dump(mode="json")}
            if context.runtime_ref
            else {"context": context.model_dump(mode="json")}
        ),
        "snapshot": snapshot_key(report),
        "expires": (time.time() if now is None else now) + 1800,
    }
    payload = base64.urlsafe_b64encode(json.dumps(value, ensure_ascii=False).encode()).decode()
    return payload + "." + _signature(payload, secret)


def decode_context(
    token: str | None, report, secret: str, *, now: float | None = None
) -> SemanticContext | None:
    if token is None:
        return None
    try:
        if len(token) > 60000 or len(secret) < 32:
            raise ValueError("invalid context")
        payload, signature = token.rsplit(".", 1)
        if not hmac.compare_digest(signature, _signature(payload, secret)):
            raise ValueError("invalid signature")
        value = json.loads(base64.urlsafe_b64decode(payload))
        clock = time.time() if now is None else now
        if value["expires"] <= clock or value["snapshot"] != snapshot_key(report):
            raise ValueError("stale context")
        if "graph" in value:
            from app.orchestration.runtime import binding_for
            from app.orchestration.store import GraphStore, owner_key
            from app.semantic.schemas import GraphReference

            context = GraphStore("web").load_context(
                GraphReference.model_validate(value["graph"]),
                binding_for(report),
                owner=owner_key("local-workspace"),
            )
        else:
            context = SemanticContext.model_validate(value["context"])
        if context.catalog_version != load_catalog()["version"]:
            raise ValueError("changed semantics")
        return context
    except Exception:
        raise QueryUnavailable(
            "追问上下文无效、已过期或数据已更新，请清除上下文后重新说明完整问题。"
        ) from None
