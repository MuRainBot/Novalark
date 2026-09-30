"""语义化动作：同一件事在两个协议下翻译成各自的接口。

断言写成「发出了哪个接口、带什么参数」。
"""

from __future__ import annotations

import json
from typing import Any

import httpx

import pytest

from novalark.adapter.base import UnsupportedAction
from novalark.adapter.milky import MilkyAdapter, MilkyConfig
from novalark.adapter.onebot_v11 import OneBotV11Adapter, OneBotV11Config
from novalark.model import (
    ActionError,
    At,
    ConversationRef,
    FriendRequestEvent,
    GroupInviteRequestEvent,
    GroupJoinRequestEvent,
    MessageRef,
    MessageScene,
    RichText,
    Text,
)

GROUP = ConversationRef(scene=MessageScene.GROUP, peer_id=12345)
FRIEND = ConversationRef(scene=MessageScene.FRIEND, peer_id=999)


class Recorder:
    """假实现端：记下每次调用，默认回成功，也可以回失败。"""

    def __init__(self, data: Any = None, *, fail: str = "") -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._data = data if data is not None else {}
        self._fail = fail

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.url.path, json.loads(request.content or b"{}")))
        failed = bool(self._fail)
        return httpx.Response(
            200,
            json={
                "status": "failed" if failed else "ok",
                "retcode": -1 if failed else 0,
                "data": self._data,
                "message": self._fail,
            },
        )

    @property
    def last(self) -> tuple[str, dict[str, Any]]:
        return self.calls[-1]

    @property
    def action(self) -> str:
        return self.last[0].rsplit("/", 1)[-1]

    @property
    def params(self) -> dict[str, Any]:
        return self.last[1]


async def _onebot(recorder: Recorder) -> OneBotV11Adapter:
    adapter = OneBotV11Adapter(
        OneBotV11Config(
            type="onebot_v11", host="127.0.0.1", port=1, api_url="http://fake"
        )
    )
    # 客户端是 start() 时才建的，直接把假的塞进去就行
    adapter._client = httpx.AsyncClient(
        base_url="http://fake", transport=httpx.MockTransport(recorder)
    )
    return adapter


async def _milky(recorder: Recorder) -> MilkyAdapter:
    adapter = MilkyAdapter(MilkyConfig(type="milky", base_url="http://fake"))
    adapter._client = httpx.AsyncClient(
        base_url="http://fake", transport=httpx.MockTransport(recorder)
    )
    return adapter


# ── 群管理 ─────────────────────────────────────────────────


def test_onebot_management_actions(scenario):
    async def body():
        rec = Recorder()
        bot = (await _onebot(rec)).bot

        await bot.recall(MessageRef(conversation=GROUP, seq=42))
        assert rec.action == "delete_msg"
        assert rec.params == {"message_id": 42}

        await bot.mute_member(12345, 999, 600)
        assert rec.action == "set_group_ban"
        assert rec.params == {"group_id": 12345, "user_id": 999, "duration": 600}

        await bot.mute_all(12345, True)
        assert rec.action == "set_group_whole_ban"
        assert rec.params == {"group_id": 12345, "enable": True}

        await bot.kick_member(12345, 999, reject_request=True)
        assert rec.action == "set_group_kick"
        assert rec.params["reject_add_request"] is True

        await bot.set_member_admin(12345, 999)
        assert rec.action == "set_group_admin"
        assert rec.params["enable"] is True

        await bot.set_member_card(12345, 999, "新名片")
        assert rec.params == {"group_id": 12345, "user_id": 999, "card": "新名片"}

        await bot.set_group_name(12345, "新群名")
        assert rec.params == {"group_id": 12345, "group_name": "新群名"}

        await bot.leave_group(12345)
        assert rec.action == "set_group_leave"

    scenario(body)


def test_milky_management_actions(scenario):
    """同一批动作，Milky 那边的接口名和参数名都不一样。"""

    async def body():
        rec = Recorder()
        bot = (await _milky(rec)).bot

        await bot.recall(MessageRef(conversation=GROUP, seq=42))
        assert rec.action == "recall_group_message"
        assert rec.params == {"group_id": 12345, "message_seq": 42}

        await bot.mute_member(12345, 999, 600)
        assert rec.action == "set_group_member_mute"
        assert rec.params["duration"] == 600

        await bot.mute_all(12345, True)
        assert rec.action == "set_group_whole_mute"
        assert rec.params == {"group_id": 12345, "is_mute": True}

        await bot.kick_member(12345, 999)
        assert rec.action == "kick_group_member"

        await bot.set_member_admin(12345, 999, enable=False)
        assert rec.action == "set_group_member_admin"
        assert rec.params["is_set"] is False

        await bot.set_group_name(12345, "新群名")
        assert rec.params == {"group_id": 12345, "new_group_name": "新群名"}

        await bot.leave_group(12345)
        assert rec.action == "quit_group"

    scenario(body)


def test_recall_splits_on_conversation(scenario):
    """撤回在模型里是一个概念，Milky 那边是两条接口。"""

    async def body():
        rec = Recorder()
        bot = (await _milky(rec)).bot

        await bot.recall(MessageRef(conversation=FRIEND, seq=7))
        assert rec.action == "recall_private_message"
        assert rec.params == {"user_id": 999, "message_seq": 7}

    scenario(body)


# ── 查询 ───────────────────────────────────────────────────


def test_onebot_queries_return_models(scenario):
    async def body():
        rec = Recorder({"group_id": 12345, "group_name": "测试群", "member_count": 10})
        bot = (await _onebot(rec)).bot

        group = await bot.get_group(12345)
        assert group.name == "测试群"
        assert group.member_count == 10

        rec._data = [
            {"group_id": 1, "group_name": "a"},
            {"group_id": 2, "group_name": "b"},
        ]
        assert [g.name for g in await bot.get_groups()] == ["a", "b"]

        rec._data = {
            "user_id": 999,
            "nickname": "小明",
            "role": "admin",
            "title": "群主",
        }
        member = await bot.get_group_member(12345, 999)
        assert member.display_name == "小明"
        assert member.title == "群主"
        assert member.is_admin

        rec._data = [{"user_id": 1, "nickname": "甲"}, {"user_id": 2, "nickname": "乙"}]
        assert [m.user_id for m in await bot.get_group_members(12345)] == [1, 2]

        rec._data = [{"user_id": 1, "nickname": "甲", "remark": "老王"}]
        friends = await bot.get_friends()
        assert friends[0].display_name == "老王"  # 备注优先

    scenario(body)


def test_milky_queries_unwrap_named_keys(scenario):
    """Milky 把列表包在命名键里，解出来要和 OneBot 一样是纯列表。"""

    async def body():
        rec = Recorder()
        bot = (await _milky(rec)).bot

        rec._data = {"group": {"group_id": 12345, "group_name": "测试群"}}
        assert (await bot.get_group(12345)).name == "测试群"

        rec._data = {"groups": [{"group_id": 1, "group_name": "a"}]}
        assert [g.name for g in await bot.get_groups()] == ["a"]

        rec._data = {"member": {"user_id": 999, "nickname": "小明"}}
        assert (await bot.get_group_member(12345, 999)).nickname == "小明"

        rec._data = {"members": [{"user_id": 1, "nickname": "甲"}]}
        assert (await bot.get_group_members(12345))[0].user_id == 1

        rec._data = {"friends": [{"user_id": 1, "nickname": "甲"}]}
        assert (await bot.get_friends())[0].user_id == 1

    scenario(body)


# ── 能力覆盖 ───────────────────────────────────────────────
#
# 适配器做不到的能力在调用点报错。


def test_onebot_reports_what_it_cannot_do(scenario):
    """标准 OneBot 没有的能力，调用点明确报错。"""

    async def body():
        bot = (await _onebot(Recorder())).bot

        with pytest.raises(UnsupportedAction, match="戳一戳"):
            await bot.send_nudge(GROUP, 999)

        with pytest.raises(UnsupportedAction, match="群公告"):
            await bot.get_announcements(12345)

        with pytest.raises(UnsupportedAction, match="精华"):
            await bot.set_essence(MessageRef(conversation=GROUP, seq=1))

    scenario(body)


def test_milky_covers_what_onebot_lacks(scenario):
    """Milky 支持标准 OneBot 没有的能力。"""

    async def body():
        rec = Recorder()
        bot = (await _milky(rec)).bot

        await bot.send_nudge(GROUP, 999)
        assert rec.action == "send_group_nudge"
        assert rec.params == {"group_id": 12345, "user_id": 999}

        await bot.send_nudge(FRIEND, 999)
        assert rec.action == "send_friend_nudge"
        assert rec.params == {"user_id": 999}

        await bot.send_reaction(MessageRef(conversation=GROUP, seq=42), "124")
        assert rec.action == "send_group_message_reaction"
        assert rec.params == {
            "group_id": 12345,
            "message_seq": 42,
            "reaction": "124",
            "is_add": True,
        }

        await bot.set_member_title(12345, 999, "大佬")
        assert rec.params["special_title"] == "大佬"

        await bot.send_announcement(12345, "通知")
        assert rec.action == "send_group_announcement"
        assert rec.params == {"group_id": 12345, "content": "通知"}

        await bot.like_profile(999, count=5)
        assert rec.params == {"user_id": 999, "count": 5}

    scenario(body)


def test_onebot_still_covers_the_standard_ones(scenario):
    """标准 OneBot 支持的接口照常实现。"""

    async def body():
        rec = Recorder(
            {"message_id": 42, "message": [{"type": "text", "data": {"text": "hi"}}]}
        )
        bot = (await _onebot(rec)).bot

        msg = await bot.get_message(MessageRef(conversation=GROUP, seq=42))
        assert rec.action == "get_msg"
        assert msg.plain_text == "hi"
        assert msg.ref.seq == 42  # 坐标用调用方给的，不从返回里推

        await bot.like_profile(999)
        assert rec.action == "send_like"
        assert rec.params == {"user_id": 999, "times": 1}

        await bot.set_nickname("新名字")
        assert rec.action == "set_qq_profile"
        assert rec.params == {"nickname": "新名字"}

    scenario(body)


def test_approve_request_takes_the_event_not_a_flag(scenario):
    """审批接口接收事件对象（OneBot 带 flag，Milky 带 uid）。"""

    async def body():
        rec = Recorder()
        bot = (await _onebot(rec)).bot
        request = FriendRequestEvent(user_id=999, flag="flag-1")

        await bot.approve_friend_request(request)
        assert rec.action == "set_friend_add_request"
        assert rec.params == {"flag": "flag-1", "approve": True, "remark": ""}

        group = GroupJoinRequestEvent(group_id=12345, flag="flag-2")
        await bot.approve_group_request(group, approve=False, reason="不加")
        assert rec.action == "set_group_add_request"
        assert rec.params["approve"] is False
        assert rec.params["reason"] == "不加"

    scenario(body)


def test_onebot_approves_with_the_matching_sub_type(scenario):
    """批准邀请时必须带 sub_type=invite，否则实现端会拒。"""

    async def body():
        rec = Recorder()
        bot = (await _onebot(rec)).bot

        await bot.approve_group_request(GroupJoinRequestEvent(group_id=1, flag="f"))
        assert rec.params["sub_type"] == "add"

        await bot.approve_group_request(
            GroupInviteRequestEvent(group_id=1, flag="f")
        )
        assert rec.params["sub_type"] == "invite"

    scenario(body)


def test_milky_approves_with_uid_and_the_right_notification_type(scenario):
    """Milky 认 uid 不认 flag；申请进群和邀请进群是两种 notification_type。

    这条以前完全没有覆盖 —— 「Milky 好友申请永远批不了」（uid 恒为空）就是
    从这个洞里漏出去的。
    """

    async def body():
        rec = Recorder()
        bot = (await _milky(rec)).bot

        await bot.approve_friend_request(FriendRequestEvent(user_id=999, flag="uid-1"))
        assert rec.action == "accept_friend_request"
        assert rec.params == {"initiator_uid": "uid-1"}

        await bot.approve_friend_request(
            FriendRequestEvent(user_id=999, flag="uid-2"), approve=False, remark="不加"
        )
        assert rec.action == "reject_friend_request"
        assert rec.params == {"initiator_uid": "uid-2", "reason": "不加"}

        await bot.approve_group_request(
            GroupJoinRequestEvent(group_id=1, sequence=7)
        )
        assert rec.action == "accept_group_request"
        assert rec.params["notification_type"] == "join_request"

        await bot.approve_group_request(
            GroupInviteRequestEvent(group_id=1, sequence=8)
        )
        assert rec.params["notification_type"] == "invited_join_request"

    scenario(body)


# ── 发消息 ─────────────────────────────────────────────────


def test_milky_send_message_picks_the_right_action(scenario):
    async def body():
        rec = Recorder({"message_seq": 88})
        bot = (await _milky(rec)).bot

        ref = await bot.send(GROUP, RichText(Text("hi"), At(user_id=7)))
        assert rec.action == "send_group_message"
        assert rec.params["group_id"] == 12345
        assert rec.params["message"] == [
            {"type": "text", "data": {"text": "hi"}},
            {"type": "mention", "data": {"user_id": 7}},
        ]
        assert ref.seq == 88

        await bot.send(FRIEND, "私聊")
        assert rec.action == "send_private_message"
        assert rec.params["user_id"] == 999

    scenario(body)


def test_send_accepts_a_plain_list_of_segments(scenario):
    """``bot.send(conv, [Text, At])`` 是最自然的写法 —— 以前会 TypeError。"""

    async def body():
        rec = Recorder({"message_seq": 1})
        bot = (await _milky(rec)).bot

        await bot.send(GROUP, [Text("查到了"), At(user_id=7)])
        assert rec.params["message"] == [
            {"type": "text", "data": {"text": "查到了"}},
            {"type": "mention", "data": {"user_id": 7}},
        ]

    scenario(body)


# ── 失败语义 ───────────────────────────────────────────────


def test_action_failure_raises_instead_of_going_quiet(scenario):
    """语义化动作失败一律抛 ActionError —— 以前一半抛一半静默。

    权限不足时静默「成功」，插件的工具层根本写不出统一的错误处理。
    """

    async def body():
        bot = (await _onebot(Recorder(fail="权限不足"))).bot
        for call in (
            bot.mute_member(12345, 999, 600),
            bot.kick_member(12345, 999),
            bot.set_member_card(12345, 999, "x"),
            bot.recall(MessageRef(conversation=GROUP, seq=1)),
        ):
            with pytest.raises(ActionError, match="权限不足"):
                await call

        mbot = (await _milky(Recorder(fail="权限不足"))).bot
        for call in (
            mbot.mute_member(12345, 999, 600),
            mbot.set_essence(MessageRef(conversation=GROUP, seq=1)),
            mbot.approve_friend_request(FriendRequestEvent(flag="uid")),
        ):
            with pytest.raises(ActionError, match="权限不足"):
                await call

    scenario(body)


def test_unsupported_action_is_caught_by_action_error(scenario):
    """「做不到」也是 ActionError —— 调用方一条 except 就够。

    这两个曾经是平行分支，`except ActionError` 兜不住 UnsupportedAction，
    和「全仓库一套错误语义」的注释对不上。
    """

    async def body():
        bot = (await _onebot(Recorder())).bot
        with pytest.raises(ActionError, match="戳一戳"):
            await bot.send_nudge(GROUP, 999)

    scenario(body)


def test_milky_rejects_group_only_actions_on_a_private_conversation(scenario):
    """表情回应和精华只有群接口 —— 私聊里不能把对方 QQ 号当群号发出去。"""

    async def body():
        rec = Recorder()
        bot = (await _milky(rec)).bot

        with pytest.raises(UnsupportedAction):
            await bot.send_reaction(MessageRef(conversation=FRIEND, seq=1), "124")
        with pytest.raises(UnsupportedAction):
            await bot.set_essence(MessageRef(conversation=FRIEND, seq=1))

        assert rec.calls == []  # 一个请求都没发出去

    scenario(body)
