import pytest
from evdev import ecodes as e

from yaprflow.hotkey import ChordTracker, HotkeyError, parse_chord

DOWN, UP, REPEAT = 1, 0, 2


@pytest.fixture
def chord():
    events: list[str] = []
    tracker = ChordTracker(parse_chord("ctrl+super"), lambda: events.append("press"),
                           lambda: events.append("release"), lambda: events.append("cancel"))
    return tracker, events


def test_parse_groups_both_sides():
    groups = parse_chord("ctrl+super")
    assert groups == (frozenset({e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL}), frozenset({e.KEY_LEFTMETA, e.KEY_RIGHTMETA}))


def test_parse_plain_key_and_errors():
    assert parse_chord("alt+F9") == (frozenset({e.KEY_LEFTALT, e.KEY_RIGHTALT}), frozenset({e.KEY_F9}))
    with pytest.raises(HotkeyError):
        parse_chord("ctrl+nosuchkey")
    with pytest.raises(HotkeyError):
        parse_chord(" + ")


def test_press_and_release_in_either_order(chord):
    t, ev = chord
    t.key("kbd", e.KEY_LEFTMETA, DOWN)
    t.key("kbd", e.KEY_RIGHTCTRL, DOWN)
    t.key("kbd", e.KEY_RIGHTCTRL, REPEAT)
    assert ev == ["press"]
    t.key("kbd", e.KEY_LEFTMETA, UP)
    assert ev == ["press", "release"]
    t.key("kbd", e.KEY_RIGHTCTRL, UP)
    assert ev == ["press", "release"]


def test_extra_key_cancels_and_stays_inert_until_let_go(chord):
    t, ev = chord
    t.key("kbd", e.KEY_LEFTCTRL, DOWN)
    t.key("kbd", e.KEY_LEFTMETA, DOWN)
    t.key("kbd", e.KEY_LEFT, DOWN)
    t.key("kbd", e.KEY_LEFT, UP)
    assert ev == ["press", "cancel"]
    t.key("kbd", e.KEY_LEFTMETA, UP)
    t.key("kbd", e.KEY_LEFTMETA, DOWN)  # chord broken, so it re-arms
    assert ev == ["press", "cancel", "press"]


def test_chord_with_other_key_already_held_does_not_fire(chord):
    t, ev = chord
    t.key("kbd", e.KEY_LEFTSHIFT, DOWN)
    t.key("kbd", e.KEY_LEFTCTRL, DOWN)
    t.key("kbd", e.KEY_LEFTMETA, DOWN)
    assert ev == []


def test_unplugged_keyboard_releases_its_keys(chord):
    t, ev = chord
    t.key("a", e.KEY_LEFTCTRL, DOWN)
    t.key("b", e.KEY_LEFTMETA, DOWN)
    assert ev == ["press"] and t.modifiers_held()
    t.drop_source("b")
    assert ev == ["press", "release"]
    t.drop_source("a")
    assert not t.modifiers_held()
