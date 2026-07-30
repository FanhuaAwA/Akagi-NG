"""Shared proxy handling for all OT3 HTTP clients."""

from __future__ import annotations

from urllib.parse import urlparse

import requests

OT3_PROXY_SCHEMES = frozenset({"http", "https", "socks5", "socks5h"})


def normalize_ot3_proxy(value: str) -> str:
    """Validate an OT3 proxy URL without exposing it in error messages."""
    proxy = value.strip()
    if not proxy:
        return ""

    parsed = urlparse(proxy)
    if parsed.scheme.lower() not in OT3_PROXY_SCHEMES or not parsed.hostname:
        raise ValueError("OT3 proxy must use http://, https://, socks5://, or socks5h://")
    if parsed.query or parsed.fragment:
        raise ValueError("OT3 proxy must not contain query parameters or fragments")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("OT3 proxy contains an invalid port") from exc
    return proxy


def configure_ot3_session(session: requests.Session, proxy: str = "") -> None:
    """Make proxy-off deterministic and proxy-on apply to HTTP and HTTPS."""
    session.trust_env = False
    normalized = normalize_ot3_proxy(proxy)
    if normalized:
        session.proxies.update({"http": normalized, "https": normalized})
