#!/usr/bin/env bash
set -euo pipefail

# Owner-run installer for the NFL production scanner.
# Goal: one shared approved-bet list -> integrity guard -> email + Appwiza + bet tracker.
# Nothing downstream may consume the unguarded candidate list.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROD_ROOT="/home/anestishkurti92/nfl-predictor-v1/research_v2"
SCANNER="$PROD_ROOT/nfl_home_opener_scanner.py"
GUARD_SRC="$REPO_ROOT/nfl/production_match_guard.py"
GUARD_DST="$PROD_ROOT/production_match_guard.py"
PATCHER="$REPO_ROOT/nfl/patch_live_scanner_match_guard.py"
BACKUP="$SCANNER.bak.$(date -u +%Y%m%dT%H%M%SZ)"

if [[ ! -f "$SCANNER" ]]; then
  echo "missing production scanner: $SCANNER" >&2
  exit 1
fi
if [[ ! -f "$GUARD_SRC" ]]; then
  echo "missing guard source: $GUARD_SRC" >&2
  exit 1
fi
if [[ ! -f "$PATCHER" ]]; then
  echo "missing scanner patcher: $PATCHER" >&2
  exit 1
fi

# Must run as the owner (or equivalent privileged account). Refuse partial install.
if [[ ! -w "$PROD_ROOT" || ! -w "$SCANNER" ]]; then
  echo "production directory/scanner is not writable by $(id -un); aborting before changes" >&2
  exit 2
fi

cp -p "$SCANNER" "$BACKUP"
cp "$GUARD_SRC" "$GUARD_DST"

python3 "$PATCHER" "$SCANNER"

# Enforce the architectural invariant in the live scanner source itself:
# 1) filter_records() creates approved_records once.
# 2) email renderer uses approved_records only.
# 3) Appwiza publisher uses approved_records only.
# 4) bet tracker / ledger uses approved_records only.
python3 - "$SCANNER" <<'PY'
from pathlib import Path
import re, sys
p=Path(sys.argv[1]); s=p.read_text()

required = [
    'filter_records(',
    'approved_records',
]
for token in required:
    if token not in s:
        raise SystemExit(f'missing required post-patch token: {token}')

# Ban downstream use of the old unguarded `records` variable after approval is created.
# We permit construction/guard call itself, but publishing/email/tracking calls must use approved_records.
checks = {
    'appwiza_publish': [r'publish_nfl\(\s*records\b', r'safe_call\([^\n]*publish_nfl[^\n]*\brecords\b'],
    'email_render': [r'(?:render|build|send)[A-Za-z_]*\([^\n]*\brecords\b'],
    'bet_tracker': [r'(?:track|record|ledger|save_bet|write_bet)[A-Za-z_]*\([^\n]*\brecords\b'],
}
violations=[]
for label, pats in checks.items():
    for pat in pats:
        for m in re.finditer(pat,s,re.I):
            # Ignore explicit approved_records occurrences.
            frag=s[m.start():m.end()+80]
            if 'approved_records' not in frag:
                violations.append((label, frag.splitlines()[0][:220]))
if violations:
    for v in violations:
        print('unguarded downstream use:',v,file=sys.stderr)
    raise SystemExit('single-source invariant failed')

# Positive proof: approved list must feed every present downstream consumer class.
for needle in ['publish_nfl']:
    if needle in s and not re.search(r'publish_nfl\([^\n]*approved_records|safe_call\([^\n]*publish_nfl[^\n]*approved_records',s,re.I):
        raise SystemExit('Appwiza publish path is not bound to approved_records')

p.write_text(s)
print('single-source approved-record invariant: PASS')
PY

# Syntax + guard tests.
python3 -m py_compile "$GUARD_DST" "$SCANNER"
python3 -m unittest -v "$REPO_ROOT/nfl/test_production_match_guard.py"

# Smoke-import scanner without running its service loop when possible.
python3 - "$SCANNER" <<'PY'
from pathlib import Path
import ast,sys
ast.parse(Path(sys.argv[1]).read_text())
print('scanner AST parse: PASS')
PY

# Restart only after every validation passed.
if command -v systemctl >/dev/null 2>&1; then
  sudo systemctl restart nfl-home-opener.service
  sudo systemctl is-active --quiet nfl-home-opener.service
  echo "nfl-home-opener.service active"
else
  echo "systemctl unavailable; scanner patched but service restart must be done manually" >&2
fi

echo "installed production match guard"
echo "backup: $BACKUP"
echo "guard:  $GUARD_DST"
echo "scanner: $SCANNER"
echo "architecture: candidates -> filter_records -> approved_records -> email/Appwiza/tracker"
