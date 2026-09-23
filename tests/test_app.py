import gzip
import json

import httpx
import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

import app


def make_request(headers=None, query=""):
    headers = headers or {}
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/chat/completions",
        "query_string": query.encode(),
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    return Request(scope)


# ---- pure helpers -------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("qwen3.8-27b", "qwen3.8-27b"),
        ("openai/qwen3.8-27b", "qwen3.8-27b"),
        ("litellm/dial/qwen3.8-27b", "qwen3.8-27b"),
        ("azure/gpt-4o", "gpt-4o"),
    ],
)
def test_normalize_model(raw, expected):
    assert app._normalize_model(raw) == expected


def test_resolve_api_version_priority():
    # query wins
    req = make_request({"api-version": "from-header"}, query="api-version=from-query")
    assert app._resolve_api_version(req) == "from-query"
    # header next
    req = make_request({"api-version": "from-header"})
    assert app._resolve_api_version(req) == "from-header"
    # default fallback
    assert app._resolve_api_version(make_request()) == app.DEFAULT_API_VERSION


def test_build_upstream_auth_api_key():
    req = make_request({"Api-Key": "secret"})
    assert app._build_upstream_auth(req) == {"Api-Key": "secret"}


def test_build_upstream_auth_bearer_jwt_kept():
    jwt = "aaa.bbb.ccc"
    req = make_request({"Authorization": f"Bearer {jwt}"})
    assert app._build_upstream_auth(req) == {"Authorization": f"Bearer {jwt}"}


def test_build_upstream_auth_bearer_plain_becomes_api_key():
    req = make_request({"Authorization": "Bearer plain-api-key"})
    assert app._build_upstream_auth(req) == {"Api-Key": "plain-api-key"}


def test_filter_response_headers_strips_encoding_and_hop():
    headers = httpx.Headers(
        {
            "content-encoding": "gzip",
            "content-length": "123",
            "transfer-encoding": "chunked",
            "content-type": "text/event-stream",
            "x-upstream-attempts": "1",
        }
    )
    out = app._filter_response_headers(headers)
    assert "content-encoding" not in out
    assert "content-length" not in out
    assert "transfer-encoding" not in out
    assert out["content-type"] == "text/event-stream"
    assert out["x-upstream-attempts"] == "1"


def test_with_api_version_adds_and_overrides():
    assert "api-version=2024-10-21" in app._with_api_version("/openai/models", "2024-10-21")
    replaced = app._with_api_version("/openai/models?api-version=old", "new")
    assert "api-version=new" in replaced
    assert "old" not in replaced


# ---- integration via mocked upstream ------------------------------------


@pytest.fixture
def mock_upstream(monkeypatch):
    captured = {}
    real_async_client = httpx.AsyncClient

    def install(handler):
        def factory(*a, **k):
            k["transport"] = httpx.MockTransport(handler)
            return real_async_client(*a, **k)

        monkeypatch.setattr(httpx, "AsyncClient", factory)

    return install, captured


def test_chat_completion_general_case(mock_upstream):
    install, captured = mock_upstream

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["api_version"] = request.url.params.get("api-version")
        captured["api_key"] = request.headers.get("Api-Key")
        captured["body"] = json.loads(request.content.decode())
        body = json.dumps({"id": "x", "choices": [{"message": {"content": "hi"}}]}).encode()
        return httpx.Response(
            200,
            content=gzip.compress(body),
            headers={"content-encoding": "gzip", "content-type": "application/json"},
        )

    install(handler)
    client = TestClient(app.app)
    resp = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer plain-key"},
        json={"model": "openai/qwen3.8-27b", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert resp.status_code == 200
    # model prefix stripped, mapped to DIAL Azure shape
    assert captured["path"] == "/openai/deployments/qwen3.8-27b/chat/completions"
    assert captured["api_version"] == app.DEFAULT_API_VERSION
    # Bearer plain key remapped to Api-Key
    assert captured["api_key"] == "plain-key"
    assert captured["body"]["model"] == "openai/qwen3.8-27b"
    # gzip header stripped, body decoded and readable
    assert "content-encoding" not in {k.lower() for k in resp.headers}
    assert resp.json()["choices"][0]["message"]["content"] == "hi"


def test_chat_completion_missing_model_returns_400(mock_upstream):
    install, _ = mock_upstream
    install(lambda request: httpx.Response(200, json={}))
    client = TestClient(app.app)
    resp = client.post("/v1/chat/completions", json={"messages": []})
    assert resp.status_code == 400


def test_streaming_passthrough_strips_encoding(mock_upstream):
    install, _ = mock_upstream
    sse = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=gzip.compress(sse),
            headers={"content-encoding": "gzip", "content-type": "text/event-stream"},
        )

    install(handler)
    client = TestClient(app.app)
    resp = client.post(
        "/v1/chat/completions",
        headers={"Api-Key": "k"},
        json={"model": "qwen3.8-27b", "stream": True, "messages": []},
    )
    assert resp.status_code == 200
    assert "content-encoding" not in {k.lower() for k in resp.headers}
    assert "data: [DONE]" in resp.text


def test_lone_surrogate_sanitized_before_forwarding(mock_upstream):
    """Copilot/OpenClaw truncate context mid-emoji -> lone \\udd12 escape.

    Valid JSON, but the parsed string cannot be UTF-8 encoded and kills Python
    services downstream (500 "surrogates not allowed"). The proxy must replace
    lone surrogates with U+FFFD, keeping valid pairs.
    """
    install, captured = mock_upstream

    def handler(request: httpx.Request) -> httpx.Response:
        captured["raw"] = request.content
        return httpx.Response(200, json={"ok": True})

    install(handler)
    client = TestClient(app.app)
    raw_body = (
        b'{"model": "qwen3.8-27b",'
        b' "messages": [{"role": "user",'
        b' "content": "lock \\ud83d\\udd12 pair and lone \\udd12 tail"}]}'
    )
    resp = client.post(
        "/v1/chat/completions",
        headers={"Api-Key": "k", "content-type": "application/json"},
        content=raw_body,
    )
    assert resp.status_code == 200

    forwarded = json.loads(captured["raw"].decode("utf-8"))  # must not raise
    content = forwarded["messages"][0]["content"]
    assert "\U0001f512" in content  # valid pair preserved
    assert "\ufffd" in content  # lone surrogate replaced
    assert "\udd12" not in content


def test_clean_body_forwarded_byte_identical(mock_upstream):
    install, captured = mock_upstream

    def handler(request: httpx.Request) -> httpx.Response:
        captured["raw"] = request.content
        return httpx.Response(200, json={"ok": True})

    install(handler)
    client = TestClient(app.app)
    raw_body = b'{"model": "qwen3.8-27b", "messages": [{"role": "user", "content": "hi \\ud83d\\udd12"}]}'
    resp = client.post(
        "/v1/chat/completions",
        headers={"Api-Key": "k", "content-type": "application/json"},
        content=raw_body,
    )
    assert resp.status_code == 200
    assert captured["raw"] == raw_body


def test_health():
    client = TestClient(app.app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}
