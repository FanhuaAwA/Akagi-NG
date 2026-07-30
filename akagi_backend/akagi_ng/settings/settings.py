import ctypes
import json
import locale
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Self

import jsonschema
from jsonschema.exceptions import ValidationError

from akagi_ng.core.paths import ensure_dir, get_assets_dir, get_settings_dir
from akagi_ng.mjai_bot.ot3_proxy import normalize_ot3_proxy
from akagi_ng.schema.constants import MajsoulServer, Platform, get_game_url
from akagi_ng.settings.logger import logger

CONFIG_DIR: Path = ensure_dir(get_settings_dir())
SETTINGS_JSON_PATH: Path = CONFIG_DIR / "settings.json"

SCHEMA_PATH: Path = get_assets_dir() / "settings.schema.json"
DEFAULT_OT3_SERVER = "https://mjapi.shinkuan.me"
LEGACY_BLOCKED_OT3_SERVER = "https://server.akagiot.org"

# Windows 语言区域 ID（LCID）
LCID_ZH_CN = 2052  # 简体中文 (0x0804)
LCID_ZH_TW = 1028  # 繁体中文-台湾 (0x0404)
LCID_ZH_HK = 3076  # 繁体中文-香港 (0x0C04)
LCID_ZH_MO = 5124  # 繁体中文-澳门 (0x1404)
LCID_JA_JP = 1041  # 日文 (0x0411)


@dataclass(slots=True)
class OTConfig:
    online: bool
    server: str = DEFAULT_OT3_SERVER
    api_key: str = ""
    protocol: str = "v3"
    model_4p: str = ""
    model_3p: str = ""
    proxy_enabled: bool = False
    proxy: str = ""

    def model_for(self, is_3p: bool) -> str:
        return self.model_3p if is_3p else self.model_4p

    def effective_proxy(self) -> str:
        return self.proxy.strip() if self.proxy_enabled else ""


@dataclass(slots=True)
class MITMConfig:
    enabled: bool
    host: str
    port: int
    upstream: str


@dataclass(slots=True)
class MihomoConfig:
    enabled: bool = False
    mixed_port: int = 7890
    controller_port: int = 9090
    strict_route: bool = False


@dataclass(slots=True)
class ServerConfig:
    host: str
    port: int


@dataclass(slots=True)
class ModelConfig:
    temperature: float
    model_4p: str = "mortal.pth"
    model_3p: str = "mortal3p.pth"


@dataclass(slots=True)
class Settings:
    log_level: str
    locale: str
    game_url: str
    majsoul_server: MajsoulServer
    platform: Platform
    mitm: MITMConfig
    mihomo: MihomoConfig
    server: ServerConfig
    ot: OTConfig
    model_config: ModelConfig

    def update(self, data: dict):
        """从字典更新设置"""
        _update_settings(self, data)
        self._normalize_game_url()

    def __post_init__(self):
        self._normalize_game_url()

    def _normalize_game_url(self):
        """根据平台设置派生只读 game_url"""
        self.game_url = get_game_url(self.platform, self.majsoul_server)

    def save(self):
        """保存设置到 settings.json 文件"""
        _save_settings(asdict(self))
        logger.info(f"Saved settings to {SETTINGS_JSON_PATH}")

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        """从字典创建 Settings 对象"""
        mitm_data = data.get("mitm", {})
        mihomo_data = data.get("mihomo", {})
        server_data = data.get("server", {})
        model_config_data = data.get("model_config", {})
        ot_data = data.get("ot", {})
        game_url = data.get("game_url", "")

        platform_val = data.get("platform")
        platform = Platform(platform_val) if platform_val else Platform.MAJSOUL
        majsoul_server_val = data.get("majsoul_server")
        majsoul_server = MajsoulServer(majsoul_server_val) if majsoul_server_val else MajsoulServer.CN

        return cls(
            log_level=data.get("log_level", "INFO"),
            locale=data.get("locale", "zh-CN"),
            game_url=game_url,
            majsoul_server=majsoul_server,
            platform=platform,
            mitm=MITMConfig(
                enabled=mitm_data.get("enabled", False),
                host=mitm_data.get("host", "127.0.0.1"),
                port=mitm_data.get("port", 6789),
                upstream=mitm_data.get("upstream", ""),
            ),
            mihomo=MihomoConfig(
                enabled=mihomo_data.get("enabled", False),
                mixed_port=mihomo_data.get("mixed_port", 7890),
                controller_port=mihomo_data.get("controller_port", 9090),
                strict_route=mihomo_data.get("strict_route", False),
            ),
            server=ServerConfig(
                host=server_data.get("host", "127.0.0.1"),
                port=server_data.get("port", 8765),
            ),
            ot=OTConfig(
                online=ot_data.get("online", False),
                server=_migrate_ot3_server(ot_data.get("server", DEFAULT_OT3_SERVER)),
                api_key=ot_data.get("api_key", ""),
                # Existing pre-OT3 settings have no protocol marker and must
                # remain on the legacy tensor endpoint until explicitly migrated.
                protocol=ot_data.get("protocol", "legacy" if ot_data else "v3"),
                model_4p=ot_data.get("model_4p", ""),
                model_3p=ot_data.get("model_3p", ""),
                proxy_enabled=ot_data.get("proxy_enabled", False),
                proxy=ot_data.get("proxy", ""),
            ),
            model_config=ModelConfig(
                model_4p=model_config_data.get("model_4p", "mortal.pth"),
                model_3p=model_config_data.get("model_3p", "mortal3p.pth"),
                temperature=model_config_data.get("temperature", 0.3),
            ),
        )


def _detect_locale_windows() -> str | None:
    """通过 Windows API 检测语言环境"""
    try:
        windll = ctypes.windll.kernel32
        lcid = windll.GetUserDefaultUILanguage()
        if lcid == LCID_ZH_CN:
            return "zh-CN"
        if lcid in (LCID_ZH_TW, LCID_ZH_HK, LCID_ZH_MO):
            return "zh-TW"
        if lcid == LCID_JA_JP:
            return "ja-JP"
    except (AttributeError, OSError) as e:
        logger.warning(f"Failed to detect locale via Windows API: {e}")
    return None


def _detect_locale_python() -> str | None:
    """通过 Python locale 模块检测语言环境"""
    try:
        sys_locale = locale.getlocale()[0]
        if sys_locale:
            if sys_locale.startswith("zh_CN"):
                return "zh-CN"
            if sys_locale.startswith(("zh_TW", "zh_HK")):
                return "zh-TW"
            if sys_locale.startswith("ja"):
                return "ja-JP"
    except Exception as e:
        logger.warning(f"Failed to detect locale via python locale: {e}")
    return None


def detect_system_locale() -> str:
    """
    检测系统语言环境,返回支持的语言之一:
    zh-CN, zh-TW, ja-JP, en-US。
    检测失败或不支持的语言默认返回 en-US。
    """
    # 优先使用 Windows API (如果在 Windows 上)
    if os.name == "nt":
        windows_locale = _detect_locale_windows()
        if windows_locale:
            return windows_locale

    # 回退到 Python locale
    python_locale = _detect_locale_python()
    if python_locale:
        return python_locale

    # 默认返回英语
    return "en-US"


def get_default_settings_dict() -> dict:
    return {
        "log_level": "INFO",
        "locale": detect_system_locale(),
        "game_url": get_game_url(Platform.MAJSOUL, MajsoulServer.CN),
        "majsoul_server": MajsoulServer.CN.value,
        "platform": Platform.MAJSOUL.value,
        "mitm": {
            "enabled": False,
            "host": "127.0.0.1",
            "port": 6789,
            "upstream": "",
        },
        "mihomo": {
            "enabled": False,
            "mixed_port": 7890,
            "controller_port": 9090,
            "strict_route": False,
        },
        "server": {"host": "127.0.0.1", "port": 8765},
        "ot": {
            "online": False,
            "server": DEFAULT_OT3_SERVER,
            "api_key": "",
            "protocol": "v3",
            "model_4p": "",
            "model_3p": "",
            "proxy_enabled": False,
            "proxy": "",
        },
        "model_config": {
            "model_4p": "mortal.pth",
            "model_3p": "mortal3p.pth",
            "temperature": 0.3,
        },
    }


def get_settings_dict() -> dict:
    """从 settings.json 读取设置"""
    return asdict(Settings.from_dict(json.loads(SETTINGS_JSON_PATH.read_text(encoding="utf-8"))))


def verify_settings(data: dict) -> bool:
    """根据 schema 和附加规则验证设置"""
    try:
        jsonschema.validate(data, _get_schema())
    except ValidationError as e:
        logger.error(f"Settings validation error: {e.message}")
        return False

    mitm_port = data.get("mitm", {}).get("port")
    server_port = data.get("server", {}).get("port")
    if mitm_port and server_port and mitm_port == server_port:
        logger.error(f"Settings validation error: mitm proxy port '{mitm_port}' cannot be the same as server port")
        return False

    mihomo_data = data.get("mihomo", {})
    used_ports = {
        "mitm": mitm_port,
        "server": server_port,
        "mihomo mixed": mihomo_data.get("mixed_port"),
        "mihomo controller": mihomo_data.get("controller_port"),
    }
    configured_ports = [(name, port) for name, port in used_ports.items() if port]
    for index, (left_name, left_port) in enumerate(configured_ports):
        for right_name, right_port in configured_ports[index + 1 :]:
            if left_port == right_port:
                logger.error(
                    f"Settings validation error: {left_name} port '{left_port}' cannot be the same as {right_name} port"
                )
                return False

    if mihomo_data.get("enabled") and not data.get("mitm", {}).get("enabled"):
        logger.error("Settings validation error: mihomo requires the MITM proxy to be enabled")
        return False

    return _verify_ot3_proxy(data.get("ot", {}))


def _verify_ot3_proxy(ot_data: dict) -> bool:
    if not ot_data.get("proxy_enabled"):
        return True
    try:
        if normalize_ot3_proxy(ot_data.get("proxy", "")):
            return True
        logger.error("Settings validation error: enabled OT3 proxy requires a proxy URL")
    except (TypeError, ValueError) as exc:
        logger.error(f"Settings validation error: {exc}")
    return False


def _load_settings() -> Settings:
    """
    加载并验证设置。
    - 检查 schema 文件是否存在
    - 从 CONFIG_DIR 读取 settings.json
    - 如果 settings.json 损坏，备份并重建默认设置

    Raises:
        FileNotFoundError: schema 不存在
    """
    # 验证 schema 文件存在
    schema = _get_schema()

    if not SETTINGS_JSON_PATH.exists():
        logger.warning(f"{SETTINGS_JSON_PATH} not found. Creating a default {SETTINGS_JSON_PATH}.")
        SETTINGS_JSON_PATH.write_text(
            json.dumps(get_default_settings_dict(), indent=4, ensure_ascii=False), encoding="utf-8"
        )

    try:
        loaded_settings = json.loads(SETTINGS_JSON_PATH.read_text(encoding="utf-8"))
        jsonschema.validate(loaded_settings, schema)
    except json.JSONDecodeError as e:
        loaded_settings = _backup_and_reset_settings(f"settings.json corrupted: {e}")
    except ValidationError as e:
        loaded_settings = _backup_and_reset_settings(f"settings.json validation failed: {e.message}")

    return Settings.from_dict(loaded_settings)


def _get_schema() -> dict:
    """获取 settings.json 的 schema"""
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f"settings.schema.json not found at {SCHEMA_PATH}")
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _update_settings(settings: Settings, data: dict):
    """从字典更新 Settings 对象"""
    settings.log_level = data.get("log_level", "INFO")
    settings.locale = data.get("locale", "zh-CN")
    settings.game_url = data.get("game_url", "")
    settings.majsoul_server = MajsoulServer(data.get("majsoul_server", MajsoulServer.CN))
    settings.platform = Platform(data.get("platform", Platform.MAJSOUL))

    mitm_data = data.get("mitm", {})
    settings.mitm.enabled = mitm_data.get("enabled", False)
    settings.mitm.host = mitm_data.get("host", "127.0.0.1")
    settings.mitm.port = mitm_data.get("port", 6789)
    settings.mitm.upstream = mitm_data.get("upstream", "")

    mihomo_data = data.get("mihomo", {})
    settings.mihomo.enabled = mihomo_data.get("enabled", False)
    settings.mihomo.mixed_port = mihomo_data.get("mixed_port", 7890)
    settings.mihomo.controller_port = mihomo_data.get("controller_port", 9090)
    settings.mihomo.strict_route = mihomo_data.get("strict_route", False)

    server_data = data.get("server", {})
    settings.server.host = server_data.get("host", "127.0.0.1")
    settings.server.port = server_data.get("port", 8765)

    model_config_data = data.get("model_config", {})
    settings.model_config.model_4p = model_config_data.get("model_4p", "mortal.pth")
    settings.model_config.model_3p = model_config_data.get("model_3p", "mortal3p.pth")
    settings.model_config.temperature = model_config_data.get("temperature", 0.3)

    ot_data = data.get("ot", {})
    settings.ot.online = ot_data.get("online", False)
    settings.ot.server = _migrate_ot3_server(ot_data.get("server", DEFAULT_OT3_SERVER))
    settings.ot.api_key = ot_data.get("api_key", "")
    settings.ot.protocol = ot_data.get("protocol", "legacy" if ot_data else "v3")
    settings.ot.model_4p = ot_data.get("model_4p", "")
    settings.ot.model_3p = ot_data.get("model_3p", "")
    settings.ot.proxy_enabled = ot_data.get("proxy_enabled", False)
    settings.ot.proxy = ot_data.get("proxy", "")


def _migrate_ot3_server(value: object) -> str:
    """Replace the obsolete Akagi-NG placeholder with Akagi-3's service."""
    if not isinstance(value, str):
        return DEFAULT_OT3_SERVER
    server = value.strip()
    if server.rstrip("/").lower() == LEGACY_BLOCKED_OT3_SERVER.lower():
        return DEFAULT_OT3_SERVER
    return server


def _save_settings(data: dict):
    """保存 settings.json"""
    SETTINGS_JSON_PATH.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")


def _backup_and_reset_settings(reason: str) -> dict:
    """
    备份当前设置文件并重建默认值。
    返回默认设置字典。
    """
    logger.error(reason)
    bak_path = SETTINGS_JSON_PATH.with_suffix(".json.bak")
    logger.warning(f"Backup settings.json to {bak_path}")

    if SETTINGS_JSON_PATH.exists():
        os.replace(SETTINGS_JSON_PATH, bak_path)

    logger.warning("Creating new settings.json with default values")
    default_settings = get_default_settings_dict()
    SETTINGS_JSON_PATH.write_text(json.dumps(default_settings, indent=4, ensure_ascii=False), encoding="utf-8")

    return default_settings


local_settings: Settings = _load_settings()
