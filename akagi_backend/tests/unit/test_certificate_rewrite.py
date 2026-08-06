from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from mitmproxy.certs import Cert

from akagi_ng.mitm_client.certificate_rewrite import CertificateStore, rewrite_certificate_report


def _certificate() -> Cert:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Example Inc"),
            x509.NameAttribute(NameOID.COMMON_NAME, "Example TLS CA"),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "*.maj-soul.com")]))
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(0x07FEC9E77B8C0D52)
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=180))
        .sign(key, hashes.SHA256())
    )
    return Cert.from_pem(cert.public_bytes(serialization.Encoding.PEM))


def _entry(host: str) -> str:
    return (
        r'{"issuer":"O=Akagi-NG, CN=mitmproxy","version":3,'
        r'"oid_value":"1.2.840.10045.2.1","thumbprint":"DEADBEEF",'
        r'"serial_number":"1234","ip":["198.18.0.46:443"],'
        r'"oid_friendly_name":"ECC","url":"wss:\/\/'
        + host
        + r'\/gateway","not_before":"8\/1\/2026 1:00:00 AM",'
        r'"not_after":"8\/1\/2027 1:00:00 AM","subject":"CN='
        + host
        + r'"}'
    )


def _encode_client(value: str) -> str:
    safe = "-_.!~*'();/?:@=$,[]"
    return "".join(
        chr(byte) if chr(byte).isascii() and (chr(byte).isalnum() or chr(byte) in safe) else f"%{byte:02X}"
        for byte in value.encode()
    )


def test_certificate_report_uses_observed_upstream_certificate_without_reserializing() -> None:
    store = CertificateStore()
    store.record("route-6.maj-soul.com", _certificate())
    content = f"[{_entry('route-6.maj-soul.com')}]"
    query = (
        "APIVersion=0.6.0&log_category=certificate_info&"
        "device_model=System%20Product%20Name%20(ASUS)&content="
        f"{_encode_client(content)}&client_type=app"
    )

    result = rewrite_certificate_report(f"/logstores/client/track?{query}", store)

    assert result is not None
    assert (result.corrected, result.uncorrected) == (1, 0)
    rewritten_query = result.path.split("?", 1)[1]
    assert "device_model=System%20Product%20Name%20(ASUS)" in rewritten_query
    decoded = unquote(rewritten_query.split("content=", 1)[1].split("&", 1)[0])
    assert "Akagi-NG" not in decoded
    assert '"issuer":"CN=Example TLS CA, O=Example Inc, C=US"' in decoded
    assert '"oid_friendly_name":"RSA"' in decoded
    assert '"serial_number":"07FEC9E77B8C0D52"' in decoded
    assert r'"url":"wss:\/\/route-6.maj-soul.com\/gateway"' in decoded
    keys = list(json.loads(decoded)[0])
    assert keys == [
        "issuer",
        "version",
        "oid_value",
        "thumbprint",
        "serial_number",
        "ip",
        "oid_friendly_name",
        "url",
        "not_before",
        "not_after",
        "subject",
    ]


def test_unknown_certificate_entry_is_not_dropped() -> None:
    store = CertificateStore()
    store.record("route-6.maj-soul.com", _certificate())
    content = f"[{_entry('route-6.maj-soul.com')},{_entry('route-5.maj-soul.com')}]"
    path = (
        "/logstores/client/track?log_category=certificate_info&content="
        f"{_encode_client(content)}"
    )

    result = rewrite_certificate_report(path, store)

    assert result is not None
    assert (result.corrected, result.uncorrected) == (1, 1)
    decoded = unquote(result.path.split("content=", 1)[1])
    assert decoded.count('"issuer":') == 2
    assert "Akagi-NG" in decoded


def test_non_certificate_telemetry_is_byte_identical() -> None:
    path = "/track?log_category=login_stats&content=%7B%22use_time%22:2%7D"
    assert rewrite_certificate_report(path, CertificateStore()) is None
