import typing
from functools import total_ordering
from typing import Callable

import anyio

from novalark.core.logger_lib import traceback_exception
from novalark.core.task import task, await_func, task_group_context

_event_listeners = {}

# class Priority(Enum):
#     HIGHEST = 0
#     HIGH = 1
#     NORMAL = 2
#     LOW = 3
#     LOWEST = 4

TEvent = typing.TypeVar("TEvent", bound=type['Event'])

_PRIORITY_MAPPING = {"HIGHEST": 0, "HIGH": 1, "NORMAL": 2, "LOW": 3, "LOWEST": 4}


class PriorityMeta(type):
    def __getattr__(cls, name: str):
        if name in _PRIORITY_MAPPING:
            priority_value = _PRIORITY_MAPPING[name]
            return cls(priority_value)

        raise AttributeError(f"type object '{cls.__name__}' has no attribute '{name}'")


@total_ordering
class Priority(metaclass=PriorityMeta):
    """
    事件的优先级，语法：Priority.等级，可嵌套，如：Priority.HIGHEST.HIGHEST
    """

    if typing.TYPE_CHECKING:
        HIGHEST: "Priority"
        HIGH: "Priority"
        NORMAL: "Priority"
        LOW: "Priority"
        LOWEST: "Priority"

    def __init__(self, value: int, parent: "Priority" = None):
        if not parent:
            self._value = (value,)
        else:
            self._value = (parent._value + (value,))

    def _get_value(self, length=None):
        if length is not None:
            assert len(self._value) <= length, "invalid length"
            return self._value + (_PRIORITY_MAPPING["NORMAL"],) * (length - len(self._value))
        return self._value

    def __getattr__(self, item: str):
        assert item in _PRIORITY_MAPPING, "invalid priority"
        return Priority(_PRIORITY_MAPPING[item], self)

    def __eq__(self, other):
        if not isinstance(other, Priority):
            return NotImplemented
        length = max(len(self._value), len(other._value))
        return self._get_value(length) == other._get_value(length)

    def __lt__(self, other):
        if not isinstance(other, Priority):
            return NotImplemented
        length = max(len(self._value), len(other._value))
        return self._get_value(length) < other._get_value(length)

    def __hash__(self):
        value = self._value
        normal_value = _PRIORITY_MAPPING["NORMAL"]

        # Remove trailing NORMAL values for hash consistency
        while value and value[-1] == normal_value:
            value = value[:-1]

        return hash(value)

    def __repr__(self) -> str:
        # Adding a __repr__ is good practice for debugging
        reverse_mapping = {v: k for k, v in _PRIORITY_MAPPING.items()}
        name_path = '.'.join(reverse_mapping.get(v, str(v)) for v in self._value)
        return f"Priority.{name_path}"


class Event:
    """
    基础事件类
    """

    @property
    def listeners(self) -> list["EventListener"]:
        ret = []
        for event_class, listeners in _event_listeners.items():
            if issubclass(self.__class__, event_class):
                ret.extend(listeners)
        ret.sort(key=lambda obj: obj.priority)
        return ret

    @task
    async def fire(self):
        """
        发送事件
        """
        for listener in self.listeners:
            await listener.call(self)


class CancellableEvent(Event):
    """
    一个可被中止的事件
    """

    def __init__(self):
        self.__is_cancelled = False

    def cancel(self):
        """
        中止该事件
        """
        self.__is_cancelled = True

    @property
    def listeners(self):
        for listener in super().listeners:
            yield listener
            if self.__is_cancelled:
                break


class EventListener:
    def __init__(
            self, callback: Callable[[TEvent], ...] | Callable[[TEvent, ...], ...],
            event: TEvent,
            priority: Priority = Priority.NORMAL
    ):
        self.__callback = await_func(traceback_exception(ignore_exception=True)(callback))
        assert isinstance(event, type) and issubclass(event, Event), "invalid event"
        self.__event = event
        self.__priority = priority

    async def call(self, event: Event, *args, **kwargs):
        """
        调用该监听器
        """
        return await self.__callback(event, *args, **kwargs)

    def set_priority(self, priority: Priority):
        """
        设置该监听器的优先级
        """
        assert isinstance(priority, Priority), "invalid priority"
        self.__priority = priority

    def subscribe(self):
        """
        注册一个事件
        """
        _event_listeners.setdefault(self.__event, []).append(self)

    def unsubscribe(self):
        """
        取消注册一个事件
        """
        assert (self.__event in _event_listeners
                and self in _event_listeners[self.__event]), "the listener has not been registered yet"
        _event_listeners[self.__event].remove(self)
        if not _event_listeners[self.__event]:
            _event_listeners.pop(self.__event)

    @property
    def priority(self) -> Priority:
        return self.__priority

    @property
    def event(self) -> type:
        return self.__event


def event_listener(event: TEvent, priority: Priority = Priority.NORMAL):
    """
    注册一个事件监听器
    :param event: 要监听的事件，注意，当该事件的子事件被出发时，该监听器也会被触发
    :param priority: 监听器的优先级
    :return: 监听器对象
    """
    def wrapper(func: Callable[[TEvent], ...] | Callable[[TEvent, ...], ...]) -> EventListener:
        listener = EventListener(func, event, priority)
        listener.subscribe()
        return listener

    return wrapper


if __name__ == "__main__":
    # print(Priority.HIGHEST.HIGHEST)
    # print(Priority.HIGHEST.LOW)
    # print(Priority.HIGHEST)
    # print(Priority.HIGHEST > Priority.HIGHEST.HIGHEST)

    class HelloEvent(Event):
        def __init__(self, name: str):
            self.name = name

    @event_listener(HelloEvent)
    async def test_listener(event: HelloEvent):
        print("Hello", event.name, "from async")

    @event_listener(HelloEvent)
    def test_listener2(event: HelloEvent):
        print("Hello", event.name, "from sync")

    async def _main():
        async with task_group_context():
            await HelloEvent("World").fire().wait()

    anyio.run(_main)
