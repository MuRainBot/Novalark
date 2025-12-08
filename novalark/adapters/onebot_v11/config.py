from novalark.config import BaseAdapterConfig


class OnebotV11Config(BaseAdapterConfig):
    type: str = "onebot_v11"
    host: str
    port: int
    bot_url: str
