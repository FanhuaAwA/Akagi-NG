"""Management client for the FlyA Test API."""

from __future__ import annotations

import os
import re
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from akagi_ng.mjai_bot.logger import logger
from akagi_ng.mjai_bot.ot3_proxy import configure_ot3_session

FLYA_TEST_API_PREFIX = "/beta/v1"
FLYA_TEST_TIMEOUT_SECONDS = (5.0, 10.0)
_SAFE_ERROR_CODES = frozenset(
    {
        "authoritative_state_forbidden",
        "bad_deadline",
        "bad_request_id",
        "idempotency_conflict",
        "match_length_invalid",
        "model_policy_unavailable",
        "model_rule_unsupported",
        "model_unavailable",
        "request_in_progress",
        "session_state_invalid",
        "state_digest_mismatch",
        "test_api_disabled",
        "test_api_key_invalid",
        "test_api_key_frozen",
        "test_api_model_unavailable",
        "test_api_quota_exhausted",
        "test_api_five_hour_quota_exhausted",
        "test_api_weekly_quota_exhausted",
        "test_api_subscription_expired",
        "decision_not_required",
        "state_replay_incomplete",
        "state_replay_invalid",
    }
)


class FlyATestServiceError(RuntimeError):
    """A safe-to-display error that contains no request secrets."""


def _log_flya_http_request(
    operation: str,
    method: str,
    url: str,
    attempt: int,
    **fields: object,
) -> float:
    """Log safe request metadata without headers, keys, or the event body."""
    suffix = " ".join(f"{key}={_safe_log_value(value)}" for key, value in fields.items() if value is not None)
    logger.info(
        f"FlyA HTTP request operation={operation} method={method} url={url} attempt={attempt}"
        f"{f' {suffix}' if suffix else ''}"
    )
    return time.monotonic()


def _log_flya_http_response(  # noqa: PLR0913
    operation: str,
    method: str,
    url: str,
    attempt: int,
    started_at: float,
    response: requests.Response,
) -> None:
    code = _safe_response_error_code(response)
    suffix = f" error_code={code}" if code else ""
    message = (
        f"FlyA HTTP response operation={operation} method={method} url={url} attempt={attempt} "
        f"status={response.status_code} elapsed_ms={(time.monotonic() - started_at) * 1000:.0f}{suffix}"
    )
    if HTTPStatus.OK <= response.status_code < HTTPStatus.MULTIPLE_CHOICES:
        logger.info(message)
    else:
        logger.warning(message)


def _log_flya_http_exception(  # noqa: PLR0913
    operation: str,
    method: str,
    url: str,
    attempt: int,
    started_at: float,
    error: requests.RequestException,
    *secrets: str,
) -> None:
    detail = " ".join(str(error).split())
    for secret in secrets:
        if secret:
            detail = detail.replace(secret, "<redacted>")
    detail = re.sub(r"(?i)bearer\s+\S+", "Bearer <redacted>", detail)[:400]
    logger.warning(
        f"FlyA HTTP exception operation={operation} method={method} url={url} attempt={attempt} "
        f"elapsed_ms={(time.monotonic() - started_at) * 1000:.0f} error_type={type(error).__name__} "
        f"detail={_safe_log_value(detail)}"
    )


def _safe_log_value(value: object) -> str:
    return str(value).replace("\r", " ").replace("\n", " ").replace(" ", "_")[:400]


class FlyATestServiceClient:
    """Client for FlyA Test API model and quota management."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        proxy: str = "",
        ca_file: str | os.PathLike[str] | None = None,
    ):
        self.base_url = _normalize_base_url(base_url)
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("FlyA test API key is required")
        self._api_key = api_key.strip()
        self._verify = _resolve_ca_file(ca_file)
        self.session = requests.Session()
        configure_ot3_session(self.session, proxy)
        self.session.headers.update({"Accept": "application/json"})

    def models(self) -> list[dict[str, Any]]:
        value = self._request_json("/models", "Model list")
        models = value.get("models")
        if not isinstance(models, list):
            raise FlyATestServiceError("Model list returned an invalid response")

        result: list[dict[str, Any]] = []
        for model in models:
            if not isinstance(model, dict):
                raise FlyATestServiceError("Model list returned an invalid response")
            try:
                model_id = _text(model["model_id"])
                display_name = _text(model["display_name"])
                rule_line = _text(model["rule_line"])
                game = {"riichi3p": "3p", "riichi4p": "4p"}[rule_line]
                provider = _text(model["provider"])
                multiplier = _decimal_text(model["multiplier"])
                cost_milliunits = _integer(model["cost_milliunits"])
                available = _boolean(model["available"])
                unavailable_reason = _nullable_text(model["unavailable_reason"])
            except (KeyError, ValueError):
                raise FlyATestServiceError("Model list returned an invalid response") from None
            result.append(
                {
                    "id": model_id,
                    "game": game,
                    "desc": display_name,
                    "display_name": display_name,
                    "available": available,
                    "unavailable_reason": unavailable_reason,
                    "cost_milliunits": cost_milliunits,
                    "multiplier": multiplier,
                    "provider": provider,
                    "rule_line": rule_line,
                }
            )
        return result

    def quota(self) -> dict[str, Any]:
        value = self._request_json("/quota", "Quota")
        try:
            key_kind = _text(value["key_kind"])
            status = _text(value["status"])
            if key_kind not in {"paygo", "subscription"} or status not in {"active", "grace"}:
                raise ValueError
            result: dict[str, Any] = {
                "key_kind": key_kind,
                "status": status,
                "expires_at": _timestamp_text(value["expires_at"]),
                "destroy_at": _nullable_timestamp_text(value.get("destroy_at")),
            }
            if key_kind == "paygo":
                result.update(
                    {
                        "total": _decimal_text(value["quota_total"], max_decimal_places=3),
                        "used": _decimal_text(value["quota_used"], max_decimal_places=3),
                        "remaining": _decimal_text(value["quota_remaining"], max_decimal_places=3, allow_negative=True),
                    }
                )
            else:
                result.update(
                    {
                        "five_hour": _quota_window(value["five_hour"]),
                        "weekly": _quota_window(value["weekly"]),
                    }
                )
            return result
        except (KeyError, ValueError):
            raise FlyATestServiceError("Quota returned an invalid response") from None

    def health(self) -> dict[str, Any]:
        models = self.models()
        return {
            "status": "ok" if models and all(model["available"] for model in models) else "degraded",
            "models": [model["id"] for model in models],
            "queue_depth": {},
        }

    def _request_json(self, path: str, label: str) -> dict[str, Any]:
        response: requests.Response
        try:
            response = self._get(path, label, attempt=1)
        except requests.RequestException:
            raise FlyATestServiceError(f"{label} could not reach the FlyA test service") from None

        if (
            path != "/quota"
            and response.status_code == HTTPStatus.FORBIDDEN
            and _response_error_code(response) == "test_api_key_frozen"
        ):
            self.quota()
            try:
                response = self._get(path, label, attempt=2)
            except requests.RequestException:
                raise FlyATestServiceError(f"{label} could not reach the FlyA test service") from None

        if not HTTPStatus.OK <= response.status_code < HTTPStatus.MULTIPLE_CHOICES:
            code = _safe_response_error_code(response)
            detail = f", {code}" if code else ""
            raise FlyATestServiceError(f"{label} failed (HTTP {response.status_code}{detail})")
        try:
            value = response.json()
        except ValueError:
            raise FlyATestServiceError(f"{label} returned invalid JSON") from None
        if not isinstance(value, dict):
            raise FlyATestServiceError(f"{label} returned an invalid response")
        return value

    def _get(self, path: str, label: str, *, attempt: int) -> requests.Response:
        url = f"{self.base_url}{path}"
        operation = label.lower().replace(" ", "-")
        started_at = _log_flya_http_request(operation, "GET", url, attempt)
        try:
            response = self.session.request(
                "GET",
                url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=FLYA_TEST_TIMEOUT_SECONDS,
                verify=self._verify,
                allow_redirects=False,
            )
        except requests.RequestException as error:
            _log_flya_http_exception(operation, "GET", url, attempt, started_at, error, self._api_key)
            raise
        _log_flya_http_response(operation, "GET", url, attempt, started_at, response)
        return response


def _normalize_base_url(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("FlyA test server URL must be a string")
    try:
        parsed = urlparse(value.strip())
        _ = parsed.port
    except ValueError:
        raise ValueError("FlyA test server URL is invalid") from None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("FlyA test server URL must use http:// or https://")
    if parsed.username or parsed.password or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("FlyA test server URL must be an origin without credentials or parameters")
    if parsed.path not in {"", "/", FLYA_TEST_API_PREFIX, f"{FLYA_TEST_API_PREFIX}/"}:
        raise ValueError("FlyA test server URL must be an origin or end with /beta/v1")
    return f"{parsed.scheme.lower()}://{parsed.netloc}{FLYA_TEST_API_PREFIX}"


def _resolve_ca_file(value: str | os.PathLike[str] | None) -> bool | str:
    selected = os.environ.get("FLYA_TEST_CA_FILE", "") if value is None else value
    try:
        ca_file = os.fspath(selected).strip()
    except TypeError:
        raise ValueError("FlyA test CA file must be a path") from None
    if not ca_file:
        return os.environ.get("FLYA_TEST_INSECURE") != "1"
    path = Path(ca_file).expanduser()
    if not path.is_file():
        raise ValueError("FlyA test CA file does not exist")
    return str(path)


def _text(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError
    return value


def _nullable_text(value: object) -> str | None:
    if value is None:
        return None
    return _text(value)


def _timestamp_text(value: object) -> str:
    text = _text(value)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError from None
    if parsed.tzinfo is None:
        raise ValueError
    return text


def _nullable_timestamp_text(value: object) -> str | None:
    if value is None:
        return None
    return _timestamp_text(value)


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError
    return value


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError
    return value


def _decimal_text(value: object, *, max_decimal_places: int | None = None, allow_negative: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError
    whole, dot, fraction = value.partition(".")
    digits = whole[1:] if allow_negative and whole.startswith("-") else whole
    if not digits.isdigit() or (dot and not fraction.isdigit()):
        raise ValueError
    if max_decimal_places is not None and len(fraction) > max_decimal_places:
        raise ValueError
    try:
        decimal = Decimal(value)
    except InvalidOperation:
        raise ValueError from None
    if not decimal.is_finite():
        raise ValueError
    return value


def _quota_window(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError
    return {
        "limit": _decimal_text(value["limit"], max_decimal_places=3),
        "used": _decimal_text(value["used"], max_decimal_places=3),
        "remaining": _decimal_text(value["remaining"], max_decimal_places=3, allow_negative=True),
        "resets_at": _timestamp_text(value["resets_at"]),
    }


def _response_error_code(response: requests.Response) -> str | None:
    try:
        value = response.json()
    except ValueError:
        return None
    code = value.get("error") if isinstance(value, dict) else None
    return code if isinstance(code, str) else None


def _safe_response_error_code(response: requests.Response) -> str | None:
    code = _response_error_code(response)
    return code if code in _SAFE_ERROR_CODES else None
