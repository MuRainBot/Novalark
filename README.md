# Novalark

**轻量、多协议、asyncio 的 QQ 机器人框架。**

一套 QQ 语义模型，各协议的适配器把协议投影上来——插件只对着模型写，
不用管跑的是 OneBot V11 还是 Milky。

```python
from novalark import At, GroupMessageEvent, RichText, ToMe, on, on_command, OptionalArg, TextArg

@on(GroupMessageEvent, depends=[ToMe()])
async def hello(event: GroupMessageEvent):
    await event.reply(RichText(At(event.sender.user_id), " 在的"))

@on_command("echo", OptionalArg(TextArg("text")))      # /echo 或 @机器人 echo
async def echo(event: GroupMessageEvent, text: str | None = None):
    await event.reply(text or "…")
```

## 快速开始

需要 Python 3.12+。

```bash
pip install -e .
python main.py
```

事件与状态按工作目录组织：配置在 `config.yaml`，插件在 `plugins/`，
日志在 `logs/`，运行期数据在 `data/`。

## 配置

`config.yaml`，一次连一个实现端：

```yaml
core:
  log_level: INFO          # 控制台级别；文件始终记 DEBUG
  thread_pool_size: 16
  command_starts: ["/"]    # 命令起始符，见下

adapter:
  type: milky              # 或 onebot_v11
  base_url: http://127.0.0.1:3010

plugins:
```

| 适配器 | 配置项 |
| --- | --- |
| `onebot_v11` | `host` / `port`（接收上报）、`api_url`（调 API）、`access_token` |
| `milky` | `base_url`（同址 `/api` 收调用、`/event` 推事件）、`access_token` |

## 命令

命令默认要带起始符（`/`），群里也可以直接 **@机器人** 代替：

| 用户发的 | 触发 |
| --- | --- |
| `/echo 你好` | ✅ |
| `@机器人 echo 你好` | ✅ |
| `@机器人 /echo 你好` | ✅ |
| `echo 你好` | ❌ |

解析前会自动剥掉消息开头的 `@机器人` 和引用回复段，所以这三样可以随意组合、
顺序无关。私聊同样要带起始符。

起始符是一组字符串，在 `core.command_starts` 里改：

```yaml
core:
  command_starts: ["/", "!"]   # /ping 和 !ping 都认
  # command_starts: []         # 关掉：命令只能靠 @机器人 触发
  # command_starts: [""]       # 裸词模式：ping 直接触发（等同于不带起始符）
```

单个命令可以覆盖：

```python
@on_command("ping", starts="!")            # 这条只认 !ping 或 @机器人 ping
@on_command("ping", at=False)              # 不认 @机器人，必须带起始符
@on_command("ping", reply=False)           # 命令前不许带引用回复
```

剥离只影响匹配，`event.content` / `event.plain_text` 始终是**原始消息**。
想在 handler 里自己处理，可以用 `event.content.strip_leading(At, Reply, starts="/")`。

## 写插件

放进 `plugins/`，单文件或包都行，启动时自动加载。元信息写在模块级
`__plugin__` 里（dict 字面量，不执行代码就能读出来）：

```python
from novalark import PluginMeta, get_logger

__plugin__: PluginMeta = {
    "author": "me",
    "version": "1.0.0",
    "description": "示例",
}

logger = get_logger("example")
```

依赖是普通函数或类，不匹配就 `raise NotMatched`；作用域让一组订阅共享门禁，
运行期可随时开关：

```python
from novalark import FromGroup, IntArg, NotMatched, Scope, depends

@depends
def is_admin(event) -> None:
    """不是群管理就不匹配。"""
    if not event.sender.is_admin:
        raise NotMatched

admin = Scope(prefix="admin", depends=[FromGroup(114514), is_admin])

@admin.command("mute", IntArg("seconds"))
async def mute(event, seconds):
    """admin mute 60"""
    ...

admin.enabled = False        # 整组关掉，不用重新注册
```

## 项目结构

```
novalark/
├── model/      QQ 语义模型（协议无关）
├── matcher/    订阅、依赖、作用域、分发
├── command/    命令模板与解析
├── plugin/     插件发现、加载、配置
├── adapter/    协议适配器（onebot_v11 / milky）
└── runtime/    原生 asyncio 运行时
```

## 许可

LGPL-2.1。项目仍在早期，API 可能大幅变动。
