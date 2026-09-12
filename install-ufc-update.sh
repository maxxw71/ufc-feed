#!/usr/bin/env bash
set -Eeuo pipefail

TARGET="$HOME/.local/bin/ufc-update"
URL="https://raw.githubusercontent.com/maxxw71/ufc-feed/main/ufc-update"

mkdir -p "$HOME/.local/bin"

echo "Installing ufc-update from raw.githubusercontent.com..."
curl -6 -L --fail --connect-timeout 10 --max-time 120 --retry 2 --retry-delay 2 \
  "$URL" -o "$TARGET"
chmod +x "$TARGET"

case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *)
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
    ;;
esac

echo
echo "Installed: $TARGET"
echo "Run:"
echo "  source ~/.bashrc"
echo "  ufc-update skill-veto"
