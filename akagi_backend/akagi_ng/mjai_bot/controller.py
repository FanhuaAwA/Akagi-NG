from akagi_ng.mjai_bot.logger import logger
from akagi_ng.mjai_bot.online_inference import OnlineInferenceExecutor
from akagi_ng.mjai_bot.status import BotStatusContext
from akagi_ng.schema.notifications import NotificationCode
from akagi_ng.schema.protocols import BotProtocol
from akagi_ng.schema.types import (
    AkagiEvent,
    MJAIEventBase,
    MJAIResponse,
    StartGameEvent,
    SystemEvent,
    SystemShutdownEvent,
)
from akagi_ng.settings import local_settings


class Controller:
    def __init__(
        self,
        status: BotStatusContext | None = None,
        *,
        flya_probe: bool = False,
        online_executor: OnlineInferenceExecutor | None = None,
    ):
        self.status: BotStatusContext = status or BotStatusContext()
        self.flya_probe = flya_probe
        self.bot: BotProtocol | None = None
        self._bot_signature: tuple[str, str] | None = None
        self.pending_start_game_event: StartGameEvent | None = None  # Bot 将在收到第一个 start_game 事件时初始化
        self.last_response: MJAIResponse | None = None  # 存储最近一次 Bot 的决策结果
        self.online_executor = online_executor

    def react(self, event: AkagiEvent):
        """
        处理来自 Bridge 的事件序列。
        """
        try:
            # 清除本轮的通知标志和响应结果
            self.status.clear_flags()
            self.last_response = None
            self._handle_event(event)

        except Exception as e:
            logger.exception(f"Controller error: {e}")
            self.status.set_flag(NotificationCode.BOT_RUNTIME_ERROR)

    def _handle_event(self, event: AkagiEvent):
        """分发单个事件并确保 Bot 已就绪"""
        match event:
            # 1. 拦截并处理特殊的管理事件
            case SystemEvent() | SystemShutdownEvent():
                return
            case StartGameEvent():
                self._handle_start_game_event(event)
            # 2. 安全检查：如果从未收到过 start_game 或 bot 激活失败，则报错
            case MJAIEventBase():
                if self.bot is None:
                    logger.error(f"Received event {event.type} before bot activation. Bot is not active.")
                    return

        # 3. 正常执行决策
        try:
            self.last_response = self.bot.react(event)
            if self.last_response:
                logger.trace(f"<- {self.last_response}")
        except Exception as e:
            logger.exception(f"Error calling bot.react: {e}")
            self.status.set_flag(NotificationCode.BOT_RUNTIME_ERROR)

    def _handle_start_game_event(self, event: StartGameEvent):
        """处理 start_game 事件：重置状态并缓存上下文"""
        self.pending_start_game_event = event
        is_3p = event.is_3p
        logger.info(f"StartGame event mode: is_3p={is_3p}.")
        self._ensure_bot_activated(is_3p)

        if self.bot:
            self.status.set_flag(NotificationCode.GAME_CONNECTED)

    def _ensure_bot_activated(self, is_3p: bool):
        """
        确保正确的 Bot 已经加载并完成了初始化（Context Sync）。
        """
        target_name = "mortal3p" if is_3p else "mortal"
        current_name = self.current_bot_name
        target_signature = (target_name, self._engine_mode())

        if current_name == target_name and self._bot_signature == target_signature:
            return

        if not self.bot:
            logger.info(f"Activating {target_name} bot.")
        else:
            logger.info(f"Switching bot from {current_name} to {target_name}.")

        if not self._choose_bot(target_name):
            logger.error(f"Failed to load {target_name} bot")
            self.status.set_flag(NotificationCode.BOT_SWITCH_FAILED)
            return
        self._bot_signature = target_signature

        if not self.pending_start_game_event:
            logger.error(f"No pending start_game event to replay for {target_name} bot activation.")
            self.status.set_flag(NotificationCode.MODEL_LOAD_FAILED)

    @property
    def current_bot_name(self) -> str | None:
        if not self.bot:
            return None
        return "mortal3p" if self.bot.is_3p else "mortal"

    def _choose_bot(self, bot_name: str) -> bool:
        if bot_name in ("mortal", "mortal3p"):
            from akagi_ng.mjai_bot.bot import MortalBot

            if self.bot and hasattr(self.bot, "close"):
                self.bot.close()
            self.bot = MortalBot(
                status=self.status,
                is_3p=(bot_name == "mortal3p"),
                flya_probe=self._engine_mode() == "flya",
                online_executor=self.online_executor,
            )
            return True
        return False

    def close(self) -> None:
        if self.bot and hasattr(self.bot, "close"):
            self.bot.close()
        self.bot = None

    def _engine_mode(self) -> str:
        ot = local_settings.ot
        if (
            self.flya_probe
            and ot.online
            and ot.provider == "flya_test_api"
            and ot.flya_server.strip()
            and ot.flya_api_key.strip()
        ):
            return "flya"
        if ot.online and ot.provider == "akagi_ot3" and getattr(ot, "protocol", "v3") == "legacy":
            return "legacy"
        return "native"
