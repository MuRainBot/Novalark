from abc import ABC, abstractmethod
from .event_manager import CancellableEvent
from .rich_text import RichText
from ..adapters import Adapter


class Bot(ABC):
    def __init__(self, bot_id: str, adapter: Adapter):
        self._id = bot_id
        self._adapter = adapter
        from .bot_manager import bot_manager
        bot_manager.register(self)

    @abstractmethod
    async def clean_up(self):
        ...

    @property
    def id(self):
        return self._id

    @property
    def adapter(self):
        return self._adapter


class PlatformEvent(CancellableEvent, ABC):
    def __init__(self):
        super().__init__()

    @property
    @abstractmethod
    def bot(self) -> Bot:
        ...


class Context(ABC):
    @property
    @abstractmethod
    def id(self) -> str: ...

    @abstractmethod
    def __hash__(self) -> int: ...

    @abstractmethod
    def __eq__(self, other: 'Context') -> bool: ...


class MessageReceipt(ABC):
    @property
    @abstractmethod
    def context(self) -> Context:
        ...

    @property
    @abstractmethod
    def id(self) -> str:
        ...

    def __str__(self):
        return self.id

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        if isinstance(other, MessageReceipt):
            return self.id == other.id
        elif isinstance(other, str):
            return self.id == other
        else:
            return False


class Message(ABC):
    @property
    @abstractmethod
    def receipt(self) -> MessageReceipt:
        ...

    @property
    def context(self) -> Context:
        return self.receipt.context

    @property
    @abstractmethod
    def content(self) -> RichText:
        ...


class MessageEvent(PlatformEvent, ABC):
    @property
    @abstractmethod
    def message(self) -> Message:
        ...

    @property
    @abstractmethod
    def context(self) -> Context:
        ...


class User(ABC):
    @property
    @abstractmethod
    def id(self) -> str:
        ...

    @classmethod
    @abstractmethod
    async def from_id(cls, user_id: str) -> 'User':
        ...

    @abstractmethod
    async def get_profile(self) -> "UserProfile":
        ...

    def __str__(self):
        return self.id

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        if isinstance(other, User):
            return self.id == other.id
        elif isinstance(other, str):
            return self.id == other
        else:
            return False


class UserProfile(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        ...
