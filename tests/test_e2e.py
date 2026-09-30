"""端到端：假实现端 <-> 适配器 <-> 分发 <-> handler <-> 调 API。

不需要真实 QQ 就能覆盖完整链路。
"""

from __future__ import annotations

import asyncio
import json

import httpx

from novalark.adapter.onebot_v11 import OneBotV11Adapter, OneBotV11Config
from novalark.matcher import ToMe, emit, on
from novalark.model import GroupMessageEvent, RichText, Text
from novalark.runtime import task


def _report(text: str, *, seq: int, mentioned: bool = False) -> dict:
    segments = []
    if mentioned:
        segments.append({"type": "at", "data": {"qq": "10001"}})
    segments.append({"type": "text", "data": {"text": text}})
    return {
        "post_type": "message",
        "message_type": "group",
        "time": 1,
        "self_id": 10001,
        "group_id": 12345,
        "user_id": 999,
        "message_id": seq,
        "message": segments,
        "sender": {"user_id": 999, "nickname": "小明", "role": "admin"},
    }


def test_full_roundtrip(scenario, free_port):
    async def body():
        calls: list[tuple[str, dict]] = []

        def fake_impl(request: httpx.Request) -> httpx.Response:
            """假实现端：记录收到的调用并按需回应。"""
            path = request.url.path
            calls.append((path, json.loads(request.content or b"{}")))
            if path.endswith("get_login_info"):
                return httpx.Response(
                    200,
                    json={
                        "status": "ok",
                        "retcode": 0,
                        "data": {"user_id": 10001, "nickname": "测试机"},
                    },
                )
            if path.endswith("send_msg"):
                return httpx.Response(
                    200,
                    json={"status": "ok", "retcode": 0, "data": {"message_id": 555}},
                )
            return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": {}})

        config = OneBotV11Config(
            type="onebot_v11",
            host="127.0.0.1",
            port=free_port,
            api_url="http://fake-impl",
        )
        adapter = OneBotV11Adapter(config)
        # 先塞进去，start() 就不会再建真的（建一份要三秒的 TLS 上下文）
        adapter._client = httpx.AsyncClient(
            base_url="http://fake-impl", transport=httpx.MockTransport(fake_impl)
        )

        received: list[tuple[int, str]] = []
        sync_seen: list[str] = []

        @on(GroupMessageEvent, depends=[ToMe()])
        async def on_mentioned(event):
            received.append((event.group_id, event.plain_text))
            await event.reply("pong")

        @on(GroupMessageEvent, depends=[~ToMe()])
        def on_plain(event):
            # 同步 handler：用 task() 桥接到异步 API
            sync_seen.append(event.plain_text)
            task(event.reply(RichText(Text("收到"))))

        await adapter.start()
        assert adapter.bot.self_id == 10001
        assert adapter.bot.nickname == "测试机"

        async with httpx.AsyncClient() as http:
            resp = await http.post(
                f"http://127.0.0.1:{free_port}/",
                json=_report("ping", seq=42, mentioned=True),
            )
            # 上报端必须立刻拿到 200，不被分发阻塞
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"

            await http.post(
                f"http://127.0.0.1:{free_port}/", json=_report("普通消息", seq=43)
            )

        await asyncio.sleep(0.5)  # 等 fire-and-forget 的分发跑完

        assert received == [(12345, "ping")]
        assert sync_seen == ["普通消息"]

        sends = [payload for path, payload in calls if path.endswith("send_msg")]
        assert len(sends) == 2

        # 回复要带引用段，且指向收到的那条消息
        assert sends[0]["message"][0] == {"type": "reply", "data": {"id": "42"}}
        assert sends[0]["message"][1] == {"type": "text", "data": {"text": "pong"}}
        assert sends[0]["message_type"] == "group"
        assert sends[0]["group_id"] == 12345

        assert sends[1]["message"][0] == {"type": "reply", "data": {"id": "43"}}
        assert sends[1]["message"][1] == {"type": "text", "data": {"text": "收到"}}

        await adapter.stop()

    scenario(body)
