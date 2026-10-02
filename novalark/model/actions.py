"""API 调用的返回封装。

Result 模式：失败不抛异常，需要异常语义时用 ``unwrap()`` / ``expect()``。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .enums import ActionStatus


class ActionError(Exception):
    """动作调用失败（``unwrap`` / ``expect`` 时抛出）。"""


class ActionResult(BaseModel):
    """一次动作调用的结果。判断成功只看 ``status``。"""

    status: ActionStatus = ActionStatus.OK
    retcode: int = 0
    data: Any = None
    message: str = ""

    @property
    def is_ok(self) -> bool:
        return self.status == ActionStatus.OK

    def unwrap(self) -> Any:
        """成功返回 data；失败抛 ``ActionError``。"""
        if not self.is_ok:
            raise ActionError(f"动作失败: {self.message} (retcode={self.retcode})")
        return self.data

    def expect(self, message: str) -> Any:
        """成功返回 data；失败抛 ``ActionError``（自定义错误信息）。"""
        if not self.is_ok:
            raise ActionError(f"{message}: {self.message} (retcode={self.retcode})")
        return self.data

    def __bool__(self) -> bool:
        return self.is_ok
