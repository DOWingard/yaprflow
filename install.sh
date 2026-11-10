#!/usr/bin/env bash
# Installs yaprflow as a background service that starts at login: a systemd user service on Linux,
# a launchd agent on macOS. Windows uses install.ps1.
set -euo pipefail
cd "$(dirname "$0")"
root=$PWD

command -v uv >/dev/null || { echo "uv is not installed: https://docs.astral.sh/uv/" >&2; exit 1; }
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/yaprflow"

install_linux() {
  if [[ -n ${WAYLAND_DISPLAY:-} ]]; then
    command -v wl-copy >/dev/null || { echo "Missing wl-copy: install wl-clipboard" >&2; exit 1; }
  else
    command -v xclip >/dev/null || { echo "Missing xclip" >&2; exit 1; }
  fi
  command -v notify-send >/dev/null || echo "notify-send not found; yaprflow will run without notifications." >&2

  uv sync --frozen --no-dev
  if ! .venv/bin/python -c "import sounddevice" 2>/dev/null; then
    echo "PortAudio is missing; install it (Arch: portaudio, Debian/Ubuntu: libportaudio2, Fedora: portaudio) and re-run." >&2
    exit 1
  fi

  # A lingering user manager survives logout and keeps its old groups, so only a reboot refreshes them.
  local relogin="log out and back in"
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

  mkdir -p ~/.config/systemd/user
  sed "s|@ROOT@|$root|g" service/yaprflow.service > ~/.config/systemd/user/yaprflow.service
  systemctl --user daemon-reload
  systemctl --user enable yaprflow.service
  systemctl --user restart yaprflow.service
  echo "yaprflow installed. Logs: journalctl --user -u yaprflow -f"
}

install_macos() {
  uv sync --frozen --no-dev
  local plist=~/Library/LaunchAgents/dev.yaprflow.plist
  mkdir -p ~/Library/LaunchAgents ~/Library/Logs
  sed -e "s|@ROOT@|$root|g" -e "s|@HOME@|$HOME|g" service/dev.yaprflow.plist > "$plist"
  launchctl bootout "gui/$(id -u)/dev.yaprflow" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$plist"
  local python
  python=$(.venv/bin/python -c 'import os, sys; print(os.path.realpath(sys.executable))')
  echo "yaprflow installed. Logs: tail -f ~/Library/Logs/yaprflow.log"
  echo
  echo "Allow this interpreter under System Settings → Privacy & Security → Accessibility and"
  echo "Input Monitoring, then run: launchctl kickstart -k gui/$(id -u)/dev.yaprflow"
  echo "  $python"
}

case "$(uname -s)" in
  Linux) install_linux ;;
  Darwin) install_macos ;;
  *) echo "Unsupported system $(uname -s); on Windows run install.ps1" >&2; exit 1 ;;
esac
