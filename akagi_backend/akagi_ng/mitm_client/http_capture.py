from __future__ import annotations

import json
import threading
import time
from collections import deque
from contextlib import suppress
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, unquote, urlsplit

if TYPE_CHECKING:
    from mitmproxy.http import Headers, HTTPFlow

MAX_CAPTURE_BODY = 64 * 1024
SENSITIVE_HEADERS = {"authorization", "cookie", "proxy-authorization", "set-cookie"}


class HttpCaptureStore:
    def __init__(self, max_entries: int = 500) -> None:
        self._entries: deque[dict[str, Any]] = deque(maxlen=max_entries)
        self._by_flow: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def record_request(self, flow: HTTPFlow, rewrite: dict[str, Any] | None = None) -> dict[str, Any]:
        url = str(flow.request.pretty_url)
        entry = {
            "id": flow.id,
            "timestamp": time.time(),
            "method": str(flow.request.method),
            "url": url,
            "request_headers": _headers(flow.request.headers),
            "request_body": _body(flow.request.raw_content, flow.request.headers.get("content-type", "")),
            "telemetry": _telemetry(url),
            "certificate_rewrite": rewrite,
            "status_code": None,
            "response_headers": {},
            "response_body": "",
            "error": "",
        }
        with self._lock:
            self._entries.append(entry)
            self._by_flow[flow.id] = entry
            self._discard_evicted_indexes()
        return dict(entry)

    def record_response(self, flow: HTTPFlow) -> None:
        with self._lock:
            entry = self._by_flow.get(flow.id)
            if entry is None:
                return
            entry["status_code"] = flow.response.status_code
            entry["response_headers"] = _headers(flow.response.headers)
            entry["response_body"] = _body(
                flow.response.raw_content,
                flow.response.headers.get("content-type", ""),
            )

    def record_error(self, flow: HTTPFlow, message: str) -> None:
        with self._lock:
            if entry := self._by_flow.get(flow.id):
                entry["error"] = message

    def list(self, limit: int = 200) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 500))
        with self._lock:
            return [dict(entry) for entry in list(self._entries)[-safe_limit:]]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._by_flow.clear()

    def _discard_evicted_indexes(self) -> None:
        live_ids = {entry["id"] for entry in self._entries}
        for flow_id in tuple(self._by_flow):
            if flow_id not in live_ids:
                self._by_flow.pop(flow_id, None)


def _headers(headers: Headers) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in headers.items(multi=True):
        normalized = key.lower()
        shown = "<已隐藏>" if normalized in SENSITIVE_HEADERS else value
        result[key] = f"{result[key]}, {shown}" if key in result else shown
    return result


def _body(content: bytes | None, content_type: str) -> str:
    if not content:
        return ""
    clipped = content[:MAX_CAPTURE_BODY]
    suffix = f"\n…（已截断，总计 {len(content)} 字节）" if len(content) > MAX_CAPTURE_BODY else ""
    if "json" in content_type.lower():
        try:
            return json.dumps(json.loads(clipped), ensure_ascii=False, indent=2) + suffix
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
    try:
        return clipped.decode("utf-8", errors="replace") + suffix
    except Exception:
        return clipped.hex() + suffix


def _telemetry(url: str) -> dict[str, Any] | None:
    parsed = urlsplit(url)
    parameters = dict(parse_qsl(parsed.query, keep_blank_values=True))
    category = parameters.get("log_category")
    if not category:
        return None
    content: Any = parameters.get("content")
    if isinstance(content, str):
        with suppress(json.JSONDecodeError):
            content = json.loads(unquote(content))
    return {
        "category": category,
        "parameters": {key: value for key, value in parameters.items() if key != "content"},
        "content": content,
    }


http_capture_store = HttpCaptureStore()
