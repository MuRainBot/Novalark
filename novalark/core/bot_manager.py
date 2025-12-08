from .abstractions import Bot
from .logger_lib import get_logger

logger = get_logger()


class BotManager:
    def __init__(self):
        self._bots: dict[str, Bot] = {}

    def register(self, bot: Bot):
        """当一个适配器成功连接后，注册 Bot 实例"""
        if bot.id in self._bots:
            # 处理 ID 冲突的逻辑
            raise ValueError(f"Bot with id '{bot.id}' already registered.")
        self._bots[bot.id] = bot
        logger.debug(f"Bot '{bot.id}' registered.")

    def unregister(self, bot: Bot):
        """当一个适配器断开连接后，注销 Bot 实例"""
        if bot.id in self._bots:
            del self._bots[bot.id]
            logger.debug(f"Bot '{bot.id}' unregistered.")

    def get_bot(self, bot_id: str) -> Bot:
        """根据 ID 获取一个 Bot 实例"""
        bot = self._bots.get(bot_id)
        if not bot:
            raise KeyError(f"Bot with id '{bot_id}' not found or not online.")
        return bot

    def get_all_bots(self) -> list[Bot]:
        """获取所有在线的 Bot 实例"""
        return list(self._bots.values())


bot_manager = BotManager()
