"""Milky 适配器配置。"""

from __future__ import annotations

from ..base import AdapterConfig


class MilkyConfig(AdapterConfig):
    """Milky 协议端。

    协议端在同一地址上提供两个端点：``/api`` 收调用，``/event`` 推事件。
    """

    type: str = "milky"
    base_url: str = "http://127.0.0.1:3010"
    access_token: str = ""

    @property
    def api_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/api"

    @property
    def event_url(self) -> str:
        """事件通道的 WebSocket 地址。"""
        base = self.base_url.rstrip("/")
        if base.startswith("https://"):
            base = "wss://" + base[len("https://") :]
        elif base.startswith("http://"):
            base = "ws://" + base[len("http://") :]
        return f"{base}/event"
