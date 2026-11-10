# yaprflow

Hold **Ctrl+Super**, speak, let go. yaprflow transcribes what you said on your own machine and
pastes it into the focused terminal, which is the input box of Claude Code, Codex or any other
terminal coding agent. It never presses Enter unless you ask it to, so you can review the text and
send it yourself.

## How it works

1. **Hotkey.** yaprflow reads the keyboards under `/dev/input` (evdev). It observes the keys and
   never grabs them. This works the same under any Wayland compositor or X11, and it can see a
   modifier-only chord like Ctrl+Super, which desktop shortcut systems usually can't bind. The
   chord counts only when it is held on its own. Pressing any other key while it is down (for
   example Ctrl+Super+Left) cancels the recording, so your existing shortcuts keep working.
2. **Recording.** The microphone opens when the chord goes down and closes when it comes up.
   Samples are kept in memory only. The level is normalised, so a quiet microphone still works.
3. **Transcription.** [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (`small.en`,
   int8, CPU) runs locally. Nothing is sent to a speech service.
4. **Paste.** yaprflow puts the text on the clipboard, then presses Ctrl+Shift+V on a virtual
   keyboard (`/dev/uinput`). Once you have let go of every key, the text lands in whichever window
   has focus at that moment. Your previous clipboard is restored straight after.

## Nothing is left behind

- Audio never touches the disk. It is held in memory and dropped once it is transcribed.
- The text is never written to a file or logged, not even at `debug`. Logs record only its
  length. It is not shown in notifications either.
- On Wayland the clipboard entry is marked sensitive, so clipboard managers such as Klipper leave it
  out of their history. Your previous clipboard comes back about 0.4 s after the paste. If it can't
  be restored, the clipboard is cleared instead.
- If the paste fails, the text is thrown away. Say it again.

The only thing written to disk is the Whisper model. It is downloaded once (~250 MB for `small.en`)
into the Hugging Face cache.

## Requirements

- Linux on Wayland or X11. macOS and Windows are not supported yet: recording and transcription
  are portable, but the hotkey and keystroke injection use Linux evdev.
- Membership of the `input` group, to read the keyboards:
  `sudo usermod -aG input $USER`, then log out and back in. If lingering is enabled
  (`loginctl show-user $USER -p Linger`), reboot instead: the user service manager survives logout
  and keeps its old groups. Be aware that any program running as
  your user can then read every keystroke.
- Write access to `/dev/uinput`. Most desktops grant it to the logged-in user through a seat ACL,
  and members of `input` often have it too. Check with `ls -l /dev/uinput` and `getfacl /dev/uinput`.
- PortAudio (Arch: `portaudio`, Debian/Ubuntu: `libportaudio2`)
- `wl-clipboard` on Wayland, or `xclip` on X11
- `notify-send` (optional, for status notifications)
- [uv](https://docs.astral.sh/uv/)

## Install

```bash
./install.sh
```

This syncs the virtualenv, checks the requirements above and enables the `yaprflow` systemd user
service, which starts with your graphical session. The first start downloads the Whisper model.

To run it in the foreground instead:

```bash
uv run yaprflow
```

## Configuration

Put overrides in `~/.config/yaprflow/env` (`KEY=value` lines), then
`systemctl --user restart yaprflow`.

| Variable | Default | Meaning |
|---|---|---|
| `YAPRFLOW_HOTKEY` | `ctrl+super` | Chord to hold. Use modifiers (`ctrl`, `shift`, `alt`, `super`), which match either side, or evdev key names such as `f9`, `rightalt` or `KEY_PAUSE` |
| `YAPRFLOW_PASTE_KEYS` | `ctrl+shift+v` | Keystroke that pastes in your terminal, e.g. `ctrl+v` for terminals that use it, or `shift+insert` |
| `YAPRFLOW_SUBMIT` | `0` | `1` presses Enter after pasting |
| `YAPRFLOW_AUDIO_DEVICE` | system default input | PortAudio device name (substring) or index; list them with `uv run python -m sounddevice` |
| `YAPRFLOW_WHISPER_MODEL` | `small.en` | Any faster-whisper model (`base.en` is faster, `medium.en` more accurate, `small` for other languages) |
| `YAPRFLOW_MIN_SECONDS` | `0.5` | Shorter holds are discarded |
| `YAPRFLOW_NOTIFY` | `1` | `0` turns off desktop notifications |
| `YAPRFLOW_LOG_LEVEL` | `info` | `debug` adds hotkey events and device scans |

Keystrokes are sent as physical key positions, and your keyboard layout applies to them. With a
non-QWERTY layout, name the QWERTY key at the position that types `v` for you (Dvorak: `ctrl+shift+dot`).

## Operating

```bash
journalctl --user -u yaprflow -f      # JSON logs
systemctl --user restart yaprflow
systemctl --user disable --now yaprflow
```

A configuration or permission problem stops the service without restart loops (exit codes 2 and
3). It shows a notification saying what to fix.

## Development

```bash
uv run pytest
```
