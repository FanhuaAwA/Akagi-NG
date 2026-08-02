"""
测试模块：akagi_backend/tests/unit/test_settings.py

描述：针对配置管理 (Settings) 模块的单元测试。
主要测试点：
- 各配置子项 (OT, MITM, Server, Model) 的 Dataclass 默认值与创建。
- 配置文件的加载、JSON Schema 验证及非法配置的备份重置逻辑。
- 根据平台 URL 自动修正平台类型的逻辑。
- 系统区域语言 (Locale) 的自动检测逻辑 (Windows/Python fallback)。
"""

import sys
import unittest
from unittest.mock import mock_open, patch

import pytest

from akagi_ng.settings.settings import (
    DEFAULT_FLYA_SERVER,
    DEFAULT_OT3_SERVER,
    SETTINGS_JSON_PATH,
    MihomoConfig,
    MITMConfig,
    ModelConfig,
    OTConfig,
    ServerConfig,
    Settings,
    _backup_and_reset_settings,
    _detect_locale_python,
    _detect_locale_windows,
    _get_schema,
    _load_settings,
    detect_system_locale,
    get_default_settings_dict,
    verify_settings,
)


class TestSettingsDataclasses(unittest.TestCase):
    """测试 Settings 相关 dataclass 的基本功能"""

    def test_ot_config_defaults(self):
        config = OTConfig(online=False)
        self.assertFalse(config.online)
        self.assertEqual(config.server, DEFAULT_OT3_SERVER)
        self.assertEqual(config.flya_server, DEFAULT_FLYA_SERVER)
        self.assertEqual(config.api_key, "")
        self.assertEqual(config.protocol, "v3")
        self.assertEqual(config.model_for(False), "")
        self.assertEqual(config.model_for(True), "")
        self.assertFalse(config.proxy_enabled)
        self.assertEqual(config.effective_proxy(), "")

    def test_mitm_config_creation(self):
        config = MITMConfig(enabled=True, host="127.0.0.1", port=6789, upstream="")
        self.assertTrue(config.enabled)
        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 6789)

    def test_server_config_creation(self):
        config = ServerConfig(host="0.0.0.0", port=8080)
        self.assertEqual(config.host, "0.0.0.0")
        self.assertEqual(config.port, 8080)

    def test_model_config_creation(self):
        config = ModelConfig(
            temperature=0.7,
        )
        self.assertEqual(config.temperature, 0.7)


class TestSettingsClass(unittest.TestCase):
    """测试 Settings 类方法"""

    def setUp(self):
        self.settings = Settings(
            log_level="INFO",
            locale="zh-CN",
            game_url="https://game.maj-soul.com/1/",
            majsoul_server="cn",
            platform="majsoul",
            mitm=MITMConfig(enabled=False, host="127.0.0.1", port=6789, upstream=""),
            mihomo=MihomoConfig(),
            server=ServerConfig(host="127.0.0.1", port=8765),
            ot=OTConfig(online=False),
            model_config=ModelConfig(
                temperature=1.0,
            ),
        )

    def test_settings_creation(self):
        self.assertEqual(self.settings.log_level, "INFO")
        self.assertEqual(self.settings.locale, "zh-CN")

    def test_settings_direct_attribute_update(self):
        self.settings.log_level = "DEBUG"
        self.settings.locale = "en-US"
        self.assertEqual(self.settings.log_level, "DEBUG")
        self.assertEqual(self.settings.locale, "en-US")

    @patch("akagi_ng.settings.settings.delete_secret")
    def test_update_preserves_flya_key_when_public_state_is_stale(self, delete_secret_mock):
        self.settings.ot.flya_api_key = "existing-secret"
        data = self.settings.to_public_dict()
        data["ot"]["flya_api_key_configured"] = False

        self.settings.update(data)

        self.assertEqual(self.settings.ot.flya_api_key, "existing-secret")
        delete_secret_mock.assert_not_called()

    @patch("akagi_ng.settings.settings.delete_secret")
    def test_update_explicitly_clears_flya_key_for_settings_reset(self, delete_secret_mock):
        self.settings.ot.flya_api_key = "existing-secret"
        data = self.settings.to_public_dict()

        self.settings.update(data, clear_flya_api_key=True)

        self.assertEqual(self.settings.ot.flya_api_key, "")
        delete_secret_mock.assert_called_once()

    @patch("akagi_ng.settings.settings.delete_secret")
    def test_update_clears_flya_key_when_server_changes(self, delete_secret_mock):
        self.settings.ot.flya_api_key = "existing-secret"
        data = self.settings.to_public_dict()
        data["ot"]["flya_server"] = "https://other.example/beta/v1"

        self.settings.update(data)

        self.assertEqual(self.settings.ot.flya_api_key, "")
        delete_secret_mock.assert_called_once()

    def test_settings_from_dict(self):
        data = {
            "log_level": "TRACE",
            "locale": "ja-JP",
            "game_url": "https://game.maj-soul.com/1/",
            "majsoul_server": "cn",
            "platform": "majsoul",
            "mitm": {"enabled": True, "host": "0.0.0.0", "port": 7890, "upstream": ""},
            "server": {"host": "localhost", "port": 9000},
            "ot": {"online": True, "server": "https://api.test.com", "api_key": "abc123"},
            "model_config": {
                "temperature": 0.5,
                "model_4p": "mortal.pth",
                "model_3p": "mortal3p.pth",
            },
        }
        settings = Settings.from_dict(data)
        self.assertEqual(settings.log_level, "TRACE")
        self.assertEqual(settings.locale, "ja-JP")
        self.assertTrue(settings.mitm.enabled)

    def test_settings_game_url_validation(self):
        s = Settings.from_dict({"platform": "tenhou", "game_url": "https://maj-soul.com"})
        self.assertEqual(s.game_url, "https://tenhou.net/3/")

        s = Settings.from_dict({"platform": "majsoul", "majsoul_server": "jp", "game_url": "https://tenhou.net"})
        self.assertEqual(s.game_url, "https://game.mahjongsoul.com/")

        s = Settings.from_dict({"platform": "majsoul", "majsoul_server": "en"})
        self.assertEqual(s.game_url, "https://mahjongsoul.game.yo-star.com/")

        s = Settings.from_dict({"platform": "riichi_city", "game_url": "https://riichi.city/"})
        self.assertEqual(s.game_url, "")

    def test_settings_update(self):
        s = Settings.from_dict({})
        s.update({"log_level": "DEBUG", "mitm": {"enabled": True}})
        self.assertEqual(s.log_level, "DEBUG")
        self.assertTrue(s.mitm.enabled)


class TestSettingsLifecycle(unittest.TestCase):
    """测试 Settings 文件的生命周期（加载、验证、备份、错误处理）"""

    def test_get_default_settings_dict(self):
        defaults = get_default_settings_dict()
        self.assertIn("log_level", defaults)
        self.assertEqual(defaults["log_level"], "INFO")
        self.assertEqual(defaults["majsoul_server"], "cn")
        self.assertEqual(defaults["game_url"], "https://game.maj-soul.com/1/")
        self.assertFalse(defaults["mihomo"]["enabled"])
        self.assertEqual(defaults["desktop"]["overlay_mode"], "standard")
        self.assertTrue(defaults["desktop"]["capture_protection"])
        self.assertEqual(defaults["ot"]["server"], "https://mjapi.shinkuan.me")
        self.assertEqual(defaults["ot"]["flya_server"], DEFAULT_FLYA_SERVER)
        self.assertFalse(defaults["ot"]["proxy_enabled"])

    def test_missing_or_empty_flya_server_uses_default(self):
        self.assertEqual(Settings.from_dict({}).ot.flya_server, DEFAULT_FLYA_SERVER)
        self.assertEqual(Settings.from_dict({"ot": {"flya_server": ""}}).ot.flya_server, DEFAULT_FLYA_SERVER)

    def test_old_settings_without_mihomo_are_migrated_in_memory(self):
        legacy = get_default_settings_dict()
        legacy.pop("mihomo")
        self.assertTrue(verify_settings(legacy))
        settings = Settings.from_dict(legacy)
        self.assertFalse(settings.mihomo.enabled)
        self.assertEqual(settings.mihomo.mixed_port, 7890)

    def test_old_settings_without_desktop_are_migrated_in_memory(self):
        legacy = get_default_settings_dict()
        legacy.pop("desktop")
        self.assertTrue(verify_settings(legacy))
        settings = Settings.from_dict(legacy)
        self.assertEqual(settings.desktop.overlay_mode, "standard")
        self.assertTrue(settings.desktop.capture_protection)

    def test_deprecated_dashboard_privacy_settings_are_ignored(self):
        legacy = get_default_settings_dict()
        legacy["desktop"]["privacy_mode"] = True
        legacy["desktop"]["start_hidden"] = True
        legacy["desktop"]["restore_shortcut"] = "CommandOrControl+Shift+H"

        settings = Settings.from_dict(legacy)
        self.assertFalse(settings.desktop.privacy_mode)
        self.assertFalse(settings.desktop.start_hidden)
        self.assertEqual(settings.desktop.restore_shortcut, "CommandOrControl+Shift+A")

    def test_deprecated_advanced_overlay_settings_are_normalized(self):
        legacy = get_default_settings_dict()
        legacy["desktop"]["overlay_mode"] = "advanced"
        legacy["desktop"]["advanced_host"] = "discord"

        settings = Settings.from_dict(legacy)
        self.assertEqual(settings.desktop.overlay_mode, "standard")
        self.assertEqual(settings.desktop.advanced_host, "auto")

        settings.update(legacy)
        self.assertEqual(settings.desktop.overlay_mode, "standard")
        self.assertEqual(settings.desktop.advanced_host, "auto")

        settings.update(legacy)
        self.assertFalse(settings.desktop.privacy_mode)
        self.assertFalse(settings.desktop.start_hidden)
        self.assertEqual(settings.desktop.restore_shortcut, "CommandOrControl+Shift+A")

    def test_pre_ot3_online_settings_stay_on_legacy_protocol(self):
        settings = Settings.from_dict(
            {
                "ot": {
                    "online": True,
                    "server": "http://legacy.example",
                    "api_key": "legacy-key",
                }
            }
        )
        self.assertEqual(settings.ot.protocol, "legacy")

    def test_blocked_placeholder_server_is_migrated(self):
        settings = Settings.from_dict(
            {
                "ot": {
                    "online": True,
                    "server": "https://server.akagiot.org/",
                    "api_key": "key",
                    "protocol": "v3",
                }
            }
        )
        self.assertEqual(settings.ot.server, DEFAULT_OT3_SERVER)

    def test_ot3_proxy_validation(self):
        settings = get_default_settings_dict()
        settings["ot"]["proxy_enabled"] = True
        self.assertFalse(verify_settings(settings))

        settings["ot"]["proxy"] = "socks5h://127.0.0.1:7890"
        self.assertTrue(verify_settings(settings))

        settings["ot"]["proxy"] = "ftp://127.0.0.1:21"
        self.assertFalse(verify_settings(settings))

    def test_online_mode_can_be_saved_before_entering_api_key(self):
        settings = get_default_settings_dict()
        settings["ot"]["online"] = True
        self.assertTrue(verify_settings(settings))

    def test_mihomo_requires_mitm_and_unique_ports(self):
        invalid = get_default_settings_dict()
        invalid["mihomo"]["enabled"] = True
        self.assertFalse(verify_settings(invalid))

        invalid["mitm"]["enabled"] = True
        invalid["mihomo"]["mixed_port"] = invalid["mitm"]["port"]
        self.assertFalse(verify_settings(invalid))

        invalid["mihomo"]["mixed_port"] = 7890
        self.assertTrue(verify_settings(invalid))

    def test_verify_settings_valid(self):
        valid_data = get_default_settings_dict()
        self.assertTrue(verify_settings(valid_data))

    def test_verify_settings_invalid(self):
        invalid_data = get_default_settings_dict()
        invalid_data["log_level"] = "INVALID_LEVEL"
        self.assertFalse(verify_settings(invalid_data))

    def test_get_schema_file_not_found(self):
        with patch("akagi_ng.settings.settings.SCHEMA_PATH") as mock_path:
            mock_path.exists.return_value = False
            with self.assertRaises(FileNotFoundError):
                _get_schema()

    def test_backup_and_reset_settings(self):
        with (
            patch("akagi_ng.settings.settings.SETTINGS_JSON_PATH") as mock_path,
            patch("os.replace") as mock_replace,
            patch("builtins.open", mock_open()),
        ):
            mock_path.exists.return_value = True
            mock_path.with_suffix.return_value = SETTINGS_JSON_PATH.with_suffix(".json.bak")

            res = _backup_and_reset_settings("test reason")
            self.assertIn("locale", res)
            mock_replace.assert_called()

    def test_load_settings_corruption_path(self):
        with (
            patch("akagi_ng.settings.settings._get_schema", return_value={}),
            patch("akagi_ng.settings.settings.SETTINGS_JSON_PATH") as mock_path,
            patch("akagi_ng.settings.settings._backup_and_reset_settings") as mock_backup,
        ):
            mock_path.exists.return_value = True
            mock_path.read_text.return_value = "invalid json"
            mock_backup.return_value = get_default_settings_dict()
            _load_settings()
            mock_backup.assert_called()


class TestLocaleDetectionDetailed(unittest.TestCase):
    """详细测试系统区域语言检测逻辑"""

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific test")
    def test_detect_locale_windows(self):
        with patch("ctypes.windll.kernel32") as mock_windll:
            # 简体中文
            mock_windll.GetUserDefaultUILanguage.return_value = 2052
            self.assertEqual(_detect_locale_windows(), "zh-CN")
            # 繁体中文
            mock_windll.GetUserDefaultUILanguage.return_value = 1028
            self.assertEqual(_detect_locale_windows(), "zh-TW")
            # 日文
            mock_windll.GetUserDefaultUILanguage.return_value = 1041
            self.assertEqual(_detect_locale_windows(), "ja-JP")

    def test_detect_locale_python(self):
        with patch("locale.getlocale") as mock_getlocale:
            mock_getlocale.return_value = ("zh_CN", "UTF-8")
            self.assertEqual(_detect_locale_python(), "zh-CN")
            mock_getlocale.return_value = ("ja_JP", "UTF-8")
            self.assertEqual(_detect_locale_python(), "ja-JP")

    def test_detect_system_locale_fallback(self):
        with patch("os.name", "posix"), patch("akagi_ng.settings.settings._detect_locale_python", return_value=None):
            self.assertEqual(detect_system_locale(), "en-US")
