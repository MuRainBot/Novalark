# Novalark

轻量、多协议、asyncio 的 QQ 机器人框架。一套 QQ 语义模型，OneBot V11 和 Milky
两个适配器各自把协议**投影**上来，写插件时不用管对面跑的是哪个协议。

```bash
python main.py                                    # 启动
python -m pytest -q                               # 测试
python -m pyright                                 # 类型检查
```

## 分层

```
model/       QQ 语义模型（协议无关）
runtime/     原生 asyncio：Task 三用法
matcher/     订阅、依赖、作用域、分发
command/     命令模板与解析
plugin/      插件发现、加载、配置
adapter/     协议适配器（onebot_v11 / milky）
```

依赖方向单向：`adapter → matcher → model`。`model` 不依赖任何上层，
`adapter` 之间互不引用。

## 定下的原则

这些是反复推敲后确定的，改动前先想清楚代价。

### 模型层

**一套模型，不是跨平台抽象。** 目标是 QQ 这一个 IM 的语义，不是"通用 IM"。
抽象成最小公约数会丢掉 QQ 的一切（群名片、群头衔、临时会话），那些才是
这个框架存在的理由。

**字段取交集进基类，协议特有的按形状分流；需要请求才能拿到的走方法。**
- 交集（所有协议零成本给）→ 基类字段。`GroupEvent.group_id`。
- 只有部分协议给、且是**标量**（数字 / 字符串 / 布尔 / 枚举）→ 也进基类，
  用中性默认值。`GroupRecallEvent.operator_id`（只有 Milky 给，OneBot 上是
  0）、`Xml.service_id`（`None`）。判据是**默认值能不能无歧义地表示「没给」**
  —— QQ 号不会是 0、空串不是签名，所以 `0` / `""` 携带的正好是这个语义。
- 只有部分协议给、且是**结构化**的（实体 / 列表）→ 下沉协议子类。它没有
  中性默认值：`None` 分不开「协议给了但为空」和「协议根本没这个概念」。
- 要发请求的 → 方法，基类给慢实现、子类重写快的。`fetch_group()`、
  `MediaSegment.get_url()`。

标量那条是有意为之：插件的 handler 写 `@on(GroupRecallEvent)`，一份代码吃
两家事件，读 `event.operator_id` 时不该先 `isinstance` 判断协议。把这种字段
下沉成子类，等于把下面那条「让用户不必判断类型」废掉。

**让用户不必判断类型**：能用默认值中性化的字段直接进基类（`Face.is_large`
在 OneBot 上是 `False`，语义正好），不能的用方法抹平（`Forward.fetch()`
统一引用和内容两种形态）。

**并集而非交集地覆盖段和事件类型。** 只有一家有的段是**独立的类**
（`Markdown` / `MarketFace` 是 Milky 的，`Poke` / `Share` / `Music` 是
OneBot 的），不是谁的子类 —— 它们是不同的概念，不是「同一个东西多了几个
字段」，后者归上面那条管。

未知类型降级为 `UnknownSegment` / `UnknownEvent` 并**保留原文**，不做不可逆
的文本降级。`extra="allow"` 让协议扩展字段留着。

段名有**两张表**要同时认：decoder 里的 `match` 分支、和模型层的
`_SEGMENT_REGISTRY`（反序列化用）。漏了一边，同一个段从事件收来是 `At`、
把 dict 存下来读回来就成了 `UnknownSegment` —— Milky 的 `mention_all` 这么
漏过。`tests/test_cross_protocol.py` 的 round-trip 用例盯着这个。

**一个概念一个类。** 「别人申请进群」和「机器人被邀请进群」是两个事件类
（`GroupJoinRequestEvent` / `GroupInviteRequestEvent`），插件的 `@on` 直接
写在类上，handler 里不用再判断类型。共有的部分用共同基类，不用一个布尔
字段把两件事挤进同一个类。

**`Segment.type` 是语义名，不是协议字段名。** 由段类自己声明（`At` 恒为
`"at"`，哪怕 Milky 那边叫 `mention`），基类的 validator 会把协议塞进来的值
强制回写掉。判断段类型直接比 `type` 或用 `isinstance`，跨协议结果一致。
协议原文进 `raw`（`exclude=True`，不参与序列化），和 `Event.raw` 对称。

**段的声明字段容忍协议的类型抖动。** `coerce_numbers_to_str` 把数字归一成
字符串，`null` 当作「没给」走默认值。一个数字字段不该让整条消息变成
`UnknownEvent` —— 那对插件就是「事件凭空消失了」。

**消息主键是 `(conversation, seq)`**，不是不透明 ID。Milky 定位消息要
scene+peer+seq 三个参数，把会话坐标塞进全局句柄必然错路由。

**模型层对协议字段名零知识。** 字段名映射（`face_id`→`id`、`json_payload`
→`payload`）写在各自的 `decode.py` 里。两家长得像的映射表也**各写各的**——
长得像是巧合，不是可以共用的抽象。

### matcher

**一层订阅。** `@on` 是唯一入口，事件分发和条件匹配是同一套机制、一个
priority 轴。

**一个概念：`Depend`。** `depends=[...]` 是门禁（返回值丢弃），
`Depends(x)` 是注入（绑定到写明的那个参数）。不按类型猜，也不按参数名。

**命令独占。** 一条消息只触发一条命令（先匹配到的赢），但普通订阅照跑。
被独占挡下时会记 debug 日志，否则"我的命令怎么不响应"无从查起。

### command

**对象化模板，不用 f-string。** `Cmd("ban", AtArg("target"))`。f-string 会
强制 `str()`，是 obj→str→obj 那条烂路的根源。

**解析失败用返回值，不用异常。** `Ok(value, rest)` / `Fail(reason)`。异常
留给真正的错误。这是回溯和错误报告能共存的前提。

**解析失败必须说出去。** `Fail.reason` 攒出来就是为了给人看的：命令词对上
了但参数不对 → warning + 自动回一条用法提示（`core.command_hint` 控制开关，
`set_miss_handler` 可以整体替换）。有命令订阅在盯着但一条都没认领 → 记一条
info。默认控制台是 INFO，用户和开发者都得当场看见，而不是去翻文件日志。

**必须消费完所有 token。** 否则 `ban @某人 理由` 会被 `Cmd("ban")` 抢先
认领——它自己解析是成功的。

**字面量比参数具体。** 同优先级内先试字面量多的模板，否则贪婪参数会把
`ban all` 吃掉，`Cmd("ban", "all")` 永远轮不到。

**前缀自动剥离，不做成 depend。** 解析前剥掉消息开头的 `@机器人` 段、引用
回复段和命令起始符，三样各剥一个、顺序无关。有 `@机器人` 就免起始符，没有就
必须带（私聊同理）。起始符是一组字符串（`core.command_starts`，默认 `/`）：
空集合 = 关掉，`{""}` = 裸词模式 —— 空串让 `startswith` 恒真，不需要特例分支。
剥离只动解析用的 token，`event.content` 保持原始，`ToMe` 那类和插件都照旧能
拿到用户原话。

### adapter

**`call_raw` 是所有请求的唯一出口。** 日志记在那儿，上面包多少层都漏不掉；
放在 `Bot` 上会漏掉适配器内部的语义化动作（发消息、撤回…）。

**`call` 建在 `call_raw` 之上**：拆封 + 把异常折成失败结果，所以它不抛。
想抛异常用 `unwrap()`，要原始行为用 `call_raw`。

**语义化动作失败一律抛 `ActionError`。** 它们是给插件用的高层接口，返回的是
语义值（`MessageRef` / `Group` / `None`）而不是 `ActionResult`。一半抛异常
一半静默，调用方就写不出统一的错误处理 —— 权限不足时的「静默成功」比报错
更贵。做不到的能力抛 `UnsupportedAction`。

**全局 `get_bot()` 在 `start()` 连上之后才登记**，`stop()` 时注销。在
`__init__` 里登记会让它在连上之前就返回一个 `self_id` 还是 0 的假 bot。
媒体段的 `_resolve` 和 `Forward._load` 靠它拿到适配器。

**每个适配器自己解析响应信封。** 基类对「响应长什么样」零假设。OneBot 和
Milky 的信封恰好一样，那是巧合。

**不注入 emitter。** 直接调分发器。运行期注入唯一的效果是制造一个可以
忘记的步骤——忘了就静默丢弃所有事件。

**「只有一方有」的能力直接用那一方的定义**，不强行抽象。

**协议多给的参数别丢：写在 adapter 的方法签名上，带默认值。** 基类取交集，
需要它的实现端才声明它；`Bot`（插件实际调的那层）只暴露交集 —— 除非整个
能力就只有一家有，那按上一条直接用那一方的定义。参数分两类：

- **能力参数**（改变「能不能做到」）—— 不支持就**报错**。静默忽略会让调用方
  以为生效了。`get_history(start_seq=...)`。
- **提示参数**（只影响实现端内部策略，最坏是拿到略旧的数据）—— 不支持可以
  忽略。`no_cache` 进基类签名，认不认看实现端。两家的 `no_cache` 覆盖面本来
  就不一样（Milky 6 个接口、OneBot 3 个），挨个对齐没意义。

**返回值和参数一样，装不下的东西别用裸 list。** 一个响应里给多个列表、或
带回一个游标时 `list[X]` 装不下，多出来的部分会无声消失 ——
`get_group_files` 丢过文件夹列表，`get_history` 丢过翻页游标。而且这是个
**成对**的模式：两家都用「入参 `start_*_seq` + 出参 `next_*_seq`」给翻页，
只接一头就永远停在第一页（`get_history_messages` 和
`get_group_notifications` 都中过）。装进响应对象：`MessagePage`、
`GroupFileList`。

**协议字段名 → 模型字段名的映射收在 adapter 的 `_to_xxx` 一处。** 直接
`GroupFile.model_validate(协议原文)` 看着省事，字段名一旦不同（Milky 的
`file_name` / `file_size` / `group_name`）值就静默掉进 `model_extra`：字段
看着有、读出来是空的。这种「不报错、只是空」比直接抛异常难查得多。

**顺序也是契约的一部分。** `get_history` 声明「从新到旧」，Milky 给的是
`message_seq` 升序 —— 拿回来必须翻一遍。声明和实现对不上时，
`page[0]` 拿到的是最旧那条，而测试通常只断言了条数。

### plugin

**归属一律取「定义期」信息，不翻栈帧。** 函数看 `fn.__module__`，配置类看
`cls.__module__`，元信息由 `PluginManager` 从模块对象上读 `__plugin__`。

翻栈帧是拿运行期手段倒推定义期信息，调用路径一变（包一层、动态导入）就
断。`fn.__module__` 是 Python 编译期写进函数对象的，零成本且精确。

**元信息静态可读。** `__plugin__: PluginMeta = {...}` 是 dict 字面量，
`ast.literal_eval` 就能读——列插件清单不用把所有插件执行一遍。

### runtime

**原生 asyncio。** 不要 anyio、不要 TaskGroup、不要全局 `_TASK_GROUP_CTX`。
`Runtime` 显式持有 loop，这是上下文变量不需要存在的根本原因。

**`Task` 三用法**：`await task` / `task.result()` / `task.on_done(cb)`。
在 loop 线程上 `.result()` 抛 `TaskDeadlockError`（会冻住整个 loop）。

**API 是普通 `async def`**，返回协程（惰性）。`await` 顺序执行，
`task(...)` 显式并发。

### 日志

**logger 不过滤，级别交给 handler。** logger 恒为 DEBUG，控制台按配置显示，
文件记全量。否则 `log_level: INFO` 时文件里也拿不到 DEBUG。

**业务日志挂 `call_raw` 和 `dispatch`。** 协议细节（请求与响应的原文）留在
`call_raw`，恒为 debug。给人看的那条走 info，集中在两处：进来的事件在
`dispatch`（`notable` 为假的心跳之类降到 debug），出去的动作在 `Bot` 的
`send` / `reply`。`event.summary` 和 `ConversationRef.display` 负责把它们说成
人话 —— 控制台上要看得到"谁在哪个群说了什么、机器人回了什么"，而不是
`send_group_msg {...}`。

## 约定

不要在代码、注释、文档中包含过程中的描述，而不提及草稿、版本、审查轮次、先前措辞、被取代的决定或编辑过程，除非用户明确要求变更日志、历史记录或决策记录。

**测试要能独立跑。** 不依赖开发机上的 `config.yaml`，不抢固定端口
（要端口用 `conftest.py` 的 `free_port`）。

**跨协议一致性有专门的回归网。** `tests/test_cross_protocol.py` 逐条断言同一
个语义在两家 decode 之后落到同一个段类、同一个 `type` —— 「模型层对协议字段
名零知识」这条原则坏掉时，只有那里会响。
