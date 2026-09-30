"""Task：运行中（或已完成）操作的句柄。

继承 ``concurrent.futures.Future``：既能在事件循环里 ``await``，也能在线程池里
阻塞 ``.result()``。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
from typing import Any, Callable, Generator, Generic, ParamSpec, TypeVar

from ..log import get_logger

logger = get_logger("runtime.task")

P = ParamSpec("P")
T = TypeVar("T")


def running_in_loop() -> bool:
    """当前线程是否处在某个正在运行的事件循环里。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class TaskDeadlockError(RuntimeError):
    """在事件循环线程上阻塞一个未完成的 Task 时抛出。"""

    def __init__(self) -> None:
        super().__init__(
            "不能在事件循环线程上用 .result() 阻塞未完成的 Task —— "
            "整个 loop 会冻住，上面所有协程都停下。请改用 await。"
        )


class Task(concurrent.futures.Future, Generic[T]):
    """一个进行中（或已完成）操作的句柄。

    三种用法::

        await task                     # async —— 任意事件循环
        task.result()                  # sync  —— 任意无事件循环的线程
        task.on_done(lambda v: ...)    # callback

    第四种（不取结果）等价于 fire-and-forget；失败会由 ``__del__`` 记日志，
    不会静默丢失。
    """

    def __init__(self) -> None:
        super().__init__()
        self._retrieved = False

    def result(self, timeout: float | None = None) -> T:
        """阻塞直到完成并返回值。

        Raises:
            TaskDeadlockError: 在事件循环线程上等待**未完成**的任务。
                已完成的任务直接返回，不会误伤。
            TimeoutError: 超时。
            BaseException: 任务失败时的原始异常。
        """
        if not self.done():
            self._block()
        try:
            return super().result(timeout)
        except BaseException:
            self._retrieved = True
            raise

    def exception(self, timeout: float | None = None) -> BaseException | None:
        """阻塞直到完成并返回异常（或 None）。查看即算消费。"""
        if not self.done():
            self._block()
        try:
            return super().exception(timeout)
        finally:
            self._retrieved = True

    def _block(self) -> None:
        """未完成时被调用：按调用线程决定能否阻塞。

        - **事件循环线程**：抛 ``TaskDeadlockError``。
        - **其他线程**（线程池 / 普通线程）：交给 ``Future.result`` 正常等待。
        """
        if running_in_loop():
            raise TaskDeadlockError()

    def __await__(self) -> Generator[Any, None, T]:
        """``await task`` —— 每次新建 wrap_future，不绑定创建时的循环。"""
        return asyncio.wrap_future(self).__await__()

    def on_done(self, callback: Callable[[T], Any]) -> "Task[T]":
        """完成回调，收到的是结果值（不是 Future）。异常不走这里。"""

        def _wrapper(fut: concurrent.futures.Future) -> None:
            if not fut.cancelled() and fut.exception() is None:
                callback(fut.result())

        self.add_done_callback(_wrapper)
        return self

    def on_exception(self, callback: Callable[[BaseException], Any]) -> "Task[T]":
        """失败回调，收到的是异常对象。"""

        def _wrapper(fut: concurrent.futures.Future) -> None:
            if not fut.cancelled() and (exc := fut.exception()) is not None:
                callback(exc)

        self.add_done_callback(_wrapper)
        return self

    def __del__(self) -> None:
        """失败且无人取结果的 Task，记一条日志。"""
        try:
            if (
                self.done()
                and not self.cancelled()
                and not getattr(self, "_retrieved", False)
                and (exc := self.exception()) is not None
            ):
                logger.error(
                    "Task 失败但结果从未被取用 —— 请用 await / .result() / .on_done() "
                    "处理异常: %s",
                    type(exc).__name__,
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
        except Exception:
            pass

    # ── 构造器（供运行时使用） ──────────────────────────────

    @classmethod
    def from_future(cls, fut: concurrent.futures.Future) -> "Task[T]":
        """把一个已有的 Future 包装成 Task。"""
        handle: Task[T] = cls()

        def _propagate(f: concurrent.futures.Future) -> None:
            if f.cancelled():
                handle.cancel()
            elif (exc := f.exception()) is not None:
                handle.set_exception(exc)
            else:
                handle.set_result(f.result())

        fut.add_done_callback(_propagate)
        return handle

    @classmethod
    def done_with(cls, value: T) -> "Task[T]":
        handle: Task[T] = cls()
        handle.set_result(value)
        return handle

    @classmethod
    def failed_with(cls, exc: BaseException) -> "Task[Any]":
        handle: Task[Any] = cls()
        handle.set_exception(exc)
        return handle


def task(coro_or_fn: Any, *args: Any, **kwargs: Any) -> Task[Any]:
    """把协程（或函数调用）提交为并发任务。

    async 上下文::

        t = task(bot.send(conv, "hi"))      # 传协程
        t = task(bot.send, conv, "hi")      # 传函数 + 参数
        await t

    sync 上下文（线程池里）::

        task(event.reply("hi"))             # 不取结果
        task(event.reply("hi")).result()    # 阻塞拿结果
    """
    from .loop import runtime

    if inspect.iscoroutine(coro_or_fn):
        return runtime.submit_coro(coro_or_fn)
    return runtime.dispatch(coro_or_fn, *args, **kwargs)
