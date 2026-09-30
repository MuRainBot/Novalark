"""应用装配与生命周期。"""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path

from .adapter import Adapter, get_adapter_entry
from .config import Settings, init_settings
from .log import get_logger, set_level, setup_logging
from .matcher.dispatch import dispatch
from .model.events import AppStartEvent, AppStopEvent, Event
from .paths import init_paths
from .plugin import manager
from .runtime import runtime


class App:
    """框架实例。"""

    def __init__(self, work_dir: str | Path | None = None) -> None:
        self._work_dir = work_dir
        self._adapter: Adapter | None = None
        self._stop = asyncio.Event()

    @property
    def adapter(self) -> Adapter | None:
        return self._adapter

    async def run(self) -> None:
        paths = init_paths(self._work_dir)
        paths.register_import_root()
        setup_logging(paths)
        settings = init_settings()
        set_level(settings.core.log_level)

        logger = get_logger()
        logger.info("Novalark 启动中…")

        runtime.bind(pool_size=settings.core.thread_pool_size)

        self._load_plugins()
        self._build_adapter(settings)
        if self._adapter is not None:
            logger.info(f"已装载适配器 {self._adapter.name}")
        else:
            logger.warning("没有配置适配器，框架不会收到事件")

        self._install_signal_handlers()

        try:
            if self._adapter is not None:
                await self._adapter.start()
            await self._announce(AppStartEvent(), logger)
            logger.info("就绪")
            await self._stop.wait()
        finally:
            await self._shutdown(logger)

    def stop(self) -> None:
        """从任意线程请求停止。"""
        loop = runtime.loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._stop.set)
        else:
            self._stop.set()

    # ── 内部 ──────────────────────────────────────────────

    def _load_plugins(self) -> None:
        plugins = manager.load_all()
        if plugins:
            names = "、".join(plugin.name for plugin in plugins)
            get_logger().info(f"共 {len(plugins)} 个插件: {names}")

    def _build_adapter(self, settings: Settings) -> None:
        config = settings.adapter
        if config is None:
            return
        entry = get_adapter_entry(config.type)
        adapter = entry.adapter_class(config)
        self._adapter = adapter

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._stop.set)
            except (NotImplementedError, RuntimeError):
                # 退回 signal.signal
                try:
                    signal.signal(
                        sig, lambda *_: loop.call_soon_threadsafe(self._stop.set)
                    )
                except (ValueError, OSError):
                    pass

    async def _announce(self, event: Event, logger) -> None:
        """派发一个生命周期事件。"""
        adapter = self._adapter
        if adapter is not None:
            event.self_id = adapter.bot.self_id
            event.bind(adapter.bot)
        try:
            await dispatch(event)
        except Exception:
            logger.exception(f"处理 {type(event).__name__} 时出错")

    async def _shutdown(self, logger) -> None:
        logger.info("正在停止…")
        await self._announce(AppStopEvent(), logger)
        if self._adapter is not None:
            try:
                await self._adapter.stop()
            except Exception:
                logger.exception(f"停止适配器 {self._adapter.name} 时出错")
        await runtime.shutdown()
        logger.info("已停止")
