import inspect
import threading
import time
import typing
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from time import sleep

import anyio
import anyio.abc

# 一个哨兵对象，用于区分“未设置”和值为None的情况
_SENTINEL = object()

# --- 全局变量定义 ---

# 1. 用于 ThreadTask 的全局线程池
# 使用max_workers=None，它会根据CPU核心数自动设置一个合理的线程数
_GLOBAL_THREAD_POOL: Executor = ThreadPoolExecutor(max_workers=None)

# 2. 用于 AsyncTask 的全局 AnyIO 任务组
# 它将在一个异步上下文中被设置
_GLOBAL_TASK_GROUP: typing.Optional[anyio.abc.TaskGroup] = None


@asynccontextmanager
async def global_task_group():
    """一个异步上下文管理器，用于创建并设置全局的 anyio.TaskGroup。"""
    global _GLOBAL_TASK_GROUP
    if _GLOBAL_TASK_GROUP is not None:
        raise RuntimeError("Global TaskGroup is already active.")

    try:
        async with anyio.create_task_group() as tg:
            _GLOBAL_TASK_GROUP = tg
            yield tg
    finally:
        _GLOBAL_TASK_GROUP = None


# --- 任务基类 (您提供的代码) ---

class Task:
    """任务基类，定义了任务的基本接口和状态管理。"""

    def __init__(self, func: typing.Callable):
        if not callable(func):
            raise TypeError("func must be a callable")
        self.func = func
        self.lock = threading.Lock()
        self.args: typing.Optional[tuple] = ()
        self.kwargs: typing.Optional[dict] = {}

    def payload(self, *args, **kwargs) -> 'Task':
        """设置任务执行时所需的参数。"""
        self.args = args
        self.kwargs = kwargs
        return self

    def submit(self, *args, **kwargs) -> 'Task':
        """
        提交任务以准备执行。

        如果已有任务在进行，会报错RuntimeError。
        子类需要重写此方法以实现具体的提交逻辑。
        """
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("Task has already been submitted.")

        # 允许在submit时直接传递参数，覆盖payload设置的参数
        if args or kwargs:
            self.payload(*args, **kwargs)

        return self

    async def wait(self):
        """等待任务完成并返回结果，如果任务出错则抛出异常。"""
        raise NotImplementedError()

    @property
    def done(self) -> bool:
        """检查任务是否已完成。"""
        raise NotImplementedError()

    @property
    def submitted(self) -> bool:
        """检查任务是否已提交。"""
        return self.lock.locked()


# --- 子类实现 ---

class ThreadTask(Task):
    """
    使用全局线程池执行同步函数的任务。
    """

    def __init__(self, func: typing.Callable):
        super().__init__(func)
        if inspect.iscoroutinefunction(func):
            raise TypeError("ThreadTask only accepts synchronous functions.")
        self._future: typing.Optional[Future] = None

    def submit(self, *args, **kwargs) -> 'ThreadTask':
        """使用全局线程池提交任务。"""
        super().submit(*args, **kwargs)
        # 将任务提交到全局线程池，并保存返回的Future对象
        self._future = _GLOBAL_THREAD_POOL.submit(self.func, *self.args, **self.kwargs)
        return self

    async def wait(self) -> typing.Any:
        """
        异步地等待任务完成并返回结果。
        它使用 anyio.to_thread.run_sync 来等待阻塞的 Future.result() 调用，
        这样就不会阻塞事件循环。
        """
        if not self._future:
            raise RuntimeError("Task has not been submitted yet.")

        # Future.result() 是一个阻塞操作，我们用anyio的工具在线程中运行它
        # 这样主事件循环就不会被阻塞
        return await anyio.to_thread.run_sync(self._future.result)

    @property
    def done(self) -> bool:
        """检查任务是否已完成。"""
        return self._future.done() if self._future else False


class AsyncTask(Task):
    """
    使用全局的 anyio.TaskGroup 执行异步或同步函数的任务。
    """

    def __init__(self, func: typing.Callable):
        super().__init__(func)
        self._result: typing.Any = _SENTINEL
        self._error: typing.Optional[Exception] = None
        self._done_event: typing.Optional[anyio.Event] = None

    def submit(self, *args, **kwargs) -> 'AsyncTask':
        """使用全局的 anyio.TaskGroup 提交任务。"""
        super().submit(*args, **kwargs)
        if _GLOBAL_TASK_GROUP is None:
            raise RuntimeError(
                "AsyncTask must be submitted within a `global_task_group` context."
            )

        self._done_event = anyio.Event()
        _GLOBAL_TASK_GROUP.start_soon(self._run_and_store_result)
        return self

    async def _run_and_store_result(self):
        """一个内部包装函数，用于执行实际的任务并保存结果或异常。"""
        try:
            # 如果是协程函数，直接 await
            self._result = await self.func(*self.args, **self.kwargs)
        except Exception as e:
            self._error = e
        finally:
            # 标记任务已完成
            self._done_event.set()

    async def wait(self) -> typing.Any:
        """等待任务完成并返回结果或抛出异常。"""
        if not self._done_event:
            raise RuntimeError("Task has not been submitted yet.")

        # 等待完成事件
        await self._done_event.wait()

        # 如果有错误，则抛出
        if self._error:
            raise self._error

        return self._result

    @property
    def done(self) -> bool:
        """检查任务是否已完成。"""
        return self._done_event.is_set() if self._done_event else False


def task(func):
    def wrapper(*args, **kwargs):
        if inspect.iscoroutinefunction(func):
            _task = AsyncTask(func)
        else:
            _task = ThreadTask(func)
        _task.submit(*args, **kwargs)
    return wrapper


def sync_blocking_task(duration, name):
    """一个模拟阻塞IO或CPU计算的同步函数"""
    print(f"[{time.monotonic():.2f}] 同步任务 '{name}' 开始，将阻塞 {duration} 秒。")
    time.sleep(duration)
    result = f"同步任务 '{name}' 完成！"
    print(f"[{time.monotonic():.2f}] {result}")
    return result


async def async_non_blocking_task(duration, name):
    """一个模拟异步IO操作的异步函数"""
    print(f"[{time.monotonic():.2f}] 异步任务 '{name}' 开始，将等待 {duration} 秒。")
    await anyio.sleep(duration)
    result = f"异步任务 '{name}' 完成！"
    print(f"[{time.monotonic():.2f}] {result}")
    return result


async def main():
    """主函数，演示如何使用 Task 类"""
    start_time = time.monotonic()
    print(f"[{start_time:.2f}] Main 程序开始。")

    # 异步任务必须在此上下文管理器中提交
    async with global_task_group():
        # === 示例 1: 执行一个同步任务 ===
        print("\n--- 提交一个 ThreadTask ---")
        # 1. 创建任务实例
        t1 = ThreadTask(sync_blocking_task)
        # 2. 提交任务并传入参数
        t1.submit(2, "计算")
        print(f"[{time.monotonic():.2f}] ThreadTask '计算' 已提交，主程序继续执行...")

        # === 示例 2: 执行一个异步任务 ===
        print("\n--- 提交一个 AsyncTask ---")
        # 1. 创建任务实例
        t2 = AsyncTask(async_non_blocking_task)
        # 2. 提交任务并传入参数
        t2.submit(3, "网络请求")
        print(f"[{time.monotonic():.2f}] AsyncTask '网络请求' 已提交，主程序继续执行...")

        # === 示例 3: 检查任务状态 ===
        # 此时任务正在后台运行，主程序没有被阻塞
        await anyio.sleep(1)  # 等待一秒钟
        print(f"\n[{time.monotonic():.2f}] 一秒后检查状态:")
        print(f"  - 任务'计算'是否完成? {t1.done}")  # 应该还是 False
        print(f"  - 任务'网络请求'是否完成? {t2.done}")  # 应该还是 False

        # === 示例 4: 等待任务完成并获取结果 ===
        print("\n--- 等待所有任务完成 ---")
        # await 会异步地等待，直到任务完成
        result1 = await t1.wait()
        print(f"[{time.monotonic():.2f}] 获取到 ThreadTask 的结果: '{result1}'")

        result2 = await t2.wait()
        print(f"[{time.monotonic():.2f}] 获取到 AsyncTask 的结果: '{result2}'")

    end_time = time.monotonic()
    print(f"\n[{end_time:.2f}] Main 程序结束。总耗时: {end_time - start_time:.2f} 秒。")
    # 注意：总耗时约等于最长的任务耗时（3秒），而不是所有任务耗时之和（2+3=5秒），证明了并发执行。


# 使用 anyio 运行主异步函数
if __name__ == "__main__":
    try:
        anyio.run(main)
    finally:
        # 优雅地关闭全局线程池
        _GLOBAL_THREAD_POOL.shutdown(wait=True)
