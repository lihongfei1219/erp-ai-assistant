import asyncio
import json
import time
from datetime import date

import httpx
import pytest

from app.ai.model_client import ModelClient, ModelSettings, ModelUnavailable, load_model_settings


def settings():
    return ModelSettings(base_url='https://model.example/v1', model='synthetic-model',
                         api_key='private-key', enabled=True)


def test_root_env_is_read_without_exporting_unrelated_values(tmp_path, monkeypatch):
    for key in ('ERP_AI_BASE_URL', 'ERP_AI_MODEL', 'ERP_AI_API_KEY', 'ERP_AI_ENABLED',
                'DASHSCOPE_API_KEY', 'MAIN_VITE_PI_NORMALIZER_BASE_URL',
                'MAIN_VITE_PI_NORMALIZER_MODEL'):
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / '.env'
    path.write_text('MAIN_VITE_PI_NORMALIZER_BASE_URL="https://model.example/v1"\n'
                    'MAIN_VITE_PI_NORMALIZER_MODEL=synthetic-model\n'
                    'DASHSCOPE_API_KEY=private-key\nMAIN_VITE_ERP_URL=https://private.example\n')
    cfg = load_model_settings(path)
    assert cfg.enabled and cfg.model == 'synthetic-model'
    assert 'private-key' not in repr(cfg)
    monkeypatch.setenv('ERP_AI_ENABLED', '0')
    assert not load_model_settings(path).enabled


def test_model_only_receives_question_date_and_contract():
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        assert request.headers['authorization'] == 'Bearer private-key'
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
            'content': '{"action":"clarify","intent":null,"unsupported_conditions":[]}'}}]})
    client = ModelClient(settings(), transport=httpx.MockTransport(respond))
    text = client.interpret('synthetic question', date(2026, 9, 20))
    assert json.loads(text)['action'] == 'clarify'
    assert set(requests[0]) == {'model', 'messages', 'temperature', 'max_tokens',
                                'stream', 'enable_thinking'}
    assert len(requests[0]['messages']) == 2
    assert 'synthetic question' in requests[0]['messages'][1]['content']
    assert '2026-09-20' in requests[0]['messages'][1]['content']
    assert 'private-key' not in json.dumps(requests[0])


@pytest.mark.parametrize('status', [302, 401, 429, 500])
def test_model_errors_are_sanitized_and_not_retried(status):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(status, text='private-key private-upstream-details',
                              headers={'location': 'https://untrusted.example'})
    client = ModelClient(settings(), transport=httpx.MockTransport(respond))
    with pytest.raises(ModelUnavailable) as caught:
        client.interpret('synthetic', date(2026, 9, 20))
    assert len(calls) == 1
    assert 'private-key' not in str(caught.value)


def test_oversized_or_truncated_response_never_enters_parser():
    for response in [httpx.Response(200, content=b'x' * 70000),
                     httpx.Response(200, json={'choices': [{'finish_reason': 'length',
                                         'message': {'content': '{}'}}]})]:
        client = ModelClient(settings(), transport=httpx.MockTransport(
            lambda req, response=response: response))
        with pytest.raises(ModelUnavailable):
            client.interpret('synthetic', date(2026, 9, 20))


def test_small_chunks_cannot_hide_elapsed_deadline(monkeypatch):
    elapsed = [0]
    monkeypatch.setattr('app.ai.model_client.time.monotonic', lambda: elapsed[0])
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(50):
                elapsed[0] += 10
                yield b'x'
    client = ModelClient(settings(), transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=SlowStream())))
    with pytest.raises(ModelUnavailable):
        client.interpret('synthetic', date(2026, 9, 20))
    assert elapsed[0] == 30


def test_total_timeout_cancels_wait_before_headers(monkeypatch):
    monkeypatch.setattr('app.ai.model_client.REQUEST_TIMEOUT_SECONDS', 0.02)
    cancelled = []
    async def respond(request):
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.append(True)
    client = ModelClient(settings(), transport=httpx.MockTransport(respond))
    start = time.monotonic()
    with pytest.raises(ModelUnavailable):
        client.interpret('synthetic', date(2026, 9, 20))
    assert time.monotonic() - start < 1
    assert cancelled == [True]
