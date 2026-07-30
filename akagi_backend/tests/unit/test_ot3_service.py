from unittest.mock import MagicMock

import pytest
import requests

from akagi_ng.mjai_bot.ot3_service import OT3ServiceClient, OT3ServiceError


def _response(payload: object, *, status: int = 200) -> MagicMock:
    response = MagicMock()
    response.ok = 200 <= status < 300
    response.status_code = status
    response.json.return_value = payload
    return response


def test_management_endpoints_and_bearer_header() -> None:
    client = OT3ServiceClient("http://server.example/", "secret")
    client.session.request = MagicMock(
        side_effect=[
            _response({"status": "ok", "models": ["4p-ot3"], "queue_depth": {"4p-ot3": 1}}),
            _response(
                {
                    "plan": "pro",
                    "expires_at": "2026-09-01",
                    "usage_today": 3,
                    "rpd": 6000,
                    "rpm": 10.0,
                    "topk": 3,
                }
            ),
            _response(
                {
                    "models": [
                        {"id": "4p-ot3", "game": "4p", "desc": "OT3 four-player"},
                        {"id": "4p-fast", "game": "4p", "desc": "Fast four-player"},
                        {"id": "3p-ot3", "game": "3p", "desc": "OT3 three-player"},
                    ]
                }
            ),
        ]
    )

    assert client.health()["queue_depth"]["4p-ot3"] == 1
    assert client.key_status()["rpm"] == 10.0
    models = client.models()
    assert [model["id"] for model in models] == ["4p-ot3", "4p-fast", "3p-ot3"]
    assert [model["game"] for model in models] == ["4p", "4p", "3p"]

    calls = client.session.request.call_args_list
    assert calls[0].args[:2] == ("GET", "http://server.example/healthz")
    assert calls[0].kwargs["headers"] == {}
    assert calls[1].args[:2] == ("GET", "http://server.example/v3/key")
    assert calls[1].kwargs["headers"]["Authorization"] == "Bearer secret"
    assert calls[2].args[:2] == ("GET", "http://server.example/v3/models")


def test_management_client_applies_proxy_to_every_endpoint() -> None:
    client = OT3ServiceClient("https://server.example", "key", "http://127.0.0.1:7890")

    assert client.session.trust_env is False
    assert client.session.proxies == {
        "http": "http://127.0.0.1:7890",
        "https": "http://127.0.0.1:7890",
    }


@pytest.mark.parametrize(
    ("proxy", "secret_part"),
    [
        ("ftp://127.0.0.1:21", "127.0.0.1:21"),
        ("socks5://", "socks5://"),
        ("http://127.0.0.1:bad", "127.0.0.1:bad"),
        ("https://127.0.0.1:7890?leak=1", "leak=1"),
    ],
)
def test_invalid_proxy_is_rejected_without_echoing_sensitive_parts(proxy: str, secret_part: str) -> None:
    with pytest.raises(ValueError) as exc_info:
        OT3ServiceClient("https://server.example", "key", proxy)
    if proxy != "socks5://":
        assert secret_part not in str(exc_info.value)


def test_redeem_omits_blank_optional_fields() -> None:
    client = OT3ServiceClient("https://server.example")
    client.session.request = MagicMock(
        return_value=_response(
            {
                "key": "new-key",
                "key_last4": "-key",
                "plan": "pro",
                "expires_at": "2026-09-01",
                "extended": False,
            }
        )
    )

    result = client.redeem(" code ", email=" ", renew_key="")

    assert result["key"] == "new-key"
    assert client.session.request.call_args.kwargs["json"] == {"code": "code"}


def test_purchase_contract_and_safe_paypal_url() -> None:
    client = OT3ServiceClient("https://server.example")
    client.session.request = MagicMock(
        return_value=_response(
            {
                "order_id": "OID",
                "approve_url": "https://www.paypal.com/checkoutnow?token=OID",
                "claim_secret": "claim",
            }
        )
    )

    created = client.create_order("pro-30", redeem=True)

    assert created["order_id"] == "OID"
    call = client.session.request.call_args
    assert call.args[:2] == ("POST", "https://server.example/paypal/create-order")
    assert call.kwargs["json"] == {"product": "pro-30", "redeem": True}


def test_purchase_rejects_unknown_product_and_unsafe_approval_url() -> None:
    client = OT3ServiceClient("https://server.example")
    with pytest.raises(ValueError, match="Unknown"):
        client.create_order("custom-price", redeem=True)

    client.session.request = MagicMock(
        return_value=_response(
            {
                "order_id": "OID",
                "approve_url": "https://evil.example/steal",
                "claim_secret": "claim",
            }
        )
    )
    with pytest.raises(OT3ServiceError, match="unsafe"):
        client.create_order("pro-30", redeem=True)


def test_errors_never_expose_key_or_server_response_body() -> None:
    client = OT3ServiceClient("https://server.example", "never-expose-this")
    client.session.request = MagicMock(return_value=_response({"error": "never-expose-this"}, status=401))

    with pytest.raises(OT3ServiceError) as exc_info:
        client.key_status()

    message = str(exc_info.value)
    assert "never-expose-this" not in message
    assert "HTTP 401" in message


def test_cloudflare_403_is_identified_as_an_edge_block() -> None:
    client = OT3ServiceClient("https://server.example", "secret")
    response = _response("<html>blocked</html>", status=403)
    response.headers = {"server": "cloudflare", "cf-ray": "example-HKG"}
    client.session.request = MagicMock(return_value=response)

    with pytest.raises(OT3ServiceError) as exc_info:
        client.key_status()

    message = str(exc_info.value)
    assert "blocked by Cloudflare before reaching OT3" in message
    assert "not an API-key validation result" in message


def test_network_error_is_sanitized() -> None:
    client = OT3ServiceClient("https://server.example", "secret")
    client.session.request = MagicMock(side_effect=requests.ConnectionError("secret transport detail"))

    with pytest.raises(OT3ServiceError) as exc_info:
        client.models()

    assert "secret" not in str(exc_info.value)
