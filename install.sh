#!/usr/bin/env bash
# Installs yaprflow as a systemd user service that starts with the graphical session.
set -euo pipefail
cd "$(dirname "$0")"
root=$PWD

missing=()
for tool in uv notify-send; do command -v "$tool" >/dev/null || missing+=("$tool"); done
if [[ -n ${WAYLAND_DISPLAY:-} ]]; then
  command -v wl-copy >/dev/null || missing+=("wl-copy (wl-clipboard)")
else
  command -v xclip >/dev/null || missing+=("xclip")
fi
if (( ${#missing[@]} )); then
  echo "Missing: ${missing[*]}" >&2
  exit 1
fi

uv sync --frozen --no-dev
if ! .venv/bin/python -c "import sounddevice" 2>/dev/null; then
  echo "PortAudio is missing; install it (Arch: sudo pacman -S portaudio) and re-run." >&2
  exit 1
fi

# A lingering user manager survives logout and keeps its old groups, so only a reboot refreshes them.
relogin="log out and back in"
[[ $(loginctl show-user "$USER" -p Linger --value 2>/dev/null) == yes ]] && relogin="reboot (lingering is enabled, so logging out is not enough)"
if ! id -nG | tr ' ' '\n' | grep -qx input; then
  if getent group input | cut -d: -f4 | tr ',' '\n' | grep -qx "$USER"; then
    echo "You are in the input group but this session predates it: $relogin." >&2
  else
    echo "Reading the hotkey needs the input group:" >&2
    echo "  sudo usermod -aG input $USER    # then $relogin" >&2
  fi
  echo "The service is installed anyway and will start working after that." >&2
fi

mkdir -p ~/.config/systemd/user ~/.config/yaprflow
sed "s|@ROOT@|$root|g" systemd/yaprflow.service > ~/.config/systemd/user/yaprflow.service
systemctl --user daemon-reload
systemctl --user enable --now yaprflow.service
echo "yaprflow installed. Logs: journalctl --user -u yaprflow -f"
