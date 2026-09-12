#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$HOME/ufc-predictor-v1"
WATCHER="$ROOT/ufc_email_watcher.py"
ENV_FILE="$HOME/.config/ufc-watcher.env"

[ -f "$WATCHER" ] || { echo "ERROR: Missing $WATCHER"; exit 2; }
[ -f "$ENV_FILE" ] || { echo "ERROR: Missing $ENV_FILE"; exit 3; }

cp "$WATCHER" "$WATCHER.pre-full-digest-subject.$(date +%Y%m%d-%H%M%S).bak"

echo "[ 20%] Patching email subject so Gmail does not collapse repeated digest content..."
python - "$WATCHER" <<'PY'
from pathlib import Path
import re, sys
p=Path(sys.argv[1])
s=p.read_text()
marker='UNIQUE_FULL_DIGEST_SUBJECT_V1'
if marker not in s:
    # Insert immediately inside the top-level send_email(...) function, regardless of
    # whether its second argument is called body/html/content.
    m=re.search(r'(?m)^def\s+send_email\s*\([^\n]*\)\s*:\s*\n', s)
    if not m:
        raise RuntimeError('Could not find def send_email(...) in watcher')
    indent='    '
    insert=(
        f"{indent}# {marker}\n"
        f"{indent}from datetime import datetime as _digest_datetime\n"
        f"{indent}subject = f\"{{subject}} — {{_digest_datetime.now().strftime('%Y-%m-%d %H:%M:%S')}}\"\n"
    )
    s=s[:m.end()]+insert+s[m.end():]
    p.write_text(s)
    print('Patched send_email() with unique timestamped subject.')
else:
    print('Unique digest subject patch already present.')
PY

echo "[ 45%] Syntax-checking watcher..."
cd "$ROOT"
source "$ROOT/venv/bin/activate"
python -m py_compile "$WATCHER"

echo "[ 65%] Loading email environment..."
set -a
source "$ENV_FILE"
set +a

echo "[ 75%] Sending a fresh FULL digest in a new Gmail thread..."
python "$WATCHER" --run --force-initial

echo "[ 92%] Restarting scheduled watcher..."
sudo systemctl daemon-reload
sudo systemctl restart ufc-model-watcher.timer

echo "[100%] Done."
echo "Future digests now get a unique timestamp in the subject so Gmail should stop hiding repeated sections behind the three-dot trimmed-content control."
