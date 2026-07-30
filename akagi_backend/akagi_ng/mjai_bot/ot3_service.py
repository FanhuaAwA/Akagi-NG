"""Typed client for OT3 management, redeem, and self-serve purchase APIs.

This module intentionally stays separate from the live-game ``OT3Client``:
management calls have longer timeouts and must never affect its circuit breaker.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import requests

from akagi_ng.mjai_bot.ot3_proxy import configure_ot3_session

OT3_MANAGEMENT_TIMEOUT_SECONDS = 8.0
OT3_PURCHASE_TIMEOUT_SECONDS = 20.0
HTTP_FORBIDDEN = 403
OT3_PRODUCTS = frozenset({"pro-30", "pro-90", "pro-monthly", "pro-yearly"})
OT3_ENDPOINT_LABELS = {
    "/healthz": "Health check",
    "/v3/key": "Key status",
    "/v3/models": "Model list",
    "/v3/redeem": "Redeem",
    "/paypal/create-order": "Create order",
    "/paypal/order-result": "Order result",
    "/paypal/create-subscription": "Create subscription",
    "/paypal/subscription-result": "Subscription result",
}


class OT3ServiceError(RuntimeError):
    """A safe-to-display OT3 service error that contains no credentials."""


class OT3ServiceClient:
    """Client for the non-gameplay OT3 API surface."""

    def __init__(self, base_url: str, api_key: str = "", proxy: str = ""):
        self.base_url = _normalize_base_url(base_url)
        self._api_key = api_key.strip()
        self.session = requests.Session()
        configure_ot3_session(self.session, proxy)
        self.session.headers.update({"Accept": "application/json"})

    def health(self) -> dict[str, Any]:
        value = self._request_json("GET", "/healthz")
        return {
            "status": _string(value, "status"),
            "models": _string_list(value, "models"),
            "queue_depth": _number_map(value, "queue_depth"),
        }

    def key_status(self) -> dict[str, Any]:
        value = self._request_json("GET", "/v3/key", authenticated=True)
        return {
            "plan": _string(value, "plan"),
            "expires_at": _string(value, "expires_at"),
            "usage_today": _integer(value, "usage_today"),
            "rpd": _integer(value, "rpd"),
            "rpm": _number(value, "rpm"),
            "topk": _integer(value, "topk"),
        }

    def models(self) -> list[dict[str, str]]:
        value = self._request_json("GET", "/v3/models", authenticated=True)
        models = value.get("models", [])
        if not isinstance(models, list):
            raise OT3ServiceError("Model list returned an invalid response")

        result: list[dict[str, str]] = []
        for model in models:
            if not isinstance(model, dict) or not isinstance(model.get("id"), str):
                raise OT3ServiceError("Model list returned an invalid response")
            result.append(
                {
                    "id": model["id"],
                    "game": model.get("game", "") if isinstance(model.get("game", ""), str) else "",
                    "desc": model.get("desc", "") if isinstance(model.get("desc", ""), str) else "",
                }
            )
        return result

    def redeem(self, code: str, *, email: str = "", renew_key: str = "") -> dict[str, Any]:
        clean_code = _required_text(code, "Redeem code", max_length=256)
        payload: dict[str, str] = {"code": clean_code}
        if email.strip():
            payload["email"] = _bounded_text(email, "Email", max_length=320)
        if renew_key.strip():
            payload["renew_key"] = _bounded_text(renew_key, "Renew key", max_length=256)

        value = self._request_json("POST", "/v3/redeem", json=payload)
        key = value.get("key")
        if key is not None and not isinstance(key, str):
            raise OT3ServiceError("Redeem returned an invalid response")
        return {
            "key": key,
            "key_last4": _string(value, "key_last4"),
            "plan": _string(value, "plan"),
            "expires_at": _string(value, "expires_at"),
            "extended": _boolean(value, "extended"),
        }

    def create_order(self, product: str, *, redeem: bool) -> dict[str, str]:
        product_id = _validate_product(product)
        if not isinstance(redeem, bool):
            raise ValueError("redeem must be a boolean")
        value = self._request_json(
            "POST",
            "/paypal/create-order",
            timeout=OT3_PURCHASE_TIMEOUT_SECONDS,
            json={"product": product_id, "redeem": redeem},
        )
        return _created_purchase(value, id_field="order_id")

    def order_result(self, order_id: str, claim: str) -> dict[str, Any]:
        value = self._request_json(
            "POST",
            "/paypal/order-result",
            timeout=OT3_PURCHASE_TIMEOUT_SECONDS,
            json={
                "order_id": _required_text(order_id, "Order ID", max_length=256),
                "claim": _required_text(claim, "Claim", max_length=512),
            },
        )
        return _purchase_result(value, subscription=False)

    def create_subscription(self, product: str) -> dict[str, str]:
        product_id = _validate_product(product)
        if product_id not in {"pro-monthly", "pro-yearly"}:
            raise ValueError("Selected product is not a subscription")
        value = self._request_json(
            "POST",
            "/paypal/create-subscription",
            timeout=OT3_PURCHASE_TIMEOUT_SECONDS,
            json={"product": product_id},
        )
        return _created_purchase(value, id_field="subscription_id")

    def subscription_result(self, subscription_id: str, claim: str) -> dict[str, Any]:
        value = self._request_json(
            "POST",
            "/paypal/subscription-result",
            timeout=OT3_PURCHASE_TIMEOUT_SECONDS,
            json={
                "subscription_id": _required_text(subscription_id, "Subscription ID", max_length=256),
                "claim": _required_text(claim, "Claim", max_length=512),
            },
        )
        return _purchase_result(value, subscription=True)

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool = False,
        timeout: float = OT3_MANAGEMENT_TIMEOUT_SECONDS,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        label = OT3_ENDPOINT_LABELS[path]
        headers: dict[str, str] = {}
        if authenticated:
            if not self._api_key:
                raise ValueError("API key is required")
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                json=json,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise OT3ServiceError(f"{label} could not reach the OT3 service") from exc

        if response.status_code == HTTP_FORBIDDEN and (
            response.headers.get("server", "").lower() == "cloudflare" or response.headers.get("cf-ray") is not None
        ):
            raise OT3ServiceError(
                f"{label} was blocked by Cloudflare before reaching OT3 (HTTP 403); "
                "this is not an API-key validation result"
            )
        if not response.ok:
            raise OT3ServiceError(f"{label} failed (HTTP {response.status_code})")

        try:
            value = response.json()
        except ValueError as exc:
            raise OT3ServiceError(f"{label} returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise OT3ServiceError(f"{label} returned an invalid response")
        return value


def _normalize_base_url(value: str) -> str:
    base_url = value.strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Server URL must use http:// or https://")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Server URL must not contain credentials, query parameters, or fragments")
    return base_url


def _validate_product(value: str) -> str:
    product = value.strip()
    if product not in OT3_PRODUCTS:
        raise ValueError("Unknown OT3 product")
    return product


def _required_text(value: object, label: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return _bounded_text(value, label, max_length=max_length)


def _bounded_text(value: str, label: str, *, max_length: int) -> str:
    result = value.strip()
    if len(result) > max_length:
        raise ValueError(f"{label} is too long")
    return result


def _string(value: dict[str, Any], field: str) -> str:
    item = value.get(field, "")
    if not isinstance(item, str):
        raise OT3ServiceError("OT3 service returned an invalid response")
    return item


def _boolean(value: dict[str, Any], field: str) -> bool:
    item = value.get(field, False)
    if not isinstance(item, bool):
        raise OT3ServiceError("OT3 service returned an invalid response")
    return item


def _integer(value: dict[str, Any], field: str) -> int:
    item = value.get(field, 0)
    if isinstance(item, bool) or not isinstance(item, int):
        raise OT3ServiceError("OT3 service returned an invalid response")
    return item


def _number(value: dict[str, Any], field: str) -> float:
    item = value.get(field, 0)
    if isinstance(item, bool) or not isinstance(item, int | float):
        raise OT3ServiceError("OT3 service returned an invalid response")
    return float(item)


def _string_list(value: dict[str, Any], field: str) -> list[str]:
    item = value.get(field, [])
    if not isinstance(item, list) or not all(isinstance(entry, str) for entry in item):
        raise OT3ServiceError("OT3 service returned an invalid response")
    return item


def _number_map(value: dict[str, Any], field: str) -> dict[str, float]:
    item = value.get(field, {})
    if not isinstance(item, dict):
        raise OT3ServiceError("OT3 service returned an invalid response")
    result: dict[str, float] = {}
    for key, number in item.items():
        if not isinstance(key, str) or isinstance(number, bool) or not isinstance(number, int | float):
            raise OT3ServiceError("OT3 service returned an invalid response")
        result[key] = float(number)
    return result


def _created_purchase(value: dict[str, Any], *, id_field: str) -> dict[str, str]:
    identifier = _required_text(value.get(id_field), id_field, max_length=256)
    approve_url = _required_text(value.get("approve_url"), "Approve URL", max_length=2048)
    claim_secret = _required_text(value.get("claim_secret"), "Claim secret", max_length=512)
    if not _is_paypal_url(approve_url):
        raise OT3ServiceError("Purchase server returned an unsafe approval URL")
    return {id_field: identifier, "approve_url": approve_url, "claim_secret": claim_secret}


def _is_paypal_url(value: str) -> bool:
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (hostname == "paypal.com" or hostname.endswith(".paypal.com"))


def _purchase_result(value: dict[str, Any], *, subscription: bool) -> dict[str, Any]:
    status = _string(value, "status")
    result: dict[str, Any] = {"status": status}
    for field in ("key", "code", "plan", "next_billing"):
        item = value.get(field)
        if item is not None:
            if not isinstance(item, str):
                raise OT3ServiceError("Purchase status returned an invalid response")
            result[field] = item
    if not subscription:
        days = value.get("days")
        if days is not None:
            if isinstance(days, bool) or not isinstance(days, int):
                raise OT3ServiceError("Purchase status returned an invalid response")
            result["days"] = days
    return result
