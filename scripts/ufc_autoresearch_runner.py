#!/usr/bin/env python3
import html, os, sys
from datetime import datetime
import ufc_autoresearch as ar

_base_schema=ar.schema

def schema(df):
    datec,winc,probc,oddsc=_base_schema(df)
    if 'fav_decimal' in df.columns:
        oddsc='fav_decimal'
    elif 'favorite_decimal_odds' in df.columns:
        oddsc='favorite_decimal_odds'
    return datec,winc,probc,oddsc

ar.schema=schema

def send_digest(extra=None):
    vals=ar.envvals()
    for k in ('RESEND_API_KEY','UFC_ALERT_EMAIL','UFC_ALERT_FROM'):
        if vals.get(k): os.environ[k]=vals[k]
    runs,shadows,snaps,last=ar.counts()
    tops=ar.top_candidates(5)
    rows=''.join(
        f"<tr><td>{html.escape(r[1])}</td><td>{r[2]}</td><td>{r[3]}-{r[4]}</td><td>{100*r[6]:+.1f}%</td><td>{100*r[8]:+.1f}%</td><td>{r[11]}</td></tr>"
        for r in tops
    ) or '<tr><td colspan="6">No shadow candidate currently clears the robustness gates.</td></tr>'
    lasttxt='None yet' if not last else f"{last[0]} · {last[5]} · {last[2] or 0} rows · {last[3] or 0:,} tested · {last[4] or 0} survivors"
    body=f'''<div style="font-family:Arial,sans-serif;max-width:900px;margin:auto;color:#172033">
    <h2>UFC Research Daily</h2>
    <p><b>Research is autonomous; promotion is not.</b> No candidate can enter the official/live arsenal without explicit approval.</p>
    <p><b>Last research:</b> {html.escape(lasttxt)}<br><b>Research runs retained:</b> {runs}<br><b>Shadow candidates retained:</b> {shadows}<br><b>Immutable source snapshots retained:</b> {snaps}</p>
    <h3>Best current shadow candidates</h3>
    <table style="border-collapse:collapse;width:100%"><tr><th align="left">Rule</th><th>Bets</th><th>W-L</th><th>ROI</th><th>Holdout ROI</th><th>Seen</th></tr>{rows}</table>
    <p style="font-size:13px;color:#596273">Candidates are screened with a chronological 70/30 train/holdout split, minimum sample gates, era consistency checks and yearly consistency checks. These are research findings, not official selections.</p>
    </div>'''
    try:
        import ufc_email_watcher as watcher
        watcher.send_email(f"UFC Research Daily — {datetime.now().strftime('%Y-%m-%d')}", body)
        ar.log('Daily research email sent through official watcher transport')
        return True
    except Exception as e:
        ar.log(f'Daily research email through watcher failed: {e}')
        return False

ar.send_digest=send_digest

if __name__=='__main__':
    ar.main()
