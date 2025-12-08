import functools
import inspect
import threading
import typing
from contextlib import asynccontextmanager
import contextvars
from typing import Callable

import anyio
import anyio.from_thread
import anyio.abc

_SENTINEL = object()

_TASK_GROUP_CTX: contextvars.ContextVar[typing.Optional[anyio.abc.TaskGroup]] = contextvars.ContextVar(
    '_TASK_GROUP_CTX', default=None
)


@asynccontextmanager
async def task_group_context():
    async with anyio.create_task_group() as tg:
        token = _TASK_GROUP_CTX.set(tg)
        try:
            yield tg
        finally:
            _TASK_GROUP_CTX.reset(token)


class Task:
    """
    一个可以智能执行异步或同步函数的任务。
    """

    def __init__(self, func: typing.Callable):
        if not callable(func):
            raise TypeError("func must be a callable")
        self.func = func
        self._is_async = inspect.iscoroutinefunction(func)

        # 状态变量
        self.args: tuple = ()
        self.kwargs: dict = {}
        self._submitted = False
        self.task_callback = None
        self._result: typing.Any = _SENTINEL
        self._error: typing.Optional[Exception] = None
        self._done_event = anyio.Event()
        self._done_event_sync = threading.Event()

    def payload(self, *args, **kwargs) -> 'Task':
        """设置任务执行时所需的参数。"""
        if self._submitted:
            raise RuntimeError("Cannot set payload after task has been submitted.")
        self.args = args
        self.kwargs = kwargs
        return self

    def submit(
            self,
            *args,
            task_callback: Callable[['Task'], ...] | Callable[['Task', ...], ...] = None,
            **kwargs
    ) -> 'Task':
        """
        使用上下文中的 anyio.TaskGroup 提交任务。
        """
        if self._submitted:
            raise RuntimeError("Task has already been submitted.")

        if args or kwargs:
            self.payload(*args, **kwargs)

        self._submitted = True

        tg = _TASK_GROUP_CTX.get()
        if tg is None:
            raise RuntimeError(
                "Task must be submitted within a `task_group_context`."
            )

        if task_callback:
            self.task_callback = Task(task_callback)

        try:
            anyio.get_current_task()
            in_loop = True
        except RuntimeError:
            in_loop = False

        if in_loop:
            tg.start_soon(self._run_and_store_result)
        else:
            anyio.from_thread.run_sync(tg.start_soon, self._run_and_store_result)

        return self

    def __await__(self):
        return self.wait().__await__()

    async def _run_and_store_result(self):
        """内部包装函数，智能执行任务并保存结果。"""
        try:
            if self._is_async:
                self._result = await self.func(*self.args, **self.kwargs)
            else:
                context = contextvars.copy_context()
                self._result = await anyio.to_thread.run_sync(
                    context.run, self.func, *self.args, **self.kwargs
                )
        except Exception as e:
            self._error = e
        finally:
            if self.task_callback:
                self.task_callback.submit(self)

            self._done_event.set()
            self._done_event_sync.set()

    async def wait(self, timeout: typing.Optional[float] = None) -> typing.Any:
        """等待任务完成并返回结果，如果任务出错则抛出异常。"""
        if not self._submitted:
            raise RuntimeError("Task has not been submitted yet.")

        with anyio.move_on_after(timeout) as cancel_scope:
            await self._done_event.wait()

        if cancel_scope.cancel_called:
            raise TimeoutError

        if self._error:
            # 保持原始堆栈信息
            raise self._error
        return self._result

    def wait_sync(self, timeout: typing.Optional[float] = None) -> typing.Any:
        is_not_timeout = self._done_event_sync.wait(timeout)
        if not is_not_timeout:
            raise TimeoutError
        if self._error:
            raise self._error
        return self._result

    @property
    def done(self) -> bool:
        """检查任务是否已完成。"""
        return self._done_event.is_set()

    @property
    def submitted(self) -> bool:
        """检查任务是否已提交。"""
        return self._submitted


def task(func: typing.Callable) -> typing.Callable[..., Task]:
    """
    一个装饰器，将函数包装成一个Task工厂。
    调用被装饰的函数会立即提交任务并返回Task对象。
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> Task:
        return Task(func).submit(*args, **kwargs)

    return wrapper


def await_func(func: typing.Callable):
    """
    一个装饰器，将函数包装成一个await版本。
    """
    if inspect.iscoroutinefunction(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs)
    else:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            context = contextvars.copy_context()
            return await anyio.to_thread.run_sync(context.run, func, *args, **kwargs)
    return wrapper
