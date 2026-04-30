#!/bin/zsh
set -euo pipefail

INTERVAL_MINUTES="${1:-1}"
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_PATH="$HOME/Library/LaunchAgents/ai.hermes.cpr.plist"
PYTHON_BIN="$(command -v python3)"

if [[ ! -f "$ROOT_DIR/config.json" ]]; then
  echo "config.json not found in $ROOT_DIR"
  echo "Copy config.example.json to config.json first."
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>ai.hermes.cpr</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON_BIN</string>
    <string>$ROOT_DIR/hermes_cpr.py</string>
    <string>--config</string>
    <string>$ROOT_DIR/config.json</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$ROOT_DIR</string>
  <key>StartInterval</key>
  <integer>$((INTERVAL_MINUTES * 60))</integer>
  <key>RunAtLoad</key>
  <true/>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)/ai.hermes.cpr" >/dev/null 2>&1 || true
launchctl enable "gui/$(id -u)/ai.hermes.cpr"
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl kickstart -k "gui/$(id -u)/ai.hermes.cpr"

echo "Installed ai.hermes.cpr"
echo "plist: $PLIST_PATH"
echo "interval: ${INTERVAL_MINUTES} minute(s)"
