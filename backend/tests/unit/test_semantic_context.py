import base64
import hashlib
import hmac
import json

import pytest

from app.analysis.sales_query import QueryUnavailable
from app.semantic.context import decode_context, encode_context, signing_key
from app.semantic.schemas import SemanticContext
from tests.unit.test_feishu_analytics import setup as _setup

semantic_setup = _setup


def test_context_expiry_snapshot_binding_and_bearer_cannot_sign(semantic_setup, tmp_path):
    report, _, _ = semantic_setup
    bearer = "test-bearer-0123456789-abcdefghijklmnop"
    secret = signing_key(bearer, path=tmp_path / "key")
    assert signing_key(bearer, path=tmp_path / "key") == secret
    context = SemanticContext(intents=[dict(domain="sales", operation="summary")], pending=["time"])
    token = encode_context(context, report, secret, now=1000)
    assert decode_context(token, report, secret, now=1100).pending == ["time"]
    with pytest.raises(QueryUnavailable):
        decode_context(token, report, secret, now=2800)
    payload, _ = token.rsplit(".", 1)
    value = json.loads(base64.urlsafe_b64decode(payload))
    value["expires"] = 100000
    payload = base64.urlsafe_b64encode(json.dumps(value).encode()).decode()
    forged = (
        payload
        + "."
        + hmac.new(bearer.encode(), ("semantic.v1:" + payload).encode(), hashlib.sha256).hexdigest()
    )
    with pytest.raises(QueryUnavailable):
        decode_context(forged, report, secret, now=1100)
    changed = report.model_copy(
        update={"metadata": report.metadata.model_copy(update={"metric_version": "changed"})}
    )
    with pytest.raises(QueryUnavailable):
        decode_context(token, changed, secret, now=1100)
