"""Presentation-only enhancement for the Appwiza NFL public tracker.

Runs after the existing allowlisted publisher. It does not select bets, alter
method qualification, send email, or mutate the official research logic.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from html import escape as esc
import json, math, re, traceback

ROOT = Path('/srv/appwiza-sports')
PUBLIC = ROOT / 'public' / 'nfl'
STATE = ROOT / 'state' / 'nfl.json'
NY = ZoneInfo('America/New_York')

DASH_CSS = r'''/* Appwiza NFL dashboard additions */
.record-strip{display:grid;grid-template-columns:repeat(5,minmax(120px,1fr));gap:12px;margin:18px 0 28px}.record-box{background:#fff;border:1px solid #dce3ec;border-radius:13px;padding:16px;box-shadow:0 4px 16px rgba(19,35,59,.04)}.record-box span{display:block;color:#627187;font-size:12px;text-transform:uppercase;letter-spacing:.05em;font-weight:700}.record-box b{display:block;margin-top:4px;font-size:24px;line-height:1.15;color:#142137}.record-box .good{color:#12734e}.record-box .bad{color:#b42318}.research-cta{display:flex;align-items:center;justify-content:space-between;gap:18px;background:#eef6ff;border:1px solid #cfe2f7;border-radius:14px;padding:17px 19px;margin:18px 0 30px}.research-cta strong{display:block;font-size:17px}.research-cta p{margin:3px 0 0;color:#5a687d;font-size:13px}.research-cta a{flex:0 0 auto;background:#142137;color:#fff;text-decoration:none;border-radius:9px;padding:10px 15px;font-weight:750}.date-block{margin:26px 0 34px}.date-heading{display:flex;align-items:baseline;gap:10px;margin:0 0 12px;padding-bottom:8px;border-bottom:1px solid #d9e0ea}.date-heading h2{margin:0;font-size:22px}.date-heading span{color:#627187;font-size:13px}.history-table-wrap{overflow:auto;background:#fff;border:1px solid #dce3ec;border-radius:12px;box-shadow:0 4px 16px rgba(19,35,59,.035)}.history-table{width:100%;border-collapse:collapse;min-width:850px}.history-table th,.history-table td{text-align:left;padding:11px 12px;border-bottom:1px solid #e7edf3;font-size:13px;vertical-align:middle}.history-table th{background:#eef3f8;color:#41536c;font-size:11px;text-transform:uppercase;letter-spacing:.05em}.history-table tr:last-child td{border-bottom:0}.history-table .win{color:#12734e;font-weight:800}.history-table .loss{color:#b42318;font-weight:800}.history-table .push{color:#765b12;font-weight:800}.history-table .pending{color:#627187;font-weight:700}.history-table .profit{font-variant-numeric:tabular-nums;font-weight:750}.downloads-block{margin-top:36px;background:#fff;border:1px solid #dce3ec;border-radius:13px;padding:0 18px}.downloads-block>summary{cursor:pointer;list-style:none;font-size:18px;font-weight:800;padding:17px 0}.downloads-block>summary::-webkit-details-marker{display:none}.downloads-block>summary:after{content:'+';float:right;font-size:22px;color:#627187}.downloads-block[open]>summary:after{content:'–'}.downloads-inner{padding:0 0 18px}.withdrawn{margin-top:22px}.withdrawn summary{font-size:15px;color:#526176}.section-kicker{color:#167597;font-size:12px;letter-spacing:.09em;text-transform:uppercase;font-weight:800;margin-bottom:4px}.grid.upcoming-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.empty-compact{background:#fff;border:1px dashed #aebdd0;border-radius:12px;padding:22px;color:#627187}.page-actions{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0 4px}.page-actions a{background:#fff;border:1px solid #d9e0ea;text-decoration:none;border-radius:9px;padding:8px 12px;font-weight:700;font-size:13px}.card .pick{margin-bottom:5px}@media(max-width:900px){.record-strip{grid-template-columns:repeat(2,minmax(0,1fr))}.grid.upcoming-grid{grid-template-columns:1fr}.research-cta{align-items:flex-start;flex-direction:column}}@media(max-width:520px){.record-strip{grid-template-columns:1fr 1fr}.record-box{padding:13px}.record-box b{font-size:20px}.research-cta{padding:15px}}
'''

def _dt(value):
    return datetime.fromisoformat(str(value).replace('Z','+00:00')).astimezone(timezone.utc)

def _atomic(path: Path, text: str, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(text)
    tmp.chmod(mode)
    tmp.replace(path)

def _moneyline(v):
    try:
        x=float(v)
        if not math.isfinite(x) or abs(x)<100:return None
        return x
    except Exception:return None

def _profit(result, price):
    p=_moneyline(price)
    if result=='push':return 0.0
    if result!='win' or p is None:return -1.0 if result=='loss' else None
    return p/100.0 if p>0 else 100.0/abs(p)

def _fmt_price(v):
    p=_moneyline(v)
    return '—' if p is None else f'{p:+.0f}'

def _ledger_entry(card, ledger):
    gid=str(card.get('id','')).removeprefix('NFL:')
    return ledger.get(gid,{}) if isinstance(ledger,dict) else {}

def _result_for(card, ledger):
    e=_ledger_entry(card,ledger)
    st=e.get('settlement') or {}
    return st.get('result')

def _record(cards, ledger):
    settled=[];pending=0
    for c in cards:
        if _dt(c['start'])>datetime.now(timezone.utc):continue
        e=_ledger_entry(c,ledger);r=(e.get('settlement') or {}).get('result')
        if r not in {'win','loss','push'}:
            pending+=1;continue
        price=((e.get('odds') or {}).get('moneyline'))
        u=_profit(r,price)
        settled.append((r,u))
    w=sum(r=='win' for r,_ in settled);l=sum(r=='loss' for r,_ in settled);p=sum(r=='push' for r,_ in settled)
    priced=[u for _,u in settled if u is not None]
    units=sum(priced) if priced else 0.0
    roi=(units/len(priced)) if priced else None
    wp=(w/(w+l)) if w+l else None
    return dict(w=w,l=l,p=p,pending=pending,units=units,roi=roi,win_pct=wp,n=len(settled))

def _history_table(past, ledger):
    rows=[]
    for c in sorted(past,key=lambda x:_dt(x['start']),reverse=True):
        e=_ledger_entry(c,ledger);sett=e.get('settlement') or {};result=sett.get('result') or 'pending'
        odds=e.get('odds') or {};price=odds.get('moneyline',c.get('price'));u=_profit(result,price)
        start=_dt(c['start']).astimezone(NY)
        methods=', '.join(str(m.get('id','')) for m in c.get('methods',[]) if m.get('id')) or '—'
        cls=result if result in {'win','loss','push'} else 'pending'
        label={'win':'WIN','loss':'LOSS','push':'PUSH','pending':'PENDING'}.get(result,'PENDING')
        unit_text='—' if u is None else f'{u:+.2f}u'
        rows.append('<tr>'
            f'<td>{esc(start.strftime("%b %d, %Y"))}</td>'
            f'<td><strong>{esc(c.get("selection"))}</strong></td>'
            f'<td>{esc(c.get("fixture"))}</td>'
            f'<td>{esc(methods)}</td>'
            f'<td>{esc(_fmt_price(price))}</td>'
            f'<td class="{cls}">{label}</td>'
            f'<td class="profit">{esc(unit_text)}</td>'
            '</tr>')
    if not rows:return '<div class="empty-compact">No completed tracked selections yet.</div>'
    return '<div class="history-table-wrap"><table class="history-table"><thead><tr><th>Date</th><th>Pick</th><th>Matchup</th><th>Method</th><th>Price</th><th>Result</th><th>P/L</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table></div>'

def _downloads(sp):
    raw=sp.downloads_html('nfl') or ''
    if not raw:return ''
    raw=re.sub(r'^<section id="downloads"><h2>Research downloads</h2>','',raw)
    raw=re.sub(r'</section>$','',raw)
    return '<details class="downloads-block" id="downloads"><summary>Research downloads</summary><div class="downloads-inner">'+raw+'</div></details>'

def _group_upcoming(upcoming, sp):
    if not upcoming:return '<div class="empty-compact">No current method-qualified selections.</div>'
    groups={}
    for c in sorted(upcoming,key=lambda x:_dt(x['start'])):
        local=_dt(c['start']).astimezone(NY);key=local.date()
        groups.setdefault(key,[]).append(c)
    out=[]
    for day,cards in groups.items():
        d=datetime.combine(day,datetime.min.time(),tzinfo=NY)
        out.append('<section class="date-block"><div class="date-heading"><h2>'+esc(d.strftime('%A, %B %d'))+'</h2><span>'+str(len(cards))+' selection'+('s' if len(cards)!=1 else '')+'</span></div><div class="grid upcoming-grid">'+''.join(sp.card_html(c) for c in cards)+'</div></section>')
    return ''.join(out)

def enhance(now, ledger, sp):
    payload=json.loads(STATE.read_text()) if STATE.exists() else {'cards':[],'updated_at':now.isoformat()}
    cards=payload.get('cards',[]);now_utc=now.astimezone(timezone.utc)
    upcoming=[c for c in cards if _dt(c['start'])>now_utc and not c.get('withdrawn')]
    past=[c for c in cards if _dt(c['start'])<=now_utc]
    withdrawn=[c for c in cards if _dt(c['start'])>now_utc and c.get('withdrawn')]
    rec=_record(past,ledger)
    wp='—' if rec['win_pct'] is None else f"{rec['win_pct']:.1%}"
    roi='—' if rec['roi'] is None else f"{rec['roi']:+.1%}"
    updated=_dt(payload.get('updated_at',now.isoformat())).astimezone(NY).strftime('%b %d, %Y · %I:%M %p ET')
    nav='<header><nav><a class="brand" href="/">appwiza.com</a><a href="/sports/">Sports</a><a href="/sports/ufc/">UFC</a><a href="/sports/nfl/" aria-current="page">NFL</a><a href="/sports/nfl/research/">Research Lab</a><a href="/sports/boxing/">Boxing</a><a href="#downloads">Downloads</a></nav></header>'
    record=f'''<div class="record-strip"><div class="record-box"><span>Overall record</span><b>{rec['w']}-{rec['l']}{('-'+str(rec['p'])) if rec['p'] else ''}</b></div><div class="record-box"><span>Win rate</span><b>{wp}</b></div><div class="record-box"><span>Tracked ROI</span><b class="{'good' if rec['roi'] is not None and rec['roi']>=0 else 'bad'}">{roi}</b></div><div class="record-box"><span>Net units</span><b class="{'good' if rec['units']>=0 else 'bad'}">{rec['units']:+.2f}u</b></div><div class="record-box"><span>Upcoming</span><b>{len(upcoming)}</b></div></div>'''
    research='''<div class="research-cta"><div><strong>NFL Research Lab</strong><p>Shadow methods, holdout performance, veto research and consensus signals live separately from the official tracker.</p></div><a href="/sports/nfl/research/">Open Research Lab →</a></div>'''
    withdrawn_html=''
    if withdrawn:
        withdrawn_html='<details class="withdrawn"><summary>No longer qualifying / withdrawn ('+str(len(withdrawn))+')</summary><div class="grid">'+''.join(sp.card_html(c) for c in withdrawn)+'</div></details>'
    css_version=now.astimezone(NY).strftime('%Y%m%d%H%M')
    page=f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="description" content="Appwiza NFL method watchlist, tracked record and research."><title>NFL watchlist | Appwiza</title><link rel="stylesheet" href="/sports/style.css"><link rel="stylesheet" href="/sports/nfl/dashboard.css?v={css_version}"></head><body>{nav}<main><span class="eyebrow">Appwiza · NFL</span><h1>NFL qualifying matchups</h1><p class="sub">Method-qualified selections used by the NFL scanner, grouped by game date, with recorded prices and historical evidence.</p><div class="bar"><span>Updated: {esc(updated)}</span><span>{len(upcoming)} upcoming · nearest games first</span></div>{record}{research}<div class="section-kicker">Current board</div><h2>Available selections</h2>{_group_upcoming(upcoming,sp)}<div class="section-kicker">Tracked results</div><h2>Completed selections</h2><p class="muted">One unit per tracked selection at the original recorded moneyline. Pending results are excluded from ROI.</p>{_history_table(past,ledger)}{withdrawn_html}{_downloads(sp)}<footer>Historical method performance does not guarantee future results. Research methods remain separate until explicitly promoted.</footer></main></body></html>'''
    _atomic(PUBLIC/'dashboard.css',DASH_CSS)
    _atomic(PUBLIC/'index.html',page)
    return {'upcoming':len(upcoming),'completed':len(past),'record':f"{rec['w']}-{rec['l']}",'roi':rec['roi']}

def safe_enhance(now, ledger, sp):
    try:
        result=enhance(now,ledger,sp)
        print('NFL_PUBLIC_PAGE',json.dumps(result,default=str))
        return result
    except Exception as exc:
        try:
            (PUBLIC/'enhance_error.log').write_text(type(exc).__name__+'\n'+traceback.format_exc())
        except Exception:pass
        print('NFL_PUBLIC_PAGE_ERROR',type(exc).__name__,str(exc))
        return None
