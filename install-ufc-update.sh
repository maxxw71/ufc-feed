#!/usr/bin/env bash
set -Eeuo pipefail

REPO_DIR="${UFC_FEED_DIR:-$HOME/ufc-feed}"
TARGET="$HOME/.local/bin/ufc-update"

mkdir -p "$HOME/.local/bin"

if [ ! -d "$REPO_DIR/.git" ]; then
  echo "Cloning maxxw71/ufc-feed to $REPO_DIR..."
  git clone https://github.com/maxxw71/ufc-feed.git "$REPO_DIR"
fi

cd "$REPO_DIR"
git pull --ff-only
chmod +x "$REPO_DIR/ufc-update"
ln -sf "$REPO_DIR/ufc-update" "$TARGET"

case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *)
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
    export PATH="$HOME/.local/bin:$PATH"
    ;;
esac

echo
 echo "Installed: $TARGET"
echo "Use:"
echo "  ufc-update"
echo "  ufc-update skill-veto"
