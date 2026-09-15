from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from html import escape as esc
import json, math

ROOT=Path('/srv/appwiza-sports')
PUBLIC=ROOT/'public'/'ufc'
STATE=ROOT/'state'/'ufc.json'
NY=ZoneInfo('America/New_York')

CSS=r'''.record-strip{display:grid;grid-template-columns:repeat(5,minmax(120px,1fr));gap:12px;margin:18px 0 30px}.record-box{background:#fff;border:1px solid #dce3ec;border-radius:13px;padding:16px;box-shadow:0 4px 16px rgba(19,35,59,.04)}.record-box span{display:block;color:#627187;font-size:12px;text-transform:uppercase;letter-spacing:.05em;font-weight:700}.record-box b{display:block;margin-top:4px;font-size:24px;line-height:1.15}.record-box .good{color:#12734e}.record-box .bad{color:#b42318}.section-kicker{color:#167597;font-size:12px;letter-spacing:.09em;text-transform:uppercase;font-weight:800;margin:30px 0 4px}.grid.upcoming-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.history-table-wrap{overflow:auto;background:#fff;border:1px solid #dce3ec;border-radius:12px;box-shadow:0 4px 16px rgba(19,35,59,.035);margin-bottom:28px}.history-table{width:100%;border-collapse:collapse;min-width:850px}.history-table th,.history-table td{text-align:left;padding:11px 12px;border-bottom:1px solid #e7edf3;font-size:13px;vertical-align:middle}.history-table th{background:#eef3f8;color:#41536c;font-size:11px;text-transform:uppercase;letter-spacing:.05em}.history-table tr:last-child td{border-bottom:0}.history-table .win{color:#12734e;font-weight:800}.history-table .loss{color:#b42318;font-weight:800}.history-table .push{color:#765b12;font-weight:800}.history-table .pending{color:#627187;font-weight:700}.history-table .profit{font-variant-numeric:tabular-nums;font-weight:750}.empty-compact{background:#fff;border:1px dashed #aebdd0;border-radius:12px;padding:22px;color:#627187}.withdrawn{margin-top:22px}.withdrawn summary{font-size:15px;color:#526176}@media(max-width:900px){.record-strip{grid-template-columns:repeat(2,minmax(0,1fr))}.grid.upcoming-grid{grid-template-columns:1fr}}@media(max-width:520px){.record-strip{grid-template-columns:1fr 1fr}.record-box{padding:13px}.record-box b{font-size:20px}}'''

def dt(v): return datetime.fromisoformat(str(v).replace('Z','+00:00')).astimezone(timezone.utc)
def fmt_price(v):
    try:
        x=float(v)
        return '—' if not math.isfinite(x) or abs(x)<100 else f'{x:+.0f}'
    except Exception:return '—'

def atomic(path:Path,text:str):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp');tmp.write_text(text);tmp.chmod(0o644);tmp.replace(path)

def record(cards,upcoming):
    settled=[c for c in cards if c.get('result') in {'win','loss','push'}]
    w=sum(c.get('result')=='win' for c in settled);l=sum(c.get('result')=='loss' for c in settled);p=sum(c.get('result')=='push' for c in settled)
    priced=[c for c in settled if c.get('profit_units') is not None]
    units=sum(float(c.get('profit_units') or 0) for c in priced)
    win='—' if not (w+l) else f'{w/(w+l):.1%}';roi='—' if not priced else f'{units/len(priced):+.1%}'
    rec=f'{w}-{l}'+(f'-{p}' if p else '')
    tone='good' if units>=0 else 'bad'
    return f'''<div class="record-strip"><div class="record-box"><span>Overall record</span><b>{rec}</b></div><div class="record-box"><span>Win rate</span><b>{win}</b></div><div class="record-box"><span>Tracked ROI</span><b class="{tone}">{roi}</b></div><div class="record-box"><span>Net units</span><b class="{tone}">{units:+.2f}u</b></div><div class="record-box"><span>Upcoming picks</span><b>{len(upcoming)}</b></div></div>'''

def results(cards):
    now=datetime.now(timezone.utc);done=sorted([c for c in cards if dt(c['start'])<=now],key=lambda c:dt(c['start']),reverse=True)
    if not done:return '<div class="empty-compact">No completed tracked selections yet.</div>'
    rows=[]
    for raw in done:
        c=dict(raw,**raw.get('tracking',{}));r=raw.get('result') or 'pending';label={'win':'WIN','loss':'LOSS','push':'PUSH','void':'VOID'}.get(r,'PENDING');cls=r if r in {'win','loss','push'} else 'pending'
        mids=', '.join(str(m.get('id','')) for m in c.get('methods',[]) if m.get('id')) or '—';units=raw.get('profit_units');pl='—' if units is None else f'{float(units):+.2f}u'
        rows.append(f'<tr><td>{esc(dt(c["start"]).astimezone(NY).strftime("%b %d, %Y"))}</td><td><strong>{esc(str(c.get("selection") or "—"))}</strong></td><td>{esc(str(c.get("fixture") or "—"))}</td><td>{esc(mids)}</td><td>{esc(fmt_price(c.get("price")))}</td><td class="{cls}">{label}</td><td class="profit">{esc(pl)}</td></tr>')
    return '<div class="history-table-wrap"><table class="history-table"><thead><tr><th>Date</th><th>Pick</th><th>Matchup</th><th>Method</th><th>Price</th><th>Result</th><th>P/L</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table></div>'

def safe_enhance(sp):
    try:
        payload=json.loads(STATE.read_text());cards=payload.get('cards',[]);now=datetime.now(timezone.utc)
        upcoming=sorted([c for c in cards if dt(c['start'])>now and not c.get('withdrawn')],key=lambda c:dt(c['start']))
        withdrawn=[c for c in cards if dt(c['start'])>now and c.get('withdrawn')]
        updated=dt(payload.get('updated_at',now.isoformat())).astimezone(NY).strftime('%b %d, %Y · %I:%M %p ET')
        nav='<header><nav><a class="brand" href="/">appwiza.com</a><a href="/sports/">Sports</a><a href="/sports/ufc/" aria-current="page">UFC</a><a href="/sports/nfl/">NFL</a><a href="/sports/boxing/">Boxing</a><a href="#downloads">Downloads</a></nav></header>'
        picks='<div class="grid upcoming-grid">'+''.join(sp.card_html(c) for c in upcoming)+'</div>' if upcoming else '<div class="empty-compact">No upcoming matchups qualify in the latest published scan.</div>'
        withdrawn_html=''
        if withdrawn: withdrawn_html='<details class="withdrawn"><summary>No longer qualifying / withdrawn ('+str(len(withdrawn))+')</summary><div class="grid">'+''.join(sp.card_html(c) for c in withdrawn)+'</div></details>'
        downloads=sp.downloads_html('ufc') or ''
        page=f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="description" content="Appwiza UFC tracked picks, record and results."><title>UFC picks | Appwiza</title><link rel="stylesheet" href="/sports/style.css"><style>{CSS}</style></head><body>{nav}<main><span class="eyebrow">Appwiza · UFC</span><h1>UFC qualifying matchups</h1><p class="sub">Current method-qualified selections, tracked record and settled results.</p><div class="bar"><span>Updated: {esc(updated)}</span><span>{len(upcoming)} upcoming</span></div>{record(cards,upcoming)}<div class="section-kicker">Current board</div><h2>Upcoming picks</h2>{picks}<div class="section-kicker">Tracked results</div><h2>Completed selections</h2><p class="muted">One unit per tracked selection at the original recorded moneyline. Pending or void results are excluded from ROI.</p>{results(cards)}{withdrawn_html}{downloads}<footer>Historical method performance does not guarantee future results. Quotes can move; confirm the current sportsbook price before acting.</footer></main></body></html>'''
        atomic(PUBLIC/'index.html',page)
        print('UFC_PUBLIC_PAGE',{'upcoming':len(upcoming),'completed':sum(dt(c['start'])<=now for c in cards)})
    except Exception as exc:
        print('UFC_PUBLIC_PAGE_ERROR',type(exc).__name__,str(exc))
