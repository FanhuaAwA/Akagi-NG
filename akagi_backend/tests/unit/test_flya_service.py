from unittest.mock import MagicMock

import pytest
import requests

from akagi_ng.mjai_bot.flya_service import FlyATestServiceClient, FlyATestServiceError


def _response(payload: object, *, status: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.json.return_value = payload
    return response


def _models_payload() -> dict[str, object]:
    return {
        "models": [
            {
                "model_id": "flya-heyman-2.1",
                "display_name": "FlyA Heyman 2.1",
                "rule_line": "riichi4p",
                "provider": "flya",
                "multiplier": "1.00000000000000000000",
                "cost_milliunits": 1000,
                "available": True,
                "unavailable_reason": None,
            },
            {
                "model_id": "flya-heyman-2-3p",
                "display_name": "FlyA Heyman 2 3P",
                "rule_line": "riichi3p",
                "provider": "flya",
                "multiplier": "1.250",
                "cost_milliunits": 1250,
                "available": False,
                "unavailable_reason": "runtime_unavailable",
            },
        ]
    }


def _paygo_quota_payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": "active",
        "key_kind": "paygo",
        "expires_at": "2026-09-02T01:23:52Z",
        "quota_total": "1000.000",
        "quota_used": "1.250",
        "quota_remaining": "998.750",
        "consumed": "0.000",
        "replay": False,
    }
    payload.update(updates)
    return payload


@pytest.mark.parametrize(
    "base_url",
    ["https://server.example", "https://server.example/", "https://server.example/beta/v1/"],
)
def test_models_use_fixed_path_bearer_header_and_safe_transport(base_url: str) -> None:
    client = FlyATestServiceClient(base_url, "secret", "http://127.0.0.1:7890")
    client.session.request = MagicMock(return_value=_response(_models_payload()))

    models = client.models()

    assert models == [
        {
            "id": "flya-heyman-2.1",
            "game": "4p",
            "desc": "FlyA Heyman 2.1",
            "display_name": "FlyA Heyman 2.1",
            "available": True,
            "unavailable_reason": None,
            "cost_milliunits": 1000,
            "multiplier": "1.00000000000000000000",
            "provider": "flya",
            "rule_line": "riichi4p",
        },
        {
            "id": "flya-heyman-2-3p",
            "game": "3p",
            "desc": "FlyA Heyman 2 3P",
            "display_name": "FlyA Heyman 2 3P",
            "available": False,
            "unavailable_reason": "runtime_unavailable",
            "cost_milliunits": 1250,
            "multiplier": "1.250",
            "provider": "flya",
            "rule_line": "riichi3p",
        },
    ]
    call = client.session.request.call_args
    assert call.args == ("GET", "https://server.example/beta/v1/models")
    assert call.kwargs["headers"] == {"Authorization": "Bearer secret"}
    assert call.kwargs["allow_redirects"] is False
    assert call.kwargs["verify"] is True
    assert call.kwargs["timeout"] == (5.0, 10.0)
    assert client.session.trust_env is False
    assert client.session.proxies == {
        "http": "http://127.0.0.1:7890",
        "https": "http://127.0.0.1:7890",
    }


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("model_id", 1),
        ("rule_line", "other"),
        ("multiplier", "NaN"),
        ("cost_milliunits", True),
        ("available", 1),
        ("unavailable_reason", []),
    ],
)
def test_models_reject_invalid_fields(field: str, invalid: object) -> None:
    payload = _models_payload()
    models = payload["models"]
    assert isinstance(models, list)
    models[0][field] = invalid
    client = FlyATestServiceClient("https://server.example", "secret")
    client.session.request = MagicMock(return_value=_response(payload))

    with pytest.raises(FlyATestServiceError, match="invalid response"):
        client.models()


def test_quota_preserves_validated_decimal_strings() -> None:
    client = FlyATestServiceClient("https://server.example", "secret")
    client.session.request = MagicMock(return_value=_response(_paygo_quota_payload()))

    assert client.quota() == {
        "key_kind": "paygo",
        "status": "active",
        "expires_at": "2026-09-02T01:23:52Z",
        "destroy_at": None,
        "total": "1000.000",
        "used": "1.250",
        "remaining": "998.750",
    }
    assert client.session.request.call_args.args[1] == "https://server.example/beta/v1/quota"


def test_quota_supports_subscription_windows_and_negative_remaining() -> None:
    client = FlyATestServiceClient("https://server.example", "secret")
    client.session.request = MagicMock(
        return_value=_response(
            {
                "status": "grace",
                "key_kind": "subscription",
                "expires_at": "2026-08-08T23:11:28.3491Z",
                "destroy_at": "2026-08-11T23:11:28.3491Z",
                "five_hour": {
                    "limit": "2.000",
                    "used": "4.000",
                    "remaining": "-2.000",
                    "resets_at": "2026-08-02T04:11:28.3491Z",
                },
                "weekly": {
                    "limit": "4.000",
                    "used": "4.000",
                    "remaining": "0.000",
                    "resets_at": "2026-08-08T23:11:28.3491Z",
                },
                "consumed": "0.000",
                "replay": False,
            }
        )
    )

    assert client.quota() == {
        "key_kind": "subscription",
        "status": "grace",
        "expires_at": "2026-08-08T23:11:28.3491Z",
        "destroy_at": "2026-08-11T23:11:28.3491Z",
        "five_hour": {
            "limit": "2.000",
            "used": "4.000",
            "remaining": "-2.000",
            "resets_at": "2026-08-02T04:11:28.3491Z",
        },
        "weekly": {
            "limit": "4.000",
            "used": "4.000",
            "remaining": "0.000",
            "resets_at": "2026-08-08T23:11:28.3491Z",
        },
    }


@pytest.mark.parametrize("invalid", [1.5, "NaN", "1.0000", "-1.000"])
def test_quota_rejects_non_contract_decimal_values(invalid: object) -> None:
    client = FlyATestServiceClient("https://server.example", "secret")
    client.session.request = MagicMock(return_value=_response(_paygo_quota_payload(quota_total=invalid)))

    with pytest.raises(FlyATestServiceError, match="invalid response"):
        client.quota()


def test_models_activates_a_frozen_key_once_then_retries() -> None:
    client = FlyATestServiceClient("https://server.example", "secret")
    client.session.request = MagicMock(
        side_effect=[
            _response({"error": "test_api_key_frozen"}, status=403),
            _response(_paygo_quota_payload(activated_now=True)),
            _response(_models_payload()),
        ]
    )

    assert len(client.models()) == 2
    assert [call.args[:2] for call in client.session.request.call_args_list] == [
        ("GET", "https://server.example/beta/v1/models"),
        ("GET", "https://server.example/beta/v1/quota"),
        ("GET", "https://server.example/beta/v1/models"),
    ]


def test_health_adapts_model_availability() -> None:
    client = FlyATestServiceClient("https://server.example", "secret")
    client.session.request = MagicMock(return_value=_response(_models_payload()))

    assert client.health() == {
        "status": "degraded",
        "models": ["flya-heyman-2.1", "flya-heyman-2-3p"],
        "queue_depth": {},
    }


def test_ca_file_explicitly_or_environment_overrides_system_ca(monkeypatch, tmp_path) -> None:
    environment_ca = tmp_path / "environment.pem"
    environment_ca.write_text("environment")
    explicit_ca = tmp_path / "explicit.pem"
    explicit_ca.write_text("explicit")
    monkeypatch.setenv("FLYA_TEST_CA_FILE", str(environment_ca))

    from_environment = FlyATestServiceClient("https://server.example", "secret")
    from_environment.session.request = MagicMock(return_value=_response({"models": []}))
    from_environment.models()
    assert from_environment.session.request.call_args.kwargs["verify"] == str(environment_ca)

    explicit = FlyATestServiceClient("https://server.example", "secret", ca_file=explicit_ca)
    explicit.session.request = MagicMock(return_value=_response({"models": []}))
    explicit.models()
    assert explicit.session.request.call_args.kwargs["verify"] == str(explicit_ca)


def test_self_signed_test_server_requires_explicit_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("FLYA_TEST_INSECURE", "1")
    client = FlyATestServiceClient("https://server.example", "secret")
    client.session.request = MagicMock(return_value=_response({"models": []}))

    client.models()

    assert client.session.request.call_args.kwargs["verify"] is False


def test_missing_ca_file_is_clear_without_echoing_the_path(tmp_path) -> None:
    missing = tmp_path / "private-ca-name.pem"

    with pytest.raises(ValueError, match="CA file does not exist") as exc_info:
        FlyATestServiceClient("https://server.example", "secret", ca_file=missing)

    assert str(missing) not in str(exc_info.value)


def test_redirect_and_response_errors_are_sanitized() -> None:
    key = "never-expose-this-key"
    body = "never-expose-this-body"
    client = FlyATestServiceClient("https://private-server.example", key)
    client.session.request = MagicMock(return_value=_response({"error": body}, status=302))

    with pytest.raises(FlyATestServiceError) as exc_info:
        client.models()

    message = str(exc_info.value)
    assert "HTTP 302" in message
    assert "private-server.example" not in message
    assert key not in message
    assert body not in message
    assert client.session.request.call_args.kwargs["allow_redirects"] is False


def test_network_errors_are_sanitized() -> None:
    client = FlyATestServiceClient("https://private-server.example", "never-expose-this-key")
    client.session.request = MagicMock(
        side_effect=requests.ConnectionError(
            "https://private-server.example never-expose-this-key never-expose-this-body"
        )
    )

    with pytest.raises(FlyATestServiceError) as exc_info:
        client.quota()

    message = str(exc_info.value)
    assert "private-server.example" not in message
    assert "never-expose" not in message


def test_management_http_log_records_transport_error_without_api_key(monkeypatch) -> None:
    info = MagicMock()
    warning = MagicMock()
    monkeypatch.setattr("akagi_ng.mjai_bot.flya_service.logger.info", info)
    monkeypatch.setattr("akagi_ng.mjai_bot.flya_service.logger.warning", warning)
    key = "never-log-this-key"
    client = FlyATestServiceClient("https://server.example", key)
    client.session.request = MagicMock(side_effect=requests.ConnectTimeout(f"connect failed {key}"))

    with pytest.raises(FlyATestServiceError):
        client.models()

    joined = "\n".join(
        str(call.args[0]) for call in [*info.call_args_list, *warning.call_args_list]
    )
    assert "FlyA HTTP request operation=model-list" in joined
    assert "error_type=ConnectTimeout" in joined
    assert "<redacted>" in joined
    assert key not in joined
