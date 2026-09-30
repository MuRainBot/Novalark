"""订阅层的控制流信号。"""

from __future__ import annotations

from enum import StrEnum


class NotMatched(Exception):
    """依赖的前提不成立。

    依赖用它表示「这个事件不满足条件」：门禁会跳过该订阅，注入依赖会跳过
    该 handler，``Or`` 会继续试下一个分支。框架把它和真正的异常分开处理。
    """


class PropagationScope(StrEnum):
    """阻断的作用范围。"""

    PLUGIN = "plugin"
    """只停本插件后面的订阅。"""

    GLOBAL = "global"
    """停所有订阅。"""


class StopPropagation(Exception):
    """停止事件继续传播。默认停全局，可指定只停本插件。"""

    def __init__(self, scope: PropagationScope = PropagationScope.GLOBAL) -> None:
        super().__init__(f"停止传播 ({scope.value})")
        self.scope = scope
