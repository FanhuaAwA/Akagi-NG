from datetime import datetime

from loguru import logger

from akagi_ng.core.paths import ensure_dir, get_logs_dir

LOG_DIR = ensure_dir(get_logs_dir())

LOG_FILE = LOG_DIR / f"akagi_{datetime.now():%Y%m%d_%H%M%S}.log"

LOG_FORMAT = "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level}</level> | {extra[module]} | {message}"


def configure_logging(level: str = "INFO"):
    logger.remove()

    logger.add(
        LOG_FILE,
        level=level,
        format=LOG_FORMAT,
        rotation="10 MB",  # 单文件超过 10 MB 自动分割
        retention="30 days",  # 保留 30 天的历史记录
        encoding="utf-8",
    )


configure_logging()

__all__ = ["configure_logging", "logger"]
