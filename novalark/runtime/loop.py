"""运行时：事件循环与线程池的持有者。

线程模型：事件循环跑在主线程，同步函数交给 ``run_async`` 扔进线程池，
contextvars 随 ``copy_context`` 传过去。

提交失败时返回 failed Task，不抛异常。
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from .task import Task

__all__ = ["Runtime", "runtime", "run_async"]


class Runtime:
    """事件循环 + 线程池的持有者。"""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pool: ThreadPoolExecutor | None = None
        self._closing = False
        #: 在飞的后台 Task 的强引用。见 :meth:`_spawn`。
        self._background: set[asyncio.Task[Any]] = set()

    # ── 生命周期 ───────────────────────────────────────────

    def bind(self, *, pool_size: int = 16) -> None:
        """在事件循环线程上绑定运行时。幂等。"""
        if self._loop is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._pool = ThreadPoolExecutor(pool_size, thread_name_prefix="novalark")

    async def shutdown(self) -> None:
        """停止接受新任务并关闭线程池。"""
        self._closing = True
        pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)

    def reset(self) -> None:
        """清掉绑定状态，让下一次 ``bind()`` 重新生效（测试用）。

        调用前先 ``await shutdown()``。
        """
        self._loop = None
        self._pool = None
        self._closing = False

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        return self._loop

    @property
    def pool(self) -> ThreadPoolExecutor | None:
        return self._pool

    def _unavailable(self) -> str | None:
        """返回不可用的原因；可用则返回 None。"""
        if self._closing:
            return "运行时正在关闭"
        if self._loop is None or self._loop.is_closed():
            return "运行时尚未绑定事件循环"
        return None

    # ── 提交 ───────────────────────────────────────────────

    def _spawn(self, coro: Any) -> None:
        """在事件循环线程上把协程变成 Task，并一路持有到它结束。

        asyncio 内部只对 Task 持**弱**引用（``asyncio.all_tasks`` 就是个
        ``WeakSet``），所以 ``create_task`` 的返回值必须有人接住。丢掉的话，
        一个挂在裸 Future 上的 Task 会连同它自己的引用环一起被 GC 回收，
        结果凭空消失 —— 事件循环只会打一行「Task was destroyed but it is
        pending!」。官方文档专门警告过这个模式。

        跑完自动从集合里摘掉，不积压。
        """
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def submit_coro(self, coro: Any) -> Task[Any]:
        """把一个协程提到事件循环上执行。"""
        handle: Task[Any] = Task()
        reason = self._unavailable()
        if reason is not None:
            # 显式关闭未提交的协程
            coro.close()
            handle.set_exception(RuntimeError(reason))
            return handle

        async def _run() -> None:
            try:
                result = await coro
                if not handle.cancelled():
                    handle.set_result(result)
            except asyncio.CancelledError:
                if not handle.cancelled():
                    handle.cancel()
                raise
            except BaseException as exc:  # noqa: BLE001
                if not handle.cancelled():
                    handle.set_exception(exc)

        assert self._loop is not None
        runner = _run()
        try:
            self._loop.call_soon_threadsafe(self._spawn, runner)
        except RuntimeError as exc:  # 循环在 _unavailable() 和这里之间关掉了
            runner.close()
            coro.close()
            handle.set_exception(exc)
        return handle

    def submit_async(
        self, fn: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> Task[Any]:
        """在事件循环上执行一个 async 函数。"""
        if not inspect.iscoroutinefunction(fn):
            return Task.failed_with(
                TypeError(f"{fn!r} 不是 async 函数，请用 submit_sync")
            )
        return self.submit_coro(fn(*args, **kwargs))

    def submit_sync(
        self, fn: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> Task[Any]:
        """在线程池里执行一个同步函数。"""
        handle: Task[Any] = Task()
        reason = self._unavailable()
        if reason is not None or self._pool is None:
            handle.set_exception(RuntimeError(reason or "运行时尚未绑定线程池"))
            return handle
        # 显式把调用方的 contextvars 带过去
        ctx = contextvars.copy_context()
        try:
            future = self._pool.submit(ctx.run, lambda: fn(*args, **kwargs))
        except RuntimeError as exc:  # 线程池已关闭
            handle.set_exception(exc)
            return handle
        return Task.from_future(future)

    def dispatch(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Task[Any]:
        """按 ``fn`` 是否 async 自动选路，统一返回 Task。"""
        if inspect.iscoroutinefunction(fn):
            return self.submit_async(fn, *args, **kwargs)
        return self.submit_sync(fn, *args, **kwargs)


runtime = Runtime()


async def run_async(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """零 Task 开销的 sync/async 分派，供框架内部使用（需在 async 上下文）。

    - async 函数：直接 await
    - sync 函数：扔线程池，并用 ``copy_context`` 把调用方的 contextvars
      带过去
    """
    if inspect.iscoroutinefunction(fn):
        return await fn(*args, **kwargs)

    pool = runtime.pool
    if pool is None:
        raise RuntimeError("运行时尚未绑定线程池，无法执行同步函数")

    ctx = contextvars.copy_context()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        pool, lambda: ctx.run(lambda: fn(*args, **kwargs))
    )
