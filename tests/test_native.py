"""The macOS/Windows key mapping, exercised through pynput's dummy backend on any platform."""

import os

os.environ.setdefault("PYNPUT_BACKEND", "dummy")

import pytest  # noqa: E402
from pynput.keyboard import Key, KeyCode  # noqa: E402

from yaprflow.backends import BackendError, native  # noqa: E402
from yaprflow.chord import ChordTracker  # noqa: E402


def test_either_side_of_a_modifier_maps_to_one_key():
    assert native._token(Key.ctrl_l) == native._token(Key.ctrl_r) == native._token(Key.ctrl)
    assert native._token(Key.cmd_r) == native._token(Key.cmd)
    assert native._token(KeyCode.from_char("V")) == native._token(KeyCode.from_char("v"))


def test_parse_chord():
    groups = native.parse_chord("ctrl+win")
    assert groups == (frozenset({native._token(Key.ctrl)}), frozenset({native._token(Key.cmd)}))
    assert native.parse_chord("alt+f9")[0] == frozenset({native._token(Key.alt), native._token(Key.alt_gr)})
    with pytest.raises(BackendError):
        native.parse_chord("ctrl+nosuchkey")


def test_tracker_with_pynput_events():
    events: list[str] = []
    t = ChordTracker(native.parse_chord("ctrl+super"), native.MODIFIERS, lambda: events.append("press"),
                     lambda: events.append("release"), lambda: events.append("cancel"))
    t.key("k", native._token(Key.ctrl_l), True)
    t.key("k", native._token(Key.cmd_r), True)
    assert events == ["press"] and t.modifiers_held()
    t.key("k", native._token(Key.ctrl_l), False)
    assert events == ["press", "release"]
    t.key("k", native._token(Key.cmd_r), False)
    assert not t.modifiers_held()


def test_paste_keys_resolve_to_pynput_keys():
    assert [native._key(p) for p in ("super", "v")] == [Key.cmd, KeyCode.from_char("v")]
