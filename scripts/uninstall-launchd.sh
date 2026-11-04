#!/bin/zsh
set -euo pipefail
LABEL="it.lucazambon.fa-bot"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "Bot disinstallato."
