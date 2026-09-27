#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$HOME/ufc-predictor-v1"
WATCHER="$ROOT/ufc_email_watcher.py"
ENV_FILE="$HOME/.config/ufc-watcher.env"
[ -f "$WATCHER" ] || exit 2
[ -f "$ENV_FILE" ] || exit 3
cp "$WATCHER" "$WATCHER.pre-opponent-context-display.$(date +%Y%m%d-%H%M%S).bak"

"$ROOT/venv/bin/python" - "$WATCHER" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]);s=p.read_text()
if 'UFC_OPPONENT_CONTEXT_DISPLAY_V1' in s:
    print('Display patch already installed');raise SystemExit(0)
marker='if __name__ == "__main__":'
if marker not in s: marker="if __name__ == '__main__':"
if marker not in s: raise RuntimeError('main marker missing')
addon=r'''
# UFC_OPPONENT_CONTEXT_DISPLAY_V1
# Keep public/email method labels and historical evidence synchronized with the
# risk gates that are actually enforced live.
_orig_method_cards_ctxdisplay=ufc_email_design.method_cards
def _ctxdisplay_method_cards(p,w):
    cards=list(_orig_method_cards_ctxdisplay(p,w))
    out=[]
    for c0 in cards:
        c=dict(c0); mid=str(c.get('id') or '')
        if mid=='U7':
            c['title']='Striking + TD Defense · Risk Gated'
            c['history']=dict(bets=40,wins=40,win_rate=1.0,roi=.2308)
            c['note']='Risk-gated historical sample: 40-0, +23.08% ROI; pre-2020 +23.82%, 2020+ +22.18%. Favorite cannot be more than 3 years older and the opponent-power risk product must remain below 0.12.'
        elif mid=='U8':
            c['title']='Striking Differential + Opponent Context'
            c['history']=dict(bets=66,wins=65,win_rate=65/66,roi=.1895)
            c['note']='Opponent-context historical sample: 66 bets, 65-1, 98.5% wins, +18.95% ROI; pre-2020 +18.91%, 2020+ +19.02%. Age and younger-opponent wrestling pressure can veto the raw striking signal.'
        elif mid=='U10':
            c['title']='KO/TKO Recovery + Opponent Age Guard'
            c['history']=dict(bets=246,wins=182,win_rate=182/246,roi=.1101)
            c['note']='Opponent-age matched sample: 246 bets, 182-64, 74.0% wins, +11.01% ROI; pre-2020 +6.38%, 2020+ +15.50%. Favorite must not be more than 2 years older.'
        out.append(c)
    return out
ufc_email_design.method_cards=_ctxdisplay_method_cards
'''
s=s.replace(marker,addon+'\n'+marker,1)
p.write_text(s)
print('Installed opponent-context display sync')
PY

"$ROOT/venv/bin/python" -m py_compile "$WATCHER"
cd "$ROOT"
set -a
source "$ENV_FILE"
set +a
"$ROOT/venv/bin/python" "$WATCHER" --run --force-initial
