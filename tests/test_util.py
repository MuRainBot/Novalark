"""适配器共用的解析小工具。

协议的字段类型很松：数字可能是字符串、可能是小数、可能是空串。
"""

from __future__ import annotations

import pytest

from novalark.adapter._util import brief, int_or_none, text_or_none, to_int


@pytest.mark.parametrize(
    "value, expected",
    [
        (600, 600),
        ("600", 600),
        (" 12 ", 12),
        (12.5, 12),
        ("12.5", 12),  # 小数文本要和小数一样截断 —— 曾经这里是 0
        ("", 0),
        (None, 0),
        ("abc", 0),
        (float("inf"), 0),
        (float("nan"), 0),
    ],
    ids=repr,
)
def test_to_int(value: object, expected: int):
    assert to_int(value) == expected


def test_to_int_does_not_silently_zero_a_duration():
    """带小数点的时长被静默当 0，在禁言上就是「0 秒 = 解禁」。"""
    assert to_int("12.5") == 12
    assert to_int(12.5) == 12


def test_int_or_none_keeps_missing_apart_from_zero():
    assert int_or_none(0) == 0
    assert int_or_none("0") == 0
    assert int_or_none(None) is None
    assert int_or_none("") is None
    assert int_or_none("abc") is None
    assert int_or_none("12.5") == 12


def test_text_or_none_and_brief():
    assert text_or_none(None) is None
    assert text_or_none("") is None
    assert text_or_none(0) == "0"
    assert brief({"a": "x" * 400}).endswith("…")
    assert len(brief({"a": "x" * 400})) <= 161
