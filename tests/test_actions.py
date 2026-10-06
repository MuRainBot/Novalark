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
    FriendRequest,
    FriendRequestEvent,
    GroupInviteRequest,
    GroupInviteRequestEvent,
    GroupJoinRequest,
    GroupJoinRequestEvent,
    MessageRef,
    MessageScene,
    RequestState,
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

        # 下面这些是 go-cqhttp 一系的扩展接口，不在 OneBot 11 标准里，
        # 别的实现端上多半也没有 —— 与其发个没人认的 action，不如报错
        with pytest.raises(UnsupportedAction, match="历史消息"):
            await bot.get_history(GROUP)

        with pytest.raises(UnsupportedAction, match="删除好友"):
            await bot.delete_friend(999)

        with pytest.raises(UnsupportedAction, match="修改昵称"):
            await bot.set_nickname("新名字")

        with pytest.raises(UnsupportedAction, match="修改签名"):
            await bot.set_bio("新签名")

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

        # 改昵称在 OneBot 标准里没有（那边叫 set_qq_profile，是实现端扩展），
        # Milky 有 —— 直接用 Milky 自己的定义
        await bot.set_nickname("新名字")
        assert rec.action == "set_nickname"
        assert rec.params == {"nickname": "新名字"}

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

    scenario(body)


def test_approve_request_takes_the_request_not_a_bare_flag(scenario):
    """审批接口收的是「申请」本身，不是散开的 flag —— OneBot 和 Milky 的凭据
    长得不一样（flag / uid），由适配器去认。"""

    async def body():
        rec = Recorder()
        bot = (await _onebot(rec)).bot
        request = FriendRequest(user_id=999, flag="flag-1")

        await bot.approve_friend_request(request)
        assert rec.action == "set_friend_add_request"
        assert rec.params == {"flag": "flag-1", "approve": True, "remark": ""}

        group = GroupJoinRequest(group_id=12345, flag="flag-2")
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

        await bot.approve_group_request(GroupJoinRequest(group_id=1, flag="f"))
        assert rec.params["sub_type"] == "add"

        await bot.approve_group_request(GroupInviteRequest(group_id=1, flag="f"))
        assert rec.params["sub_type"] == "invite"

    scenario(body)


def test_milky_approves_with_uid_and_the_right_notification_type(scenario):
    """Milky 认 uid 不认 flag；群请求按语义分走两组 API。

    - 自己申请 / 被群成员邀请：都走 ``accept_group_request``，靠
      ``notification_type`` 区分来源
    - 机器人自己被邀请：走 ``accept_group_invitation``，凭据是 ``invitation_seq``

    这条以前完全没有覆盖 —— 「Milky 好友申请永远批不了」（uid 恒为空）就是
    从这个洞里漏出去的。
    """

    async def body():
        rec = Recorder()
        bot = (await _milky(rec)).bot

        await bot.approve_friend_request(FriendRequest(user_id=999, flag="uid-1"))
        assert rec.action == "accept_friend_request"
        assert rec.params == {"initiator_uid": "uid-1", "is_filtered": False}

        await bot.approve_friend_request(
            FriendRequest(user_id=999, flag="uid-2"), approve=False, remark="不加"
        )
        assert rec.action == "reject_friend_request"
        assert rec.params == {
            "initiator_uid": "uid-2",
            "is_filtered": False,
            "reason": "不加",
        }

        # 自己申请进群
        await bot.approve_group_request(GroupJoinRequest(group_id=1, sequence=7))
        assert rec.action == "accept_group_request"
        assert rec.params == {
            "group_id": 1,
            "notification_seq": 7,
            "notification_type": "join_request",
            "is_filtered": False,
        }

        # 被群成员邀请进群：同一个 API，notification_type 不同
        await bot.approve_group_request(
            GroupJoinRequest(group_id=1, sequence=9, invitor_id=888)
        )
        assert rec.action == "accept_group_request"
        assert rec.params["notification_type"] == "invited_join_request"

        # 机器人自己被邀请：另一组 API，凭据是 invitation_seq
        await bot.approve_group_request(GroupInviteRequest(group_id=1, sequence=8))
        assert rec.action == "accept_group_invitation"
        assert rec.params == {"group_id": 1, "invitation_seq": 8}

        await bot.approve_group_request(
            GroupInviteRequest(group_id=1, sequence=8), approve=False
        )
        assert rec.action == "reject_group_invitation"
        assert rec.params == {"group_id": 1, "invitation_seq": 8}

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
            mbot.approve_friend_request(FriendRequest(flag="uid")),
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


# ── 群文件 ─────────────────────────────────────────────────


def test_milky_file_actions_use_the_protocol_field_names(scenario):
    """Milky 的参数名(`file_uri` / `parent_folder_id`…)和模型层的签名对不上，
    这一层映射错了表现就是"传上去的文件名不对/传不上去"。"""

    async def body():
        rec = Recorder(data={"file_id": "f1", "download_url": "http://d/1"})
        bot = (await _milky(rec)).bot

        assert await bot.upload_group_file(1, "file:///tmp/a.txt", "a.txt") == "f1"
        assert rec.params == {
            "group_id": 1,
            "parent_folder_id": "/",
            "file_uri": "file:///tmp/a.txt",
            "file_name": "a.txt",
        }

        assert await bot.get_group_file_url(1, "f1") == "http://d/1"
        assert rec.params == {"group_id": 1, "file_id": "f1"}

        # 私聊下载要带 TriSHA1，就是 File.file_hash 那个
        assert await bot.get_private_file_url(2, "f1", "hash1") == "http://d/1"
        assert rec.params == {
            "user_id": 2,
            "file_id": "f1",
            "file_hash": "hash1",
            "is_self_send": False,
        }

        await bot.move_group_file(1, "f1", target_folder_id="dir1")
        assert rec.params == {
            "group_id": 1,
            "file_id": "f1",
            "parent_folder_id": "/",
            "target_folder_id": "dir1",
        }

    scenario(body)


def test_milky_folder_actions_return_the_new_id(scenario):
    async def body():
        rec = Recorder(data={"folder_id": "dir1"})
        bot = (await _milky(rec)).bot

        assert await bot.create_group_folder(1, "资料") == "dir1"
        assert rec.params == {"group_id": 1, "folder_name": "资料"}

        await bot.rename_group_folder(1, "dir1", "新资料")
        assert rec.params == {
            "group_id": 1,
            "folder_id": "dir1",
            "new_folder_name": "新资料",
        }

    scenario(body)


def test_onebot_has_no_file_or_pin_actions(scenario):
    """OneBot 侧只按标准协议做 —— 没实现的能力要明确报错，不能静默当成成功。"""

    async def body():
        rec = Recorder()
        bot = (await _onebot(rec)).bot

        for call in (
            bot.upload_group_file(1, "file:///tmp/a.txt", "a.txt"),
            bot.persist_group_file(1, "f1"),
            bot.get_pinned(),
            bot.get_friend_requests(),
        ):
            with pytest.raises(UnsupportedAction):
                await call

        assert rec.calls == []  # 一个请求都没发出去

    scenario(body)


def test_onebot_credential_actions_follow_the_standard(scenario):
    """get_cookies / get_csrf_token / get_version_info 是 OneBot **标准**里的，
    不能因为 Milky 那边也有就当成扩展挡掉。"""

    async def body():
        rec = Recorder(data={"cookies": "a=1", "token": 12345, "app_name": "impl"})
        bot = (await _onebot(rec)).bot

        assert await bot.get_cookies("qun.qq.com") == "a=1"
        assert rec.action == "get_cookies"
        assert rec.params == {"domain": "qun.qq.com"}

        # 标准里字段叫 token，Milky 那边叫 csrf_token
        assert await bot.get_csrf_token() == "12345"
        assert rec.action == "get_csrf_token"

        info = await bot.get_impl_info()
        assert rec.action == "get_version_info"
        assert info.name == "impl"

    scenario(body)


# ── 置顶与实现端信息 ───────────────────────────────────────


def test_milky_reads_pins_and_impl_info(scenario):
    async def body():
        rec = Recorder(
            data={"friends": [{"user_id": 7}], "groups": [{"group_id": 8}]}
        )
        bot = (await _milky(rec)).bot

        friends, groups = await bot.get_pinned()
        assert [f.user_id for f in friends] == [7]
        assert [g.group_id for g in groups] == [8]

        await bot.set_pinned(ConversationRef(scene=MessageScene.GROUP, peer_id=8), False)
        assert rec.params == {
            "message_scene": "group",
            "peer_id": 8,
            "is_pinned": False,
        }

    scenario(body)


def test_request_events_approve_themselves(scenario):
    """``event.approve()`` 是插件的主要写法 —— 和 ``event.reply()`` 一个路子，
    不用把 event 递回 Bot。"""

    async def body():
        rec = Recorder()
        bot = (await _milky(rec)).bot

        request = GroupJoinRequestEvent(
            request=GroupJoinRequest(group_id=1, sequence=7)
        ).bind(bot)
        await request.approve()
        assert rec.action == "accept_group_request"

        await request.reject("不加")
        assert rec.action == "reject_group_request"
        assert rec.params["reason"] == "不加"

        friend = FriendRequestEvent(request=FriendRequest(user_id=9, flag="uid-1"))
        await friend.bind(bot).approve("小名")
        assert rec.action == "accept_friend_request"
        assert rec.params["initiator_uid"] == "uid-1"

    scenario(body)


def test_milky_friend_requests_come_back_approvable(scenario):
    """查出来的每一条和事件里的是**同一种东西**（``FriendRequest``），带着同一个
    ``flag`` 凭据，所以拿起来就能直接交给 ``approve_friend_request``。"""

    async def body():
        rec = Recorder(
            data={
                "requests": [
                    {
                        "initiator_id": 42,
                        "initiator_uid": "u-42",
                        "comment": "求加",
                        "via": "group",
                        "state": "pending",
                        "is_filtered": True,
                    }
                ]
            }
        )
        bot = (await _milky(rec)).bot

        requests = await bot.get_friend_requests()
        assert len(requests) == 1
        assert isinstance(requests[0], FriendRequest)
        assert requests[0].user_id == 42
        assert requests[0].flag == "u-42"  # 批的时候要用它
        assert requests[0].state is RequestState.PENDING
        assert requests[0].is_filtered is True

        await bot.approve_friend_request(requests[0])
        assert rec.action == "accept_friend_request"
        assert rec.params == {"initiator_uid": "u-42", "is_filtered": True}

    scenario(body)


def test_a_request_survives_a_round_trip(scenario):
    """申请存下来、过一阵再批 —— 序列化这一圈不能丢凭据。"""

    async def body():
        rec = Recorder()
        bot = (await _milky(rec)).bot

        saved = GroupJoinRequestEvent(
            request=GroupJoinRequest(
                group_id=1, sequence=7, invitor_id=888, is_filtered=True
            )
        ).model_dump()

        restored = GroupJoinRequestEvent.model_validate(saved).bind(bot)
        await restored.approve()
        assert rec.action == "accept_group_request"
        assert rec.params == {
            "group_id": 1,
            "notification_seq": 7,
            "notification_type": "invited_join_request",
            "is_filtered": True,
        }

    scenario(body)
