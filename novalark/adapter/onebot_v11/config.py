"""OneBot V11 适配器配置。"""

from pydantic import Field

from ..base import AdapterConfig


class OneBotV11Config(AdapterConfig):
    """OneBot V11 连接配置。

    bot 身份由连接后从实现端取到的 ``self_id`` 决定，不需要在这里声明。
    """

    type: str = "onebot_v11"

    host: str = Field(default="127.0.0.1", description="接收事件上报的监听地址")
    port: int = Field(default=5701, description="接收事件上报的监听端口")
    api_url: str = Field(
        default="http://127.0.0.1:5700", description="实现端的 HTTP API 地址"
    )
    access_token: str = Field(
        default="", description="API 访问令牌，非空时带 Authorization 头"
    )
