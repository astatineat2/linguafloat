from types import SimpleNamespace

import pytest

from linguafloat.selection import (
    MAX_SELECTION_LENGTH,
    looks_like_secret,
    normalize_selection,
    selected_text_from_control,
)


class FakeRange:
    def __init__(self, text, rectangles=None):
        self.text = text
        self.rectangles = rectangles or []

    def GetText(self, max_length):
        return self.text[:max_length]

    def GetBoundingRectangles(self):
        return self.rectangles


class FakePattern:
    def __init__(self, ranges):
        self.ranges = ranges

    def GetSelection(self):
        return self.ranges


class FakeControl:
    def __init__(self, *, pattern=None, parent=None, process_id=42, password=False):
        self.pattern = pattern
        self.parent = parent
        self.ProcessId = process_id
        self.IsPassword = password

    def GetPattern(self, pattern_id):
        return self.pattern

    def GetParentControl(self):
        return self.parent


def test_reads_selected_text_and_uses_last_line_as_anchor():
    rectangles = [
        SimpleNamespace(left=10, top=20, right=110, bottom=40),
        SimpleNamespace(left=10, top=40, right=85, bottom=60),
    ]
    control = FakeControl(pattern=FakePattern([FakeRange("  Hello world  ", rectangles)]))

    candidate = selected_text_from_control(control, 25, 35, own_process_id=999)

    assert candidate is not None
    assert candidate.text == "Hello world"
    assert (candidate.anchor_x, candidate.anchor_y) == (85, 50)
    assert (candidate.left, candidate.top, candidate.right, candidate.bottom) == (
        10,
        20,
        110,
        60,
    )
    assert candidate.contains(50, 30)
    assert not candidate.contains(200, 200)


def test_walks_to_parent_text_control():
    parent = FakeControl(pattern=FakePattern([FakeRange("parent selection")]))
    child = FakeControl(parent=parent)

    candidate = selected_text_from_control(child, 20, 30, own_process_id=999)

    assert candidate is not None
    assert candidate.text == "parent selection"


def test_empty_point_control_can_fall_back_to_focused_control(monkeypatch):
    import linguafloat.selection as selection

    point_control = FakeControl()
    focused_control = FakeControl(
        pattern=FakePattern([FakeRange("focused selection")])
    )
    fake_auto = SimpleNamespace(
        UIAutomationInitializerInThread=lambda: _NullContext(),
        ControlFromPoint=lambda x, y: point_control,
        GetFocusedControl=lambda: focused_control,
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "uiautomation.uiautomation",
        fake_auto,
    )

    candidate = selection.read_selection_at(10, 20, own_process_id=999)

    assert candidate is not None
    assert candidate.text == "focused selection"


class _NullContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


@pytest.mark.parametrize(
    "control",
    [
        FakeControl(
            pattern=FakePattern([FakeRange("hidden password")]), password=True
        ),
        FakeControl(
            pattern=FakePattern([FakeRange("own app text")]), process_id=999
        ),
    ],
)
def test_blocks_password_and_own_process_controls(control):
    assert selected_text_from_control(control, 20, 30, own_process_id=999) is None


@pytest.mark.parametrize(
    "text",
    [
        "sk-abcdefghijklmnopqrstuvwxyz",
        "AKIA1234567890ABCDEF",
        "eyJheader.payload.signature",
        "a" * 40,
    ],
)
def test_detects_likely_secrets(text):
    assert looks_like_secret(text)
    with pytest.raises(ValueError, match="密码或密钥"):
        normalize_selection(text)


def test_rejects_overlong_selection():
    with pytest.raises(ValueError, match=str(MAX_SELECTION_LENGTH)):
        normalize_selection("中" * (MAX_SELECTION_LENGTH + 1))
