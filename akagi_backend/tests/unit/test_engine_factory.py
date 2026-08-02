"""
测试模块：akagi_backend/tests/unit/test_engine_factory.py

描述：针对引擎工厂 (Engine Factory) 和延迟加载机制的单元测试。
主要测试点：
- 延迟加载引擎 (LazyLocalEngine) 的初始化、代理和按需加载逻辑。
- 根据 3P/4P 配置加载对应的 Bot 和引擎实例。
- 根据在线/本地配置加载 EngineProvider 及其组合逻辑。
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from akagi_ng.mjai_bot.engine.factory import (
    _RESOURCE_CACHE,
    FlyAProbeEngine,
    LazyLocalEngine,
    load_bot_and_engine,
)
from akagi_ng.mjai_bot.status import BotStatusContext

# 自动应用 mock_lib_loader_module fixture（定义在 unit/conftest.py 中）
pytestmark = pytest.mark.usefixtures("mock_lib_loader_module")


@pytest.fixture(autouse=True)
def clear_cache():
    """每个测试前清理缓存。"""
    _RESOURCE_CACHE.clear()


@pytest.fixture
def mock_consts():
    return MagicMock()


def test_lazy_local_engine_init(mock_consts) -> None:
    """测试延迟加载引擎的初始化。"""
    path = Path("mortal.pth")
    engine = LazyLocalEngine(BotStatusContext(), path, mock_consts, is_3p=False)
    assert engine.name == "Local"
    assert engine._real_engine is None


def test_lazy_local_engine_ensure_engine(mock_consts) -> None:
    """测试延迟加载引擎的真实加载。"""
    path = Path("mortal.pth")
    engine = LazyLocalEngine(BotStatusContext(), path, mock_consts, is_3p=False)

    # Keep this unit test independent from the optional heavyweight torch runtime.
    mock_mortal = MagicMock()
    with patch.dict(sys.modules, {"akagi_ng.mjai_bot.engine.mortal": mock_mortal}):
        mock_load = mock_mortal.load_mortal_resource
        mock_resource = MagicMock()
        mock_load.return_value = mock_resource

        # 第一次触发加载
        real = engine._ensure_engine()
        assert real is not None
        mock_load.assert_called_once()


def test_lazy_local_engine_delegation(mock_consts) -> None:
    """测试延迟加载引擎的状态代理。"""
    status = BotStatusContext()
    engine = LazyLocalEngine(status, Path("mortal.pth"), mock_consts, is_3p=False)
    mock_real = MagicMock()
    mock_real.status = status
    engine._real_engine = mock_real

    # LazyLocalEngine 通过 BaseEngine 继承持有相同 status
    assert engine.status == status


def test_flya_probe_returns_legal_mask_without_local_model() -> None:
    engine = FlyAProbeEngine(BotStatusContext(), is_3p=False)
    masks = np.array([[False, True, False]], dtype=bool)

    actions, q_values, returned_masks, _ = engine.react_batch(np.empty((1, 0)), masks)

    assert actions == [1]
    assert q_values == [[0.0, 0.0, 0.0]]
    assert returned_masks == masks.tolist()
    assert engine.engine_type == "flya"


def test_load_bot_and_engine_4p(mock_lib_loader_module) -> None:
    """测试加载 4 人麻将引擎和 Bot。"""
    with patch("akagi_ng.mjai_bot.engine.factory.local_settings") as mock_settings:
        mock_settings.ot.online = False
        mock_settings.model_config.model_4p = "mortal_4p.pth"

        # Setup mock
        mock_lib_loader_module.libriichi.mjai.Bot = MagicMock()

        bot, engine = load_bot_and_engine(BotStatusContext(), player_id=0, is_3p=False)

        assert bot is not None
        assert engine is not None


def test_load_bot_and_engine_3p(mock_lib_loader_module) -> None:
    """测试加载 3 人麻将引擎和 Bot。"""
    with patch("akagi_ng.mjai_bot.engine.factory.local_settings") as mock_settings:
        mock_settings.ot.online = False
        mock_settings.model_config.model_3p = "mortal_3p.pth"

        mock_lib_loader_module.libriichi3p.mjai.Bot = MagicMock()

        bot, engine = load_bot_and_engine(BotStatusContext(), player_id=1, is_3p=True)

        assert bot is not None
        assert engine.is_3p is True


def test_load_bot_and_engine_online(mock_lib_loader_module) -> None:
    """测试加载包含在线引擎的 Provider。"""
    with (
        patch("akagi_ng.mjai_bot.engine.factory.local_settings") as mock_settings,
        patch("akagi_ng.mjai_bot.engine.factory.AkagiOTEngine") as mock_ot,
    ):
        mock_settings.ot.online = True
        mock_settings.ot.provider = "akagi_ot3"
        mock_settings.ot.protocol = "legacy"
        mock_settings.ot.server = "http://localhost"
        mock_settings.ot.api_key = "key"
        mock_settings.model_config.model_4p = "mortal_4p.pth"

        mock_lib_loader_module.libriichi.mjai.Bot = MagicMock()

        _, engine = load_bot_and_engine(BotStatusContext(), player_id=0, is_3p=False)

        # 应该创建了 AkagiOTEngine
        mock_ot.assert_called_once()
        assert engine.name.startswith("Provider")


def test_load_bot_and_engine_flya_does_not_create_legacy_online_engine(mock_lib_loader_module) -> None:
    with (
        patch("akagi_ng.mjai_bot.engine.factory.local_settings") as mock_settings,
        patch("akagi_ng.mjai_bot.engine.factory.AkagiOTEngine") as mock_ot,
        patch("akagi_ng.mjai_bot.engine.factory.threading.Thread") as mock_thread,
    ):
        mock_settings.ot.online = True
        mock_settings.ot.provider = "flya_test_api"
        mock_settings.ot.protocol = "legacy"
        mock_settings.model_config.model_4p = "mortal_4p.pth"
        mock_lib_loader_module.libriichi.mjai.Bot = MagicMock()

        _, engine = load_bot_and_engine(BotStatusContext(), player_id=0, is_3p=False, flya_probe=True)

        mock_ot.assert_not_called()
        assert engine.online_engine is None
        mock_thread.return_value.start.assert_called_once()


def test_load_bot_and_engine_flya_probe_prewarms_mortal_in_background(mock_lib_loader_module) -> None:
    with (
        patch("akagi_ng.mjai_bot.engine.factory.local_settings") as mock_settings,
        patch("akagi_ng.mjai_bot.engine.factory.threading.Thread") as mock_thread,
    ):
        mock_settings.ot.online = True
        mock_settings.ot.protocol = "v3"
        mock_settings.model_config.model_4p = "mortal_4p.pth"
        mock_lib_loader_module.libriichi.mjai.Bot = MagicMock()

        _, engine = load_bot_and_engine(BotStatusContext(), 0, flya_probe=True)

        assert isinstance(engine.local_engine, FlyAProbeEngine)
        mock_thread.return_value.start.assert_called_once()
