from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import stat
import sys
import uuid
import zipfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit

import requests

from akagi_ng.plugins.base import AkagiPlugin

PLUGIN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
PLUGIN_VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_NAME = "akagi-plugin.json"
MANIFEST_SCHEMA_VERSION = 1
CATALOG_SCHEMA_VERSION = 1
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_FILES = 4096
MAX_MANIFEST_BYTES = 64 * 1024
DEFAULT_CATALOG_URL = "https://raw.githubusercontent.com/FanhuaAwA/Akagi-NG-Plugins/main/index.json"


class PluginPackageError(ValueError):
    pass


class DuplicatePluginError(PluginPackageError):
    pass


@dataclass(frozen=True, slots=True)
class PluginManifest:
    id: str
    version: str
    entrypoint: str
    conflicts: tuple[str, ...]
    schema_version: int = MANIFEST_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    id: str
    name: str
    version: str
    description: str
    author: str
    homepage: str
    download_url: str
    sha256: str
    requires_mitm: bool
    conflicts: tuple[str, ...]

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "homepage": self.homepage,
            "download_url": self.download_url,
            "sha256": self.sha256,
            "requires_mitm": self.requires_mitm,
            "conflicts": list(self.conflicts),
        }


def _validated_plugin_id(value: object, *, field: str = "id") -> str:
    if not isinstance(value, str) or not PLUGIN_ID_PATTERN.fullmatch(value):
        raise PluginPackageError(f"Invalid plugin {field}")
    return value


def _validated_version(value: object) -> str:
    if not isinstance(value, str) or not PLUGIN_VERSION_PATTERN.fullmatch(value):
        raise PluginPackageError("Invalid plugin version")
    return value


def _validated_conflicts(value: object, *, plugin_id: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise PluginPackageError("conflicts must be a list")
    conflicts: list[str] = []
    for item in value:
        conflict = _validated_plugin_id(item, field="conflict id")
        if conflict == plugin_id:
            raise PluginPackageError("A plugin cannot conflict with itself")
        if conflict not in conflicts:
            conflicts.append(conflict)
    return tuple(conflicts)


def parse_manifest(payload: object) -> PluginManifest:
    if not isinstance(payload, dict):
        raise PluginPackageError("Plugin manifest must be a JSON object")
    schema_version = payload.get("schema_version")
    if schema_version != MANIFEST_SCHEMA_VERSION:
        raise PluginPackageError(f"Unsupported plugin manifest schema: {schema_version}")
    plugin_id = _validated_plugin_id(payload.get("id"))
    version = _validated_version(payload.get("version"))
    entrypoint = payload.get("entrypoint")
    if not isinstance(entrypoint, str) or entrypoint.count(":") != 1:
        raise PluginPackageError("entrypoint must use relative/path.py:factory format")
    relative_file, symbol = entrypoint.split(":", 1)
    entry_path = PurePosixPath(relative_file)
    if entry_path.is_absolute() or ".." in entry_path.parts or entry_path.suffix != ".py" or not symbol.isidentifier():
        raise PluginPackageError("Invalid plugin entrypoint")
    conflicts = _validated_conflicts(payload.get("conflicts", []), plugin_id=plugin_id)
    return PluginManifest(
        id=plugin_id,
        version=version,
        entrypoint=entrypoint,
        conflicts=conflicts,
        schema_version=schema_version,
    )


def load_manifest(path: Path) -> PluginManifest:
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_MANIFEST_BYTES:
            raise PluginPackageError("Plugin manifest is too large")
        return parse_manifest(json.loads(raw.decode("utf-8-sig")))
    except PluginPackageError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PluginPackageError(f"Unable to read plugin manifest: {exc}") from exc


def _validated_zip_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    if len(members) > MAX_ARCHIVE_FILES:
        raise PluginPackageError("Plugin archive contains too many files")
    total_size = 0
    for member in members:
        if member.flag_bits & 0x1:
            raise PluginPackageError("Encrypted plugin archives are not supported")
        name = member.filename
        path = PurePosixPath(name)
        if not name or "\\" in name or path.is_absolute() or ".." in path.parts:
            raise PluginPackageError(f"Unsafe archive path: {name!r}")
        mode = member.external_attr >> 16
        if mode and stat.S_ISLNK(mode):
            raise PluginPackageError(f"Plugin archive may not contain symlinks: {name}")
        if not member.is_dir():
            total_size += member.file_size
            if total_size > MAX_ARCHIVE_BYTES:
                raise PluginPackageError("Plugin archive expands beyond the size limit")
    return members


def read_archive_manifest(archive_path: Path) -> PluginManifest:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            _validated_zip_members(archive)
            try:
                info = archive.getinfo(MANIFEST_NAME)
            except KeyError as exc:
                raise PluginPackageError(f"Plugin archive is missing {MANIFEST_NAME}") from exc
            if info.file_size > MAX_MANIFEST_BYTES:
                raise PluginPackageError("Plugin manifest is too large")
            return parse_manifest(json.loads(archive.read(info).decode("utf-8-sig")))
    except PluginPackageError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise PluginPackageError(f"Invalid plugin archive: {exc}") from exc


def extract_archive_to_staging(archive_path: Path, plugins_root: Path) -> tuple[PluginManifest, Path]:
    manifest = read_archive_manifest(archive_path)
    staging = plugins_root / f".install-{manifest.id}-{uuid.uuid4().hex}"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for member in _validated_zip_members(archive):
                relative = PurePosixPath(member.filename)
                target = staging.joinpath(*relative.parts)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("wb") as destination:
                    while chunk := source.read(1024 * 1024):
                        destination.write(chunk)
        entry_file = manifest.entrypoint.split(":", 1)[0]
        if not staging.joinpath(*PurePosixPath(entry_file).parts).is_file():
            raise PluginPackageError(f"Plugin entrypoint does not exist: {entry_file}")
        return manifest, staging
    except Exception:
        import shutil

        shutil.rmtree(staging, ignore_errors=True)
        raise


def _purge_plugin_modules(plugin_root: Path) -> None:
    for loaded_name, loaded_module in list(sys.modules.items()):
        loaded_file = getattr(loaded_module, "__file__", None)
        if not loaded_file:
            continue
        try:
            resolved = Path(loaded_file).resolve()
        except OSError:
            continue
        if resolved == plugin_root or plugin_root in resolved.parents:
            sys.modules.pop(loaded_name, None)


def _execute_entrypoint(
    plugin_dir: Path,
    manifest: PluginManifest,
    entry_file: Path,
    symbol: str,
) -> tuple[Any, ModuleType]:
    digest = hashlib.sha256(f"{plugin_dir}:{manifest.version}".encode()).hexdigest()[:16]
    module_name = f"_akagi_external_{manifest.id.replace('-', '_').replace('.', '_')}_{digest}"
    spec = importlib.util.spec_from_file_location(module_name, entry_file)
    if spec is None or spec.loader is None:
        raise PluginPackageError("Unable to create plugin module loader")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    sys.path.insert(0, str(plugin_dir))
    try:
        spec.loader.exec_module(module)
        factory = getattr(module, symbol, None)
        if factory is None or not callable(factory):
            raise PluginPackageError(f"Plugin entrypoint symbol is not callable: {symbol}")
        plugin = factory()
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    finally:
        with suppress(ValueError):
            sys.path.remove(str(plugin_dir))
    return plugin, module


def _validate_loaded_plugin(plugin: object, manifest: PluginManifest) -> AkagiPlugin:
    if not isinstance(plugin, AkagiPlugin):
        raise PluginPackageError("Plugin entrypoint did not return an AkagiPlugin")
    if plugin.metadata.id != manifest.id:
        raise PluginPackageError("Plugin metadata id does not match its package manifest")
    if plugin.metadata.version != manifest.version:
        raise PluginPackageError("Plugin metadata version does not match its package manifest")
    if set(plugin.metadata.conflicts) != set(manifest.conflicts):
        raise PluginPackageError("Plugin conflict metadata does not match its package manifest")
    return plugin


def load_installed_plugin(plugin_dir: Path, manifest: PluginManifest | None = None) -> tuple[AkagiPlugin, ModuleType]:
    manifest = manifest or load_manifest(plugin_dir / MANIFEST_NAME)
    if plugin_dir.name != manifest.id:
        raise PluginPackageError(f"Plugin directory {plugin_dir.name!r} does not match manifest id {manifest.id!r}")
    relative_file, symbol = manifest.entrypoint.split(":", 1)
    entry_file = plugin_dir.joinpath(*PurePosixPath(relative_file).parts)
    if not entry_file.is_file():
        raise PluginPackageError(f"Plugin entrypoint does not exist: {relative_file}")

    _purge_plugin_modules(plugin_dir.resolve())
    plugin, module = _execute_entrypoint(plugin_dir, manifest, entry_file, symbol)
    return _validate_loaded_plugin(plugin, manifest), module


def _catalog_text_fields(raw: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for field in ("name", "description", "author", "homepage"):
        value = raw.get(field, "")
        if not isinstance(value, str):
            raise PluginPackageError(f"Catalog field {field} must be a string")
        result[field] = value
    return result


def _parse_catalog_entry(raw: object) -> CatalogEntry:
    if not isinstance(raw, dict):
        raise PluginPackageError("Catalog entry must be an object")
    plugin_id = _validated_plugin_id(raw.get("id"))
    version = _validated_version(raw.get("version"))
    download_url = raw.get("download_url")
    parsed_url = urlsplit(download_url) if isinstance(download_url, str) else None
    if parsed_url is None or parsed_url.scheme != "https" or not parsed_url.hostname or parsed_url.username:
        raise PluginPackageError(f"Invalid download URL for plugin {plugin_id}")
    sha256 = raw.get("sha256")
    if not isinstance(sha256, str) or not SHA256_PATTERN.fullmatch(sha256.lower()):
        raise PluginPackageError(f"Invalid SHA-256 for plugin {plugin_id}")
    requires_mitm = raw.get("requires_mitm")
    if not isinstance(requires_mitm, bool):
        raise PluginPackageError("requires_mitm must be a boolean")
    text_fields = _catalog_text_fields(raw)
    return CatalogEntry(
        id=plugin_id,
        name=text_fields["name"],
        version=version,
        description=text_fields["description"],
        author=text_fields["author"],
        homepage=text_fields["homepage"],
        download_url=download_url,
        sha256=sha256.lower(),
        requires_mitm=requires_mitm,
        conflicts=_validated_conflicts(raw.get("conflicts", []), plugin_id=plugin_id),
    )


def parse_catalog(payload: object) -> list[CatalogEntry]:
    if not isinstance(payload, dict) or payload.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise PluginPackageError("Unsupported plugin catalog schema")
    raw_plugins = payload.get("plugins")
    if not isinstance(raw_plugins, list):
        raise PluginPackageError("Catalog plugins must be a list")
    entries = [_parse_catalog_entry(raw) for raw in raw_plugins]
    ids = [entry.id for entry in entries]
    if len(ids) != len(set(ids)):
        raise DuplicatePluginError("Duplicate plugin id in catalog")
    return entries


def fetch_catalog(url: str, *, timeout: float = 12.0) -> list[CatalogEntry]:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise PluginPackageError("Plugin catalog URL must be an HTTPS URL without credentials")
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        if len(response.content) > 2 * 1024 * 1024:
            raise PluginPackageError("Plugin catalog is too large")
        return parse_catalog(response.json())
    except PluginPackageError:
        raise
    except (requests.RequestException, ValueError) as exc:
        raise PluginPackageError(f"Unable to fetch plugin catalog: {exc}") from exc


def download_plugin(entry: CatalogEntry, destination: Path, *, timeout: float = 30.0) -> None:
    digest = hashlib.sha256()
    total = 0
    try:
        with requests.get(entry.download_url, timeout=timeout, stream=True) as response:
            response.raise_for_status()
            with destination.open("wb") as output:
                for chunk in response.iter_content(1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_ARCHIVE_BYTES:
                        raise PluginPackageError("Downloaded plugin exceeds the size limit")
                    digest.update(chunk)
                    output.write(chunk)
    except PluginPackageError:
        raise
    except (OSError, requests.RequestException) as exc:
        raise PluginPackageError(f"Unable to download plugin: {exc}") from exc
    if digest.hexdigest() != entry.sha256:
        raise PluginPackageError("Downloaded plugin SHA-256 does not match the catalog")
