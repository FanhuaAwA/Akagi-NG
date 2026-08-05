import os
import shutil
from functools import cache
from pathlib import Path


@cache
def get_app_root() -> Path:
    """
    动态推导项目根目录。
    """
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "assets").is_dir():
            return parent

    # 极端异常场景的回退保障（按照开发源目录推三层）
    return current.parents[3]


def get_assets_dir() -> Path:
    return get_app_root() / "assets"


def get_settings_dir() -> Path:
    target = get_user_data_root() / "config"
    legacy = get_app_root() / "config"
    if target != legacy and not target.exists() and legacy.is_dir():
        # Preserve portable Windows settings while moving all future writes out
        # of immutable AppImage/.app roots. copytree never mutates the bundle.
        shutil.copytree(legacy, target)
    return target


def get_lib_dir() -> Path:
    return get_app_root() / "lib"


def get_models_dir() -> Path:
    return get_app_root() / "models"


def get_logs_dir() -> Path:
    return get_user_data_root() / "logs"


@cache
def get_user_data_root() -> Path:
    configured = os.environ.get("AKAGI_USER_DATA_DIR", "").strip()
    if not configured:
        return get_app_root()
    path = Path(configured).expanduser()
    if not path.is_absolute():
        raise ValueError("AKAGI_USER_DATA_DIR must be an absolute path")
    return path.resolve()


def get_liqi_path() -> Path:
    override = get_settings_dir() / "liqi.json"
    return override if override.is_file() else get_assets_dir() / "liqi.json"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
