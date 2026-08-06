from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import unquote

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed448, ed25519, rsa

if TYPE_CHECKING:
    from mitmproxy.certs import Cert
    from mitmproxy.http import HTTPFlow

CERTIFICATE_CATEGORY = "certificate_info"
REPLACED_FIELDS = (
    "issuer",
    "version",
    "oid_value",
    "oid_friendly_name",
    "thumbprint",
    "serial_number",
    "not_before",
    "not_after",
    "subject",
)


@dataclass(frozen=True, slots=True)
class ObservedCertificate:
    issuer: str
    version: int
    oid_value: str
    oid_friendly_name: str
    thumbprint: str
    serial_number: str
    not_before: str
    not_after: str
    subject: str

    @classmethod
    def from_mitmproxy(cls, certificate: Cert) -> ObservedCertificate:
        cert = certificate.to_cryptography()
        oid_value, friendly_name = _public_key_algorithm(cert)
        serial = f"{cert.serial_number:X}"
        if len(serial) % 2:
            serial = f"0{serial}"
        return cls(
            issuer=_windows_distinguished_name(cert.issuer),
            version=cert.version.value + 1,
            oid_value=oid_value,
            oid_friendly_name=friendly_name,
            # Mahjong Soul reports the Windows certificate thumbprint, which is SHA-1 by definition.
            thumbprint=cert.fingerprint(hashes.SHA1()).hex().upper(),
            serial_number=serial,
            not_before=_format_client_datetime(_not_valid_before(cert)),
            not_after=_format_client_datetime(_not_valid_after(cert)),
            subject=_windows_distinguished_name(cert.subject),
        )


@dataclass(frozen=True, slots=True)
class RewriteResult:
    path: str
    corrected: int
    uncorrected: int


class CertificateStore:
    def __init__(self) -> None:
        self._certificates: dict[str, ObservedCertificate] = {}
        self._lock = threading.Lock()

    def record(self, host: str, certificate: Cert) -> None:
        normalized = host.strip().lower().rstrip(".")
        if not normalized:
            return
        observed = ObservedCertificate.from_mitmproxy(certificate)
        with self._lock:
            self._certificates[normalized] = observed

    def get(self, host: str) -> ObservedCertificate | None:
        with self._lock:
            return self._certificates.get(host.strip().lower().rstrip("."))


def record_upstream_certificate(flow: HTTPFlow, store: CertificateStore) -> bool:
    certificates = tuple(flow.server_conn.certificate_list or ())
    if not certificates:
        return False
    hosts = {
        flow.request.host,
        flow.request.pretty_host,
        flow.server_conn.sni or "",
    }
    for host in hosts:
        if host:
            store.record(host, certificates[0])
    return True


def rewrite_certificate_report(path: str, store: CertificateStore) -> RewriteResult | None:
    if "?" not in path:
        return None
    raw_path, query = path.split("?", 1)
    category = _decoded_query_parameter(query, "log_category")
    if category != CERTIFICATE_CATEGORY:
        return None
    content = _decoded_query_parameter(query, "content")
    if content is None:
        return None

    corrected_content = _correct_content(content, store)
    if corrected_content is None:
        return None
    replacement, corrected, uncorrected = corrected_content
    rewritten_query = _splice_query_parameter(query, "content", _escape_uri_component(replacement))
    if rewritten_query is None:
        return None
    return RewriteResult(path=f"{raw_path}?{rewritten_query}", corrected=corrected, uncorrected=uncorrected)


def _correct_content(content: str, store: CertificateStore) -> tuple[str, int, int] | None:
    spans = _array_element_spans(content)
    if spans is None:
        return None
    out: list[str] = []
    cursor = 0
    corrected = 0
    uncorrected = 0
    for start, end in spans:
        out.append(content[cursor:start])
        element = content[start:end]
        fixed = _correct_element(element, store)
        if fixed is None:
            uncorrected += 1
            out.append(element)
        else:
            corrected += 1
            out.append(fixed)
        cursor = end
    out.append(content[cursor:])
    if corrected == 0:
        return None
    return "".join(out), corrected, uncorrected


def _correct_element(element: str, store: CertificateStore) -> str | None:
    try:
        parsed = json.loads(element)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict) or not all(field in parsed for field in REPLACED_FIELDS):
        return None
    url = parsed.get("url")
    if not isinstance(url, str):
        return None
    host = _host_of(url)
    certificate = store.get(host) if host else None
    if certificate is None:
        return None

    spans = _object_value_spans(element)
    if spans is None or not all(field in spans for field in REPLACED_FIELDS):
        return None
    replacements: dict[str, str | int] = {
        "issuer": certificate.issuer,
        "version": certificate.version,
        "oid_value": certificate.oid_value,
        "oid_friendly_name": certificate.oid_friendly_name,
        "thumbprint": certificate.thumbprint,
        "serial_number": certificate.serial_number,
        "not_before": certificate.not_before,
        "not_after": certificate.not_after,
        "subject": certificate.subject,
    }
    edits = sorted(((spans[field], _json_token(value)) for field, value in replacements.items()), reverse=True)
    fixed = element
    for (start, end), value in edits:
        fixed = f"{fixed[:start]}{value}{fixed[end:]}"
    return fixed


def _array_element_spans(content: str) -> list[tuple[int, int]] | None:  # noqa: PLR0911
    decoder = json.JSONDecoder()
    index = _skip_whitespace(content, 0)
    if index >= len(content) or content[index] != "[":
        return None
    index += 1
    spans: list[tuple[int, int]] = []
    while True:
        index = _skip_whitespace(content, index)
        if index >= len(content):
            return None
        if content[index] == "]":
            return spans if not content[index + 1 :].strip() else None
        start = index
        try:
            _, end = decoder.raw_decode(content, index)
        except json.JSONDecodeError:
            return None
        spans.append((start, end))
        index = _skip_whitespace(content, end)
        if index >= len(content):
            return None
        if content[index] == ",":
            index += 1
            continue
        if content[index] == "]":
            return spans if not content[index + 1 :].strip() else None
        return None


def _object_value_spans(value: str) -> dict[str, tuple[int, int]] | None:  # noqa: C901, PLR0911
    decoder = json.JSONDecoder()
    index = _skip_whitespace(value, 0)
    if index >= len(value) or value[index] != "{":
        return None
    index += 1
    spans: dict[str, tuple[int, int]] = {}
    while True:
        index = _skip_whitespace(value, index)
        if index >= len(value):
            return None
        if value[index] == "}":
            return spans
        try:
            key, index = decoder.raw_decode(value, index)
        except json.JSONDecodeError:
            return None
        if not isinstance(key, str):
            return None
        index = _skip_whitespace(value, index)
        if index >= len(value) or value[index] != ":":
            return None
        start = _skip_whitespace(value, index + 1)
        try:
            _, end = decoder.raw_decode(value, start)
        except json.JSONDecodeError:
            return None
        spans[key] = (start, end)
        index = _skip_whitespace(value, end)
        if index >= len(value):
            return None
        if value[index] == ",":
            index += 1
            continue
        if value[index] == "}":
            return spans
        return None


def _decoded_query_parameter(query: str, name: str) -> str | None:
    prefix = f"{name}="
    for part in query.split("&"):
        if part.startswith(prefix):
            return unquote(part[len(prefix) :], errors="replace")
    return None


def _splice_query_parameter(query: str, name: str, encoded_value: str) -> str | None:
    prefix = f"{name}="
    cursor = 0
    for part in query.split("&"):
        if part.startswith(prefix):
            start = cursor + len(prefix)
            end = cursor + len(part)
            return f"{query[:start]}{encoded_value}{query[end:]}"
        cursor += len(part) + 1
    return None


def _escape_uri_component(value: str) -> str:
    safe = "-_.!~*'();/?:@=$,[]"
    out: list[str] = []
    for byte in value.encode():
        character = chr(byte)
        if character.isascii() and (character.isalnum() or character in safe):
            out.append(character)
        else:
            out.append(f"%{byte:02X}")
    return "".join(out)


def _json_token(value: str | int) -> str:
    if isinstance(value, int):
        return str(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("/", r"\/")


def _host_of(url: str) -> str | None:
    if "://" not in url:
        return None
    authority = url.split("://", 1)[1].split("/", 1)[0]
    host = authority.rsplit("@", 1)[-1].split(":", 1)[0].strip().lower()
    return host or None


def _skip_whitespace(value: str, index: int) -> int:
    while index < len(value) and value[index].isspace():
        index += 1
    return index


def _format_client_datetime(value: datetime) -> str:
    local = value.astimezone()
    hour = local.strftime("%I").lstrip("0") or "0"
    return f"{local.month}/{local.day}/{local.year} {hour}:{local:%M:%S} {local:%p}"


def _not_valid_before(cert: x509.Certificate) -> datetime:
    try:
        return cert.not_valid_before_utc
    except AttributeError:  # pragma: no cover - cryptography < 42
        return cert.not_valid_before


def _not_valid_after(cert: x509.Certificate) -> datetime:
    try:
        return cert.not_valid_after_utc
    except AttributeError:  # pragma: no cover - cryptography < 42
        return cert.not_valid_after


def _public_key_algorithm(cert: x509.Certificate) -> tuple[str, str]:
    key = cert.public_key()
    if isinstance(key, rsa.RSAPublicKey):
        return "1.2.840.113549.1.1.1", "RSA"
    if isinstance(key, ec.EllipticCurvePublicKey):
        return "1.2.840.10045.2.1", "ECC"
    if isinstance(key, dsa.DSAPublicKey):
        return "1.2.840.10040.4.1", "DSA"
    if isinstance(key, ed25519.Ed25519PublicKey):
        return "1.3.101.112", "Ed25519"
    if isinstance(key, ed448.Ed448PublicKey):
        return "1.3.101.113", "Ed448"
    return "", key.__class__.__name__.replace("PublicKey", "")


def _windows_distinguished_name(name: x509.Name) -> str:
    """Match the comma-space style emitted by .NET X509Certificate2."""
    value = name.rfc4514_string()
    out: list[str] = []
    backslashes = 0
    for character in value:
        if character == "\\":
            backslashes += 1
            out.append(character)
            continue
        if character == "," and backslashes % 2 == 0:
            out.append(", ")
        else:
            out.append(character)
        backslashes = 0
    return "".join(out)
