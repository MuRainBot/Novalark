"""运行时：Task 三用法、死锁保护、sync/async 分派、contextvars 传播。"""

from __future__ import annotations

import asyncio
import contextvars
import gc

import pytest

from novalark.runtime import TaskDeadlockError, run_async, task


async def _add(a: int, b: int) -> int:
    await asyncio.sleep(0.01)
    return a + b


def _mul(a: int, b: int) -> int:
    return a * b


def test_task_await(scenario):
    async def body():
        assert await task(_add(1, 2)) == 3
        assert await task(_add, 3, 4) == 7

    scenario(body)


def test_result_from_pool_thread(scenario):
    """池线程里 .result() 阻塞拿结果，不能死锁。"""

    async def body():
        def worker():
            return task(_add(10, 20)).result()

        assert await run_async(worker) == 30

    scenario(body)


def test_deadlock_protection(scenario):
    """循环线程上阻塞未完成的 Task 必须报错。"""

    async def body():
        handle = task(_add(7, 8))
        with pytest.raises(TaskDeadlockError):
            handle.result()
        assert await handle == 15  # 已完成的任务不误伤

    scenario(body)


def test_on_done_callback(scenario):
    async def body():
        seen: list[int] = []
        handle = task(_add(5, 6))
        handle.on_done(seen.append)
        await handle
        await asyncio.sleep(0.01)
        assert seen == [11]

    scenario(body)


def test_exception_propagates(scenario):
    async def body():
        async def boom():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            await task(boom())

    scenario(body)


def test_unbound_runtime_returns_failed_task():
    """运行时没绑定时返回 failed Task。"""
    from novalark.runtime import Runtime

    fresh = Runtime()

    async def coro():
        return 1

    handle = fresh.submit_coro(coro())
    with pytest.raises(RuntimeError, match="尚未绑定"):
        handle.result()


def test_submitted_coro_is_not_garbage_collected(scenario):
    """提交上去的协程必须被强引用住。

    asyncio 内部只对 Task 持**弱**引用（``asyncio.all_tasks`` 就是个
    ``WeakSet``）。``create_task`` 的返回值没人接的话，一个挂在裸 Future
    上的 Task 会连着自己的引用环一起被 GC 回收，结果凭空消失 —— handle
    只会收到一个 GeneratorExit。
    """

    async def body():
        from novalark.runtime import runtime

        started = asyncio.Event()

        async def hang():
            started.set()
            await asyncio.Future()  # 只被本协程帧引用：纯引用环

        handle = runtime.submit_coro(hang())
        await asyncio.wait_for(started.wait(), 1)

        gc.collect()
        await asyncio.sleep(0)

        assert not handle.done()  # 被回收的话这里会拿到 GeneratorExit

        for item in asyncio.all_tasks():
            if item is not asyncio.current_task():
                item.cancel()
        await asyncio.sleep(0)

    scenario(body)


def test_run_async_dispatches_both(scenario):
    async def body():
        assert await run_async(_mul, 6, 7) == 42
        assert await run_async(_add, 8, 9) == 17

    scenario(body)


def test_contextvars_cross_thread(scenario):
    """同步 handler 在线程池里要读得到调用方的 contextvars。"""
    probe = contextvars.ContextVar("probe", default="unset")

    async def body():
        probe.set("from-loop")

        async def read():
            return probe.get()

        assert await run_async(read) == "from-loop"

    scenario(body)
