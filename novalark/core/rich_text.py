from typing import Any


segments = {}


def register_segment(cls: 'Segment'):
    if cls.segment_type in segments:
        raise ValueError(f"{cls.segment_type}已存在")
    segments[cls.segment_type] = cls
    return cls


class Segment:
    segment_type = None

    def __init__(self, data: dict[str, Any]):
        self._data = data

    def __add__(self, other: 'Segment | RichText'):
        if isinstance(other, RichText):
            return RichText(self, *other.segments)
        if isinstance(other, Segment):
            return RichText(self, other)
        raise TypeError("只支持拼接Segment或RichText")

    def __repr__(self):
        return f"<Segment {self.segment_type} {self.data}>"

    @property
    def type(self):
        return self._data["type"]

    @type.setter
    def type(self, value):
        self._data["type"] = value

    @property
    def data(self):
        return self._data["data"]

    @data.setter
    def data(self, value):
        self._data["data"] = value

    @property
    def raw_data(self):
        return self._data

    @raw_data.setter
    def raw_data(self, value):
        self._data = value

    @classmethod
    def from_json(cls, data: dict[str, Any]):
        segment = segments.get(data["type"], cls)
        return segment.from_json(data)


@register_segment
class Text(Segment):
    segment_type = "text"

    def __init__(self, text: str):
        super().__init__({
            "type": self.segment_type,
            "data": {"text": text}
        })

    @property
    def text(self) -> str:
        return self.data["text"]

    @text.setter
    def text(self, text: str):
        self.data["text"] = text

    @classmethod
    def from_json(cls, data: dict[str, Any]):
        return cls(data["data"]["text"])


@register_segment
class Image(Segment):
    segment_type = "image"

    def __init__(self, image_url: str):
        super().__init__({
            "type": self.segment_type,
            "data": {"image_url": image_url}
        })

    @property
    def image_url(self) -> str:
        return self.data["image_url"]

    @image_url.setter
    def image_url(self, image_url: str):
        self.data["image_url"] = image_url

    @property
    def get_download_url(self) -> str:
        return self.image_url

    @classmethod
    def from_json(cls, data: dict[str, Any]):
        return cls(data["data"]["image_url"])


class RichText:
    def __init__(self, *segments: Segment):
        self.segments = segments

    def get_plain_text(self):
        return "".join(seg.text for seg in self.segments if isinstance(seg, Text))

    def to_json(self):
        return [seg.raw_data for seg in self.segments]

    @classmethod
    def from_json(cls, data: list[dict[str, Any]]):
        return cls(*[Segment.from_json(seg) for seg in data])

    def __add__(self, other: 'Segment | RichText'):
        if isinstance(other, RichText):
            return RichText(*self.segments, *other.segments)
        if isinstance(other, Segment):
            return RichText(*self.segments, other)
        raise TypeError("只支持拼接Segment或RichText")

    def __repr__(self):
        return f"<RichText {repr(self.to_json())}>"
