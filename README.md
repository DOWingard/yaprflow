# yaprflow

Hold **Ctrl+Super**, speak, let go. yaprflow transcribes what you said on your own machine and
pastes it into the focused window. That is usually the input box of Claude Code, Codex or another
terminal coding agent. It never presses Enter unless you ask it to, so you can review the text and
send it yourself. It runs in the background from login onwards.

Super is the Windows key on Windows and Command on macOS.

| Platform | Status |
|---|---|
| Linux (any distribution, Wayland or X11) | Tested |
| macOS | Implemented, not yet tested on a Mac |
| Windows | Implemented, not yet tested on Windows |

## How it works

1. **Hotkey.** The chord counts only when it is held on its own. Pressing any other key while it
   is down (Ctrl+Super+Left, …) cancels the recording, so your existing shortcuts keep working.
   Keys are observed, never blocked.
   - Linux reads the keyboards under `/dev/input` (evdev). This works under any Wayland compositor
     or X11, and it can see a modifier-only chord, which desktop shortcut systems usually can't bind.
   - macOS uses a Quartz event tap, and Windows uses a low-level keyboard hook (both through pynput).
2. **Recording.** The microphone opens when the chord goes down and closes when it comes up.
   Samples stay in memory. The level is normalised, so a quiet microphone still works.
3. **Transcription.** [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (`small.en`,
   int8, CPU) runs locally. Nothing is sent to a speech service.
4. **Paste.** yaprflow waits until you have let go of every key. It then puts the text on the
   clipboard, presses the paste keystroke (Ctrl+Shift+V on Linux, Cmd+V on macOS, Ctrl+V on
   Windows) and restores your previous clipboard. Linux sends the keystroke from a virtual
   keyboard (`/dev/uinput`). macOS and Windows use the OS's synthetic input.

## Nothing is left behind

- Audio never touches the disk. It is held in memory and dropped once it is transcribed.
- The text is never written to a file or logged, not even at `debug`. Logs record only its length.
  It is not shown in notifications either.
- Your previous clipboard comes back about 0.4 s after the paste.
  - On Wayland, the entry is also marked sensitive, so clipboard managers such as Klipper leave it
    out of their history.
  - On X11, macOS and Windows, only text is restored: an image or files on the clipboard are lost.
- If the paste fails, the text is thrown away. Say it again.

The only thing written to disk is the Whisper model. It is downloaded once (~250 MB for `small.en`)
into the Hugging Face cache.

## Requirements

All platforms need [uv](https://docs.astral.sh/uv/).

**Linux**
- Membership of the `input` group, to read the keyboards: `sudo usermod -aG input $USER`, then log
  out and back in. If lingering is enabled (`loginctl show-user $USER -p Linger`), reboot instead:
  the user service manager survives logout and keeps its old groups. Be aware that any program
  running as your user can then read every keystroke.
- Write access to `/dev/uinput`. Most desktops grant it to the logged-in user through a seat ACL,
  and members of `input` often have it too. Check with `getfacl /dev/uinput`.
- PortAudio (Arch: `portaudio`, Debian/Ubuntu: `libportaudio2`, Fedora: `portaudio`)
- `wl-clipboard` on Wayland, or `xclip` on X11
- systemd, for the background service
- `notify-send` (optional), for status notifications

**macOS**
- Allow the Python interpreter (the installer prints its path) under System Settings → Privacy &
  Security → **Accessibility** and **Input Monitoring**. Allow microphone access when asked.

**Windows**
- Nothing extra. Windows does not let a normal process send keys to a window running as
  administrator, so an elevated terminal can't receive pastes.

## Install

Linux and macOS:

```bash
./install.sh
```

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

| | Background service | Logs |
|---|---|---|
| Linux | systemd user service `yaprflow`, started with the graphical session | `journalctl --user -u yaprflow -f` |
| macOS | launchd agent `dev.yaprflow`, started at login | `~/Library/Logs/yaprflow.log` |
| Windows | `yaprflow.lnk` in the Startup folder, runs without a console window | `%LOCALAPPDATA%\yaprflow\yaprflow.log` |

The first start downloads the Whisper model. To run it in the foreground instead: `uv run yaprflow`.

## Configuration

Put `KEY=value` lines in the config file, then restart yaprflow. Real environment variables take
precedence over the file.

| Platform | Config file | Restart |
|---|---|---|
| Linux | `~/.config/yaprflow/env` | `systemctl --user restart yaprflow` |
| macOS | `~/.config/yaprflow/env` | `launchctl kickstart -k gui/$(id -u)/dev.yaprflow` |
| Windows | `%APPDATA%\yaprflow\env` | re-run `install.ps1`, or sign out and in |

| Variable | Default | Meaning |
|---|---|---|
| `YAPRFLOW_HOTKEY` | `ctrl+super` | Chord to hold. Use modifiers (`ctrl`, `shift`, `alt`, `super`; aliases `win`, `cmd`, `option`), which match either side, plus key names such as `f9` or `space` |
| `YAPRFLOW_PASTE_KEYS` | per platform | Keystroke that pastes into your terminal. Defaults: `ctrl+shift+v` on Linux, `super+v` on macOS, `ctrl+v` on Windows |
| `YAPRFLOW_SUBMIT` | `0` | `1` presses Enter after pasting |
| `YAPRFLOW_AUDIO_DEVICE` | system default input | Input to record from. On Linux this can be a PipeWire source name from `pactl list sources short` (needs pipewire-alsa). On every platform it can be a PortAudio device name or index from `uv run python -m sounddevice`. Unset follows the system default, and a change of default applies from the next press |
| `YAPRFLOW_WHISPER_MODEL` | `small.en` | Any faster-whisper model (`base.en` is faster, `medium.en` more accurate, `small` for other languages) |
| `YAPRFLOW_MIN_SECONDS` | `0.5` | Shorter holds are discarded |
| `YAPRFLOW_NOTIFY` | `1` | `0` turns off notifications (Linux and macOS only) |
| `YAPRFLOW_LOG_LEVEL` | `info` | `debug` adds hotkey events and device scans |
| `YAPRFLOW_LOG_FILE` | stderr | Write logs to this file instead (rotated at 1 MB) |

On Linux, raw `hw:` PortAudio devices disappear while PipeWire holds the card open, so PipeWire
source names are the reliable choice there.

Keystrokes are sent as physical key positions, and your keyboard layout applies to them. With a
non-QWERTY layout, name the QWERTY key at the position that types `v` for you (Dvorak:
`ctrl+shift+dot`).

## Troubleshooting

- **"microphone is silent".** The selected input delivers digital silence: the wrong input, or a
  hardware gain at zero. Set `YAPRFLOW_AUDIO_DEVICE`, or change the system default input.
- **Nothing happens on the hotkey.** Check the logs for `cannot start`. A configuration or
  permission problem stops yaprflow without restart loops and shows a notification saying what to
  fix (exit code 2 for configuration, 3 for permissions or missing tools).

## Development

```bash
uv run pytest
```

The tests cover the chord logic for both input backends: pynput's dummy backend lets the
macOS/Windows key mapping run on any platform.
