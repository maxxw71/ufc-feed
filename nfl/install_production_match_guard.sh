#!/usr/bin/env bash
set -Eeuo pipefail

# Installs the already-tested NFL matchup identity guard into the confirmed live
# Appwiza/email scanner. This script deliberately does NOT send an email.

LIVE_ROOT=/home/anestishkurti92/nfl-predictor-v1
R2="$LIVE_ROOT/research_v2"
SCANNER="$R2/nfl_home_opener_scanner.py"
EMAIL="$R2/nfl_email_design.py"
PY="$LIVE_ROOT/venv/bin/python"
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
GUARD="$HERE/production_match_guard.py"
PATCHER="$HERE/patch_live_scanner_match_guard.py"
TEST="$HERE/test_production_match_guard.py"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP="$LIVE_ROOT/backups/match_integrity_$STAMP"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

if [ "$(id -un)" != "anestishkurti92" ] && [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: run this as anestishkurti92 (or root). Current user: $(id -un)" >&2
  exit 77
fi

for f in "$SCANNER" "$EMAIL" "$GUARD" "$PATCHER" "$TEST"; do
  test -f "$f" || { echo "ERROR: missing $f" >&2; exit 2; }
done

test -x "$PY" || PY=$(command -v python3)

# 1. Re-run the regression suite from the exact commit being installed.
(
  cd "$HERE"
  "$PY" test_production_match_guard.py
)

# 2. Construct patched production files in an isolated temp directory.
cp "$SCANNER" "$TMP/nfl_home_opener_scanner.py"
cp "$EMAIL" "$TMP/nfl_email_design.py"
"$PY" "$PATCHER" "$TMP/nfl_home_opener_scanner.py"

"$PY" - "$TMP/nfl_email_design.py" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]);text=p.read_text()
old="  selected=r['away'] if r.get('selection_side')=='away' else r['home'];opp=r['home'] if selected==r['away'] else r['away']"
new="  side=r.get('selection_side')\n  if side not in ('home','away') or r.get('selected_team')!=r.get(side):raise ValueError('Unvalidated NFL selection identity')\n  selected=r['selected_team'];opp=r['away'] if side=='home' else r['home']"
if old in text:
    text=text.replace(old,new,1)
elif "raise ValueError('Unvalidated NFL selection identity')" not in text:
    raise RuntimeError('email identity anchor missing')
p.write_text(text)
PY

"$PY" -m py_compile "$GUARD" "$TMP/nfl_home_opener_scanner.py" "$TMP/nfl_email_design.py"

# 3. Assert the common fail-closed boundary before modifying production.
"$PY" - "$TMP/nfl_home_opener_scanner.py" "$TMP/nfl_email_design.py" <<'PY'
from pathlib import Path
import sys
s=Path(sys.argv[1]).read_text();e=Path(sys.argv[2]).read_text()
g=s.index('match_guard.filter_records(raw_production_records,G,now)')
for needle in [
    "atomic(S/(stamp+'.json')",
    'sports_publish.publish_nfl(records,now,nfl_email_design)',
    'nfl_email_design.render(records,now)',
    'bet_tracker.nfl_picks(records)',
    'invalidate any pre-validation cached envelope',
]:
    assert s.index(needle,g)>g, needle
assert 'sports_publish.safe_call(sports_publish.publish_nfl,records,now,nfl_email_design)' not in s
assert "selection_side='home',selected_team=x.home_team" in s
assert "market_side':side,'market_team':selected" in s
assert "raise ValueError('Unvalidated NFL selection identity')" in e
print('pre-install production boundary validation: PASS')
PY

# 4. Preserve exact current production files, then install atomically by rename.
mkdir -p "$BACKUP"
cp -a "$SCANNER" "$EMAIL" "$BACKUP/"
[ ! -f "$R2/production_match_guard.py" ] || cp -a "$R2/production_match_guard.py" "$BACKUP/production_match_guard.py.previous"

install -m 0644 "$GUARD" "$R2/production_match_guard.py.new"
install -m 0644 "$TMP/nfl_home_opener_scanner.py" "$R2/nfl_home_opener_scanner.py.new"
install -m 0644 "$TMP/nfl_email_design.py" "$R2/nfl_email_design.py.new"

mv "$R2/production_match_guard.py.new" "$R2/production_match_guard.py"
mv "$R2/nfl_home_opener_scanner.py.new" "$SCANNER"
mv "$R2/nfl_email_design.py.new" "$EMAIL"

# 5. Validate the exact live bytes. Roll back on any failure.
rollback() {
  echo 'ERROR: live validation failed; restoring backup' >&2
  cp -a "$BACKUP/nfl_home_opener_scanner.py" "$SCANNER"
  cp -a "$BACKUP/nfl_email_design.py" "$EMAIL"
  if [ -f "$BACKUP/production_match_guard.py.previous" ]; then
    cp -a "$BACKUP/production_match_guard.py.previous" "$R2/production_match_guard.py"
  else
    rm -f "$R2/production_match_guard.py"
  fi
}
trap 'rc=$?; if [ $rc -ne 0 ]; then rollback; fi; rm -rf "$TMP"; exit $rc' EXIT

"$PY" -m py_compile "$R2/production_match_guard.py" "$SCANNER" "$EMAIL"
cmp -s "$GUARD" "$R2/production_match_guard.py"
grep -q 'match_guard.filter_records(raw_production_records,G,now)' "$SCANNER"
grep -q "selection_side='home',selected_team=x.home_team" "$SCANNER"
grep -q "market_side':side,'market_team':selected" "$SCANNER"
grep -q 'sports_publish.publish_nfl(records,now,nfl_email_design)' "$SCANNER"
! grep -q 'sports_publish.safe_call(sports_publish.publish_nfl,records,now,nfl_email_design)' "$SCANNER"
grep -q 'invalidate any pre-validation cached envelope' "$SCANNER"
grep -q "raise ValueError('Unvalidated NFL selection identity')" "$EMAIL"

trap - EXIT
rm -rf "$TMP"

echo "NFL production matchup guard: INSTALLED"
echo "Backup: $BACKUP"
echo "No email was sent by this installer."
