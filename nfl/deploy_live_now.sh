#!/usr/bin/env bash
set -Eeuo pipefail

PROD_ROOT=/home/anestishkurti92/nfl-predictor-v1/research_v2
SCANNER="$PROD_ROOT/nfl_home_opener_scanner.py"
EMAIL="$PROD_ROOT/nfl_email_design.py"
STATE=/srv/appwiza-sports/state/nfl.json
PUB_ROOT=/opt/sports-publisher
TMP=$(mktemp -d /tmp/appwiza-nfl-deploy.XXXXXX)
TS=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP=/home/anestishkurti92/nfl-predictor-v1/backups/manual_guard_$TS
mkdir -p "$BACKUP"
trap 'rm -rf "$TMP"' EXIT

if [ "$(id -u)" -ne 0 ]; then
  echo 'Run as root.' >&2
  exit 1
fi

for f in "$SCANNER" "$EMAIL"; do
  [ -f "$f" ] || { echo "Missing production file: $f" >&2; exit 2; }
done

cp -a "$SCANNER" "$EMAIL" "$BACKUP/"
[ ! -f "$PROD_ROOT/production_match_guard.py" ] || cp -a "$PROD_ROOT/production_match_guard.py" "$BACKUP/production_match_guard.py.previous"
[ ! -f "$STATE" ] || cp -a "$STATE" "$BACKUP/nfl.json.previous"

git clone -q --depth 1 https://github.com/maxxw71/ufc-feed.git "$TMP/repo"
cd "$TMP/repo"

python3 nfl/test_production_match_guard.py
install -m 0644 nfl/production_match_guard.py "$PROD_ROOT/production_match_guard.py"
python3 nfl/patch_live_scanner_match_guard.py "$SCANNER"

python3 - "$EMAIL" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]); text=p.read_text()
old="  selected=r['away'] if r.get('selection_side')=='away' else r['home'];opp=r['home'] if selected==r['away'] else r['away']"
new="  side=r.get('selection_side')\n  if side not in ('home','away') or r.get('selected_team')!=r.get(side):raise ValueError('Unvalidated NFL selection identity')\n  selected=r['selected_team'];opp=r['away'] if side=='home' else r['home']"
if old in text:
    text=text.replace(old,new,1)
elif "raise ValueError('Unvalidated NFL selection identity')" not in text:
    raise SystemExit('email identity anchor missing')
p.write_text(text)
PY

python3 -m py_compile "$PROD_ROOT/production_match_guard.py" "$SCANNER" "$EMAIL"
grep -q 'match_guard.filter_records(raw_production_records,G,now)' "$SCANNER"
grep -q "selection_side='home',selected_team=x.home_team" "$SCANNER"
grep -q 'sports_publish.publish_nfl(records,now,nfl_email_design)' "$SCANNER"
grep -q 'bet_tracker.nfl_picks(records)' "$SCANNER"
grep -q "raise ValueError('Unvalidated NFL selection identity')" "$EMAIL"

systemctl restart nfl-home-opener.service
sleep 4
systemctl is-active --quiet nfl-home-opener.service

# Remove the known erroneous historical Chargers/Cardinals card from the public state.
# The new guard prevents it (and equivalent identity mismatches) from being published again.
if [ -f "$STATE" ]; then
  python3 - "$STATE" <<'PY'
from pathlib import Path
import json, os, sys
p=Path(sys.argv[1])
data=json.loads(p.read_text())
cards=data.get('cards',[])
def bad(c):
    fixture=str(c.get('fixture') or '')
    sel=str(c.get('selection') or '')
    cid=str(c.get('id') or '')
    return cid=='NFL:2026_01_ARI_LAC' or (sel in {'Chargers','Los Angeles Chargers'} and 'Cardinals' in fixture and 'Chargers' in fixture)
new=[c for c in cards if not bad(c)]
data['cards']=new
tmp=p.with_suffix('.json.tmp')
tmp.write_text(json.dumps(data,allow_nan=False))
os.chmod(tmp,0o600)
os.replace(tmp,p)
print(f'removed_bad_public_cards={len(cards)-len(new)}')
PY
fi

PYTHONPATH="$PUB_ROOT" python3 - <<'PY'
import sports_publish
sports_publish.render_all()
print('appwiza_render=PASS')
PY

# Local source checks after deployment.
grep -q 'raw_odds_selected_team_mismatch' "$PROD_ROOT/production_match_guard.py"
grep -q 'raw_odds_price_mismatch' "$PROD_ROOT/production_match_guard.py"

echo "DEPLOY_OK"
echo "backup=$BACKUP"
echo "service=nfl-home-opener.service active"
echo "guard=live"
echo "email=guarded"
echo "appwiza=rerendered"
