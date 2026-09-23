#!/usr/bin/env python3
# Copyright 2026 Sergey Zinchenko
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""OpenAI-compatible → DIAL (Azure OpenAI shape) reverse proxy."""

from __future__ import annotations

import json
import os
import re
from typing import AsyncIterator
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

DIAL_UPSTREAM = os.environ.get(
    "DIAL_UPSTREAM", "http://dial-core.dial.svc.cluster.local"
).rstrip("/")
DEFAULT_API_VERSION = os.environ.get("DEFAULT_API_VERSION", "2024-10-21")
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "900"))
MODELS_UPSTREAM_PATH = os.environ.get("MODELS_UPSTREAM_PATH", "/openai/models")

# OpenClaw / LiteLLM style provider prefixes on model ids
_MODEL_PREFIX_RE = re.compile(r"^(?:litellm|openai|azure|dial)/", re.IGNORECASE)
_JWT_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    # httpx already decodes the upstream body; forwarding these would make the
    # client (e.g. OpenClaw/undici) try to gunzip plaintext → "terminated".
    "content-encoding",
}


def _strip_lone_surrogates(s: str) -> str:
    # json.loads keeps unpaired \uD800-\uDFFF escapes as lone surrogates (clients
    # truncating context mid-emoji, e.g. Copilot/OpenClaw). Such strings cannot be
    # UTF-8 encoded, so Python services downstream (interceptors, vLLM) fail the
    # request with 500 "surrogates not allowed". Valid pairs are already combined
    # by json.loads; anything left is garbage -> replace with U+FFFD.
    try:
        s.encode("utf-8")
        return s
    except UnicodeEncodeError:
        return "".join("\ufffd" if 0xD800 <= ord(ch) <= 0xDFFF else ch for ch in s)


def _sanitize_utf8(value):
    """Recursively replace lone UTF-16 surrogates in all strings."""
    if isinstance(value, str):
        return _strip_lone_surrogates(value)
    if isinstance(value, list):
        return [_sanitize_utf8(v) for v in value]
    if isinstance(value, dict):
        return {_sanitize_utf8(k): _sanitize_utf8(v) for k, v in value.items()}
    return value


def _normalize_model(model: str) -> str:
    model = model.strip()
    while True:
        updated = _MODEL_PREFIX_RE.sub("", model)
        if updated == model:
            break
        model = updated
    return model


def _resolve_api_version(request: Request) -> str:
    q = request.query_params.get("api-version")
    if q:
        return q
    for name in ("api-version", "Api-Version", "x-api-version"):
        v = request.headers.get(name)
        if v:
            return v
    return DEFAULT_API_VERSION


def _build_upstream_auth(request: Request) -> dict[str, str]:
    """Map inbound OpenAI/Azure auth to DIAL Core headers."""
    headers: dict[str, str] = {}
    api_key = request.headers.get("api-key") or request.headers.get("Api-Key")
    auth = request.headers.get("Authorization") or request.headers.get("authorization")

    bearer: str | None = None
    if auth and auth.lower().startswith("bearer "):
        bearer = auth[7:].strip()

    if api_key:
        headers["Api-Key"] = api_key

    if bearer:
        if _JWT_RE.match(bearer):
            headers["Authorization"] = f"Bearer {bearer}"
        else:
            headers.setdefault("Api-Key", bearer)

    return headers


def _filter_request_headers(request: Request, auth_headers: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    skip = {
        "authorization",
        "api-key",
        "api_key",
        "api-version",
        "x-api-version",
    }
    for k, v in request.headers.items():
        lk = k.lower()
        if lk in _HOP_BY_HOP or lk in skip:
            continue
        out[k] = v
    out.update(auth_headers)
    return out


def _filter_response_headers(headers: httpx.Headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


def _with_api_version(path: str, api_version: str) -> str:
    parsed = urlparse(path)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs["api-version"] = [api_version]
    return urlunparse(parsed._replace(query=urlencode(qs, doseq=True)))


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "healthy"})


async def list_models(request: Request) -> Response:
    api_version = _resolve_api_version(request)
    auth = _build_upstream_auth(request)
    headers = _filter_request_headers(request, auth)
    url = f"{DIAL_UPSTREAM}{_with_api_version(MODELS_UPSTREAM_PATH, api_version)}"

    timeout = httpx.Timeout(REQUEST_TIMEOUT, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        upstream = await client.get(url, headers=headers)

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_filter_response_headers(upstream.headers),
        media_type=upstream.headers.get("content-type"),
    )


async def _proxy_deployment_post(request: Request, suffix: str) -> Response:
    api_version = _resolve_api_version(request)
    body = await request.body()
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        return JSONResponse({"error": {"message": "Invalid JSON body"}}, status_code=400)

    if not isinstance(payload, dict):
        return JSONResponse({"error": {"message": "JSON body must be an object"}}, status_code=400)

    # Common case: nothing to fix -> forward the original bytes untouched.
    sanitized = _sanitize_utf8(payload)
    if sanitized != payload:
        payload = sanitized
        body = json.dumps(sanitized, ensure_ascii=False).encode("utf-8")

    model = payload.get("model")
    if not model or not isinstance(model, str):
        return JSONResponse({"error": {"message": "Missing 'model' in request body"}}, status_code=400)

    deployment = _normalize_model(model)
    if not deployment:
        return JSONResponse({"error": {"message": "Empty model id after normalization"}}, status_code=400)

    stream = bool(payload.get("stream", False))
    path = f"/openai/deployments/{deployment}/{suffix}"
    url = f"{DIAL_UPSTREAM}{_with_api_version(path, api_version)}"

    auth = _build_upstream_auth(request)
    headers = _filter_request_headers(request, auth)
    headers["content-type"] = request.headers.get("content-type", "application/json")

    timeout = httpx.Timeout(REQUEST_TIMEOUT, connect=30.0)
    client = httpx.AsyncClient(timeout=timeout)

    if stream:
        req = client.build_request("POST", url, headers=headers, content=body)
        upstream = await client.send(req, stream=True)

        async def generate() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream.aiter_bytes():
                    if chunk:
                        yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(
            generate(),
            status_code=upstream.status_code,
            headers=_filter_response_headers(upstream.headers),
            media_type=upstream.headers.get("content-type", "text/event-stream"),
        )

    try:
        upstream = await client.post(url, headers=headers, content=body)
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=_filter_response_headers(upstream.headers),
            media_type=upstream.headers.get("content-type"),
        )
    finally:
        await client.aclose()


async def chat_completions(request: Request) -> Response:
    return await _proxy_deployment_post(request, "chat/completions")


async def embeddings(request: Request) -> Response:
    return await _proxy_deployment_post(request, "embeddings")


routes = [
    Route("/health", health, methods=["GET"]),
    Route("/v1/models", list_models, methods=["GET"]),
    Route("/v1/chat/completions", chat_completions, methods=["POST"]),
    Route("/v1/embeddings", embeddings, methods=["POST"]),
]

app = Starlette(routes=routes)
