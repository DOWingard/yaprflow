import pytest
from evdev import ecodes as e

from yaprflow.backends import BackendError, linux
from yaprflow.chord import ChordError, ChordTracker, split


@pytest.fixture
def chord():
    events: list[str] = []
    tracker = ChordTracker(linux.parse_chord("ctrl+super"), linux.MODIFIERS, lambda: events.append("press"),
                           lambda: events.append("release"), lambda: events.append("cancel"))
    return tracker, events


def test_split_normalises_modifier_aliases():
    assert split("Control+Win+F9") == ["ctrl", "super", "f9"]
    assert split("cmd + option + v") == ["super", "alt", "v"]
    with pytest.raises(ChordError):
        split(" + ")


def test_linux_parse_groups_both_sides():
    assert linux.parse_chord("ctrl+super") == (frozenset({e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL}),
                                               frozenset({e.KEY_LEFTMETA, e.KEY_RIGHTMETA}))
    assert linux.parse_chord("alt+F9") == (frozenset({e.KEY_LEFTALT, e.KEY_RIGHTALT}), frozenset({e.KEY_F9}))
    with pytest.raises(BackendError):
        linux.parse_chord("ctrl+nosuchkey")


def test_press_and_release_in_either_order(chord):
    t, ev = chord
    t.key("kbd", e.KEY_LEFTMETA, True)
    t.key("kbd", e.KEY_RIGHTCTRL, True)
    t.key("kbd", e.KEY_RIGHTCTRL, True)  # a repeated key-down (pynput autorepeat) is harmless
    assert ev == ["press"]
    t.key("kbd", e.KEY_LEFTMETA, False)
    assert ev == ["press", "release"]
    t.key("kbd", e.KEY_RIGHTCTRL, False)
    assert ev == ["press", "release"]


def test_extra_key_cancels_and_stays_inert_until_let_go(chord):
    t, ev = chord
    t.key("kbd", e.KEY_LEFTCTRL, True)
    t.key("kbd", e.KEY_LEFTMETA, True)
    t.key("kbd", e.KEY_LEFT, True)
    t.key("kbd", e.KEY_LEFT, False)
    assert ev == ["press", "cancel"]
    t.key("kbd", e.KEY_LEFTMETA, False)
    t.key("kbd", e.KEY_LEFTMETA, True)  # chord broken, so it re-arms
    assert ev == ["press", "cancel", "press"]


def test_chord_with_other_key_already_held_does_not_fire(chord):
    t, ev = chord
    t.key("kbd", e.KEY_LEFTSHIFT, True)
    t.key("kbd", e.KEY_LEFTCTRL, True)
    t.key("kbd", e.KEY_LEFTMETA, True)
    assert ev == []


def test_unplugged_keyboard_releases_its_keys(chord):
    t, ev = chord
    t.key("a", e.KEY_LEFTCTRL, True)
    t.key("b", e.KEY_LEFTMETA, True)
    assert ev == ["press"] and t.modifiers_held()
    t.drop_source("b")
    assert ev == ["press", "release"]
    t.drop_source("a")
    assert not t.modifiers_held()
