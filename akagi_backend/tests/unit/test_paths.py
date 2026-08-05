from pathlib import Path

import pytest

from akagi_ng.core import paths


@pytest.fixture(autouse=True)
def clear_user_data_cache():
    paths.get_user_data_root.cache_clear()
    yield
    paths.get_user_data_root.cache_clear()


def test_user_data_root_migrates_legacy_config_and_keeps_assets_read_only(tmp_path, monkeypatch):
    bundle_root = tmp_path / "bundle"
    user_root = tmp_path / "user-data"
    legacy_config = bundle_root / "config"
    legacy_config.mkdir(parents=True)
    (legacy_config / "settings.json").write_text('{"locale":"zh-CN"}', encoding="utf-8")
    assets = bundle_root / "assets"
    assets.mkdir()
    (assets / "liqi.json").write_text('{"source":"bundle"}', encoding="utf-8")

    monkeypatch.setenv("AKAGI_USER_DATA_DIR", str(user_root))
    monkeypatch.setattr(paths, "get_app_root", lambda: bundle_root)
    assert paths.get_settings_dir() == user_root / "config"
    assert (user_root / "config" / "settings.json").read_text(encoding="utf-8") == '{"locale":"zh-CN"}'
    assert paths.get_logs_dir() == user_root / "logs"
    assert paths.get_liqi_path() == assets / "liqi.json"

    override = user_root / "config" / "liqi.json"
    override.write_text('{"source":"user"}', encoding="utf-8")
    assert paths.get_liqi_path() == override


def test_user_data_root_rejects_relative_environment_path(monkeypatch):
    monkeypatch.setenv("AKAGI_USER_DATA_DIR", "relative/path")
    with pytest.raises(ValueError, match="must be an absolute path"):
        paths.get_user_data_root()


def test_user_data_root_defaults_to_bundle_for_standalone_backend(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("AKAGI_USER_DATA_DIR", raising=False)
    monkeypatch.setattr(paths, "get_app_root", lambda: tmp_path)
    assert paths.get_user_data_root() == tmp_path
