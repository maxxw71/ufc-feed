from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from html import escape as esc
import json, math, re

ROOT=Path('/srv/appwiza-sports')
PUBLIC=ROOT/'public'/'ufc'
STATE=ROOT/'state'/'ufc.json'
NY=ZoneInfo('America/New_York')

DASH_CSS=r'''/* Appwiza UFC dashboard additions — mirrors NFL structure */
.record-strip{display:grid;grid-template-columns:repeat(5,minmax(120px,1fr));gap:12px;margin:18px 0 28px}.record-box{background:#fff;border:1px solid #dce3ec;border-radius:13px;padding:16px;box-shadow:0 4px 16px rgba(19,35,59,.04)}.record-box span{display:block;color:#627187;font-size:12px;text-transform:uppercase;letter-spacing:.05em;font-weight:700}.record-box b{display:block;margin-top:4px;font-size:24px;line-height:1.15;color:#142137}.record-box .good{color:#12734e}.record-box .bad{color:#b42318}.date-block{margin:26px 0 34px}.date-heading{display:flex;align-items:baseline;gap:10px;margin:0 0 12px;padding-bottom:8px;border-bottom:1px solid #d9e0ea}.date-heading h2{margin:0;font-size:22px}.date-heading span{color:#627187;font-size:13px}.history-table-wrap{overflow:auto;background:#fff;border:1px solid #dce3ec;border-radius:12px;box-shadow:0 4px 16px rgba(19,35,59,.035)}.history-table{width:100%;border-collapse:collapse;min-width:850px}.history-table th,.history-table td{text-align:left;padding:11px 12px;border-bottom:1px solid #e7edf3;font-size:13px;vertical-align:middle}.history-table th{background:#eef3f8;color:#41536c;font-size:11px;text-transform:uppercase;letter-spacing:.05em}.history-table tr:last-child td{border-bottom:0}.history-table .win{color:#12734e;font-weight:800}.history-table .loss{color:#b42318;font-weight:800}.history-table .push{color:#765b12;font-weight:800}.history-table .pending{color:#627187;font-weight:700}.history-table .profit{font-variant-numeric:tabular-nums;font-weight:750}.downloads-block{margin-top:36px;background:#fff;border:1px solid #dce3ec;border-radius:13px;padding:0 18px}.downloads-block>summary{cursor:pointer;list-style:none;font-size:18px;font-weight:800;padding:17px 0}.downloads-block>summary::-webkit-details-marker{display:none}.downloads-block>summary:after{content:'+';float:right;font-size:22px;color:#627187}.downloads-block[open]>summary:after{content:'–'}.downloads-inner{padding:0 0 18px}.withdrawn{margin-top:22px}.withdrawn summary{font-size:15px;color:#526176}.section-kicker{color:#167597;font-size:12px;letter-spacing:.09em;text-transform:uppercase;font-weight:800;margin-bottom:4px}.grid.upcoming-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.empty-compact{background:#fff;border:1px dashed #aebdd0;border-radius:12px;padding:22px;color:#627187}.card .pick{margin-bottom:5px}@media(max-width:900px){.record-strip{grid-template-columns:repeat(2,minmax(0,1fr))}.grid.upcoming-grid{grid-template-columns:1fr}}@media(max-width:520px){.record-strip{grid-template-columns:1fr 1fr}.record-box{padding:13px}.record-box b{font-size:20px}}
'''

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
    return f'''<div class="record-strip"><div class="record-box"><span>Overall record</span><b>{rec}</b></div><div class="record-box"><span>Win rate</span><b>{win}</b></div><div class="record-box"><span>Tracked ROI</span><b class="{tone}">{roi}</b></div><div class="record-box"><span>Net units</span><b class="{tone}">{units:+.2f}u</b></div><div class="record-box"><span>Actionable now</span><b>{len(upcoming)}</b></div></div>'''

def group_upcoming(upcoming,sp):
    if not upcoming:return '<div class="empty-compact">No actionable UFC selections in the latest published scan.</div>'
    groups={}
    for c in sorted(upcoming,key=lambda x:dt(x['start'])):
        local=dt(c['start']).astimezone(NY);groups.setdefault(local.date(),[]).append(c)
    out=[]
    for day,cards in groups.items():
        d=datetime.combine(day,datetime.min.time(),tzinfo=NY)
        out.append('<section class="date-block"><div class="date-heading"><h2>'+esc(d.strftime('%A, %B %d'))+'</h2><span>'+str(len(cards))+' selection'+('s' if len(cards)!=1 else '')+'</span></div><div class="grid upcoming-grid">'+''.join(sp.card_html(c) for c in cards)+'</div></section>')
    return ''.join(out)

def results(cards):
    now=datetime.now(timezone.utc);done=sorted([c for c in cards if dt(c['start'])<=now],key=lambda c:dt(c['start']),reverse=True)
    if not done:return '<div class="empty-compact">No completed tracked selections yet.</div>'
    rows=[]
    for raw in done:
        c=dict(raw,**raw.get('tracking',{}));r=raw.get('result') or 'pending';label={'win':'WIN','loss':'LOSS','push':'PUSH','void':'VOID'}.get(r,'PENDING');cls=r if r in {'win','loss','push'} else 'pending'
        mids=', '.join(str(m.get('id','')) for m in c.get('methods',[]) if m.get('id')) or '—';units=raw.get('profit_units');pl='—' if units is None else f'{float(units):+.2f}u'
        rows.append(f'<tr><td>{esc(dt(c["start"]).astimezone(NY).strftime("%b %d, %Y"))}</td><td><strong>{esc(str(c.get("selection") or "—"))}</strong></td><td>{esc(str(c.get("fixture") or "—"))}</td><td>{esc(mids)}</td><td>{esc(fmt_price(c.get("price")))}</td><td class="{cls}">{label}</td><td class="profit">{esc(pl)}</td></tr>')
    return '<div class="history-table-wrap"><table class="history-table"><thead><tr><th>Date</th><th>Pick</th><th>Matchup</th><th>Method</th><th>Price</th><th>Result</th><th>P/L</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table></div>'

def downloads(sp):
    raw=sp.downloads_html('ufc') or ''
    if not raw:return ''
    raw=re.sub(r'^<section id="downloads"><h2>Research downloads</h2>','',raw)
    raw=re.sub(r'</section>$','',raw)
    return '<details class="downloads-block" id="downloads"><summary>Research downloads</summary><div class="downloads-inner">'+raw+'</div></details>'

def safe_enhance(sp):
    try:
        payload=json.loads(STATE.read_text());cards=payload.get('cards',[]);now=datetime.now(timezone.utc)
        upcoming=[c for c in cards if dt(c['start'])>now and not c.get('withdrawn')]
        withdrawn=[c for c in cards if dt(c['start'])>now and c.get('withdrawn')]
        updated=dt(payload.get('updated_at',now.isoformat())).astimezone(NY).strftime('%b %d, %Y · %I:%M %p ET')
        nav='<header><nav><a class="brand" href="/">appwiza.com</a><a href="/sports/">Sports</a><a href="/sports/ufc/" aria-current="page">UFC</a><a href="/sports/nfl/">NFL</a><a href="/sports/boxing/">Boxing</a><a href="#downloads">Downloads</a></nav></header>'
        withdrawn_html=''
        if withdrawn: withdrawn_html='<details class="withdrawn"><summary>No longer qualifying / withdrawn ('+str(len(withdrawn))+')</summary><div class="grid">'+''.join(sp.card_html(c) for c in withdrawn)+'</div></details>'
        page=f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="description" content="Appwiza UFC method watchlist and tracked results."><title>UFC watchlist | Appwiza</title><link rel="stylesheet" href="/sports/style.css"><style>{DASH_CSS}</style></head><body>{nav}<main><span class="eyebrow">Appwiza · UFC</span><h1>UFC qualifying matchups</h1><p class="sub">Method-qualified selections and tracked results from the live UFC model.</p><div class="bar"><span>Updated: {esc(updated)}</span><span>{len(upcoming)} actionable</span></div>{record(cards,upcoming)}<div class="section-kicker">Current board</div><h2>Available selections</h2>{group_upcoming(upcoming,sp)}<div class="section-kicker">Tracked results</div><h2>Completed selections</h2><p class="muted">One unit per tracked selection at the original recorded moneyline. Pending results are excluded from ROI.</p>{results(cards)}{withdrawn_html}{downloads(sp)}<footer>Historical method performance does not guarantee future results. Quotes can move; confirm the current sportsbook price before acting.</footer></main></body></html>'''
        atomic(PUBLIC/'index.html',page)
        print('UFC_PUBLIC_PAGE',{'actionable':len(upcoming),'completed':sum(dt(c['start'])<=now for c in cards),'nfl_structure':True})
    except Exception as exc:
        print('UFC_PUBLIC_PAGE_ERROR',type(exc).__name__,str(exc))
