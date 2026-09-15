#!/usr/bin/env python3
from pathlib import Path
import re

P = Path('/opt/sports-publisher/sports_publish.py')
s = P.read_text()

MARKER = '# UFC_DASHBOARD_V1'
if MARKER not in s:
    anchor = "def downloads_html(sport):\n"
    if anchor not in s:
        raise SystemExit('downloads_html anchor missing')
    helper = r'''# UFC_DASHBOARD_V1
def ufc_dashboard_record(cards,upcoming):
 w=sum(c.get('result')=='win' for c in cards);l=sum(c.get('result')=='loss' for c in cards);p=sum(c.get('result')=='push' for c in cards)
 priced=[c for c in cards if c.get('profit_units') is not None and c.get('result') in {'win','loss','push'}]
 units=sum(float(c.get('profit_units') or 0) for c in priced)
 win='—' if not (w+l) else f'{w/(w+l):.1%}'
 roi='—' if not priced else f'{units/len(priced):+.1%}'
 record=f'{w}-{l}'+(f'-{p}' if p else '')
 tone='positive' if units>=0 else 'negative'
 return f'''<div class="record-strip"><div class="record-box"><span>Overall record</span><b>{record}</b></div><div class="record-box"><span>Win rate</span><b>{win}</b></div><div class="record-box"><span>Tracked ROI</span><b class="{tone}">{roi}</b></div><div class="record-box"><span>Net units</span><b class="{tone}">{units:+.2f}u</b></div><div class="record-box"><span>Upcoming picks</span><b>{len(upcoming)}</b></div></div>'''

def ufc_results_table(cards):
 completed=sorted([c for c in cards if dt(c['start'])<=datetime.now(timezone.utc)],key=lambda c:dt(c['start']),reverse=True)
 if not completed:return '<div class="empty-compact">No completed tracked selections yet.</div>'
 e=lambda x:esc(str(x or ''))
 rows=[]
 for raw in completed:
  c=dict(raw,**raw.get('tracking',{}))
  when=dt(c['start']).astimezone(NY).strftime('%b %d, %Y')
  mids=', '.join(str(m.get('id','')) for m in c.get('methods',[]) if m.get('id')) or '—'
  price=c.get('price');price_text='—'
  if isinstance(price,(int,float)) and math.isfinite(price) and abs(price)>=100:price_text=f'{price:+g}'
  result=raw.get('result') or 'pending';label={'win':'WIN','loss':'LOSS','push':'PUSH','void':'VOID'}.get(result,'PENDING')
  cls=result if result in {'win','loss','push'} else 'pending'
  units=raw.get('profit_units');pl='—' if units is None else f'{float(units):+.2f}u'
  source=''
  if raw.get('result_source'):source=f'<br><a class="result-source" href="{e(raw.get("result_source"))}">Result source</a>'
  rows.append(f'<tr><td>{e(when)}</td><td><strong>{e(c.get("selection"))}</strong></td><td>{e(c.get("fixture"))}</td><td>{e(mids)}</td><td>{e(price_text)}</td><td class="{cls}">{label}{source}</td><td class="profit">{e(pl)}</td></tr>')
 return '<div class="history-table-wrap"><table class="history-table"><thead><tr><th>Date</th><th>Pick</th><th>Matchup</th><th>Method</th><th>Price</th><th>Result</th><th>P/L</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table></div>'

def ufc_withdrawn_details(cards):
 now=datetime.now(timezone.utc)
 withdrawn=[c for c in cards if dt(c['start'])>now and c.get('withdrawn')]
 if not withdrawn:return ''
 return '<details class="withdrawn"><summary>No longer qualifying / withdrawn ('+str(len(withdrawn))+')</summary><div class="grid">'+''.join(card_html(c) for c in sorted(withdrawn,key=lambda x:dt(x['start'])))+'</div></details>'

'''
    s=s.replace(anchor,helper+anchor,1)

css_anchor="@media(max-width:750px){.grid{grid-template-columns:1fr}main{padding:28px 16px}.card{padding:20px}nav{gap:15px;padding:16px}.stats{gap:24px}}'''"
if css_anchor not in s:
    raise SystemExit('CSS anchor missing')
css_extra=r'''.record-strip{display:grid;grid-template-columns:repeat(5,minmax(120px,1fr));gap:12px;margin:18px 0 30px}.record-box{background:#fff;border:1px solid #dce3ec;border-radius:13px;padding:16px;box-shadow:0 4px 16px rgba(19,35,59,.04)}.record-box span{display:block;color:#627187;font-size:12px;text-transform:uppercase;letter-spacing:.05em;font-weight:700}.record-box b{display:block;margin-top:4px;font-size:24px;line-height:1.15;color:#142137}.record-box .positive{color:#12734e}.record-box .negative{color:#b42318}.section-kicker{color:#167597;font-size:12px;letter-spacing:.09em;text-transform:uppercase;font-weight:800;margin:30px 0 4px}.grid.upcoming-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.history-table-wrap{overflow:auto;background:#fff;border:1px solid #dce3ec;border-radius:12px;box-shadow:0 4px 16px rgba(19,35,59,.035);margin-bottom:28px}.history-table{width:100%;border-collapse:collapse;min-width:850px}.history-table th,.history-table td{text-align:left;padding:11px 12px;border-bottom:1px solid #e7edf3;font-size:13px;vertical-align:middle}.history-table th{background:#eef3f8;color:#41536c;font-size:11px;text-transform:uppercase;letter-spacing:.05em}.history-table tr:last-child td{border-bottom:0}.history-table .win{color:#12734e;font-weight:800}.history-table .loss{color:#b42318;font-weight:800}.history-table .push{color:#765b12;font-weight:800}.history-table .pending{color:#627187;font-weight:700}.history-table .profit{font-variant-numeric:tabular-nums;font-weight:750}.result-source{font-size:11px;font-weight:600}.empty-compact{background:#fff;border:1px dashed #aebdd0;border-radius:12px;padding:22px;color:#627187}.withdrawn{margin-top:22px}.withdrawn summary{font-size:15px;color:#526176}@media(max-width:900px){.record-strip{grid-template-columns:repeat(2,minmax(0,1fr))}.grid.upcoming-grid{grid-template-columns:1fr}}@media(max-width:520px){.record-strip{grid-template-columns:1fr 1fr}.record-box{padding:13px}.record-box b{font-size:20px}}'''
s=s.replace(css_anchor,css_extra+css_anchor,1)

old = """   else:\n    content+='<h2>Upcoming selections</h2><div class=\"grid\">'+''.join(card_html(c) for c in upcoming)+'</div>' if upcoming else '<div class=\"empty\">No upcoming matchups qualify in the latest published scan.</div>'\n    if sport=='ufc':content+=ufc_record_html()\n    if past:content+='<details open><summary>Selection history and results ('+str(len(past))+')</summary><p class=\"muted\">Original recorded selection and price, one unit per fight. Unconfirmed results remain pending. Recovered previews are labelled separately from confirmed email deliveries.</p><div class=\"grid\">'+''.join(card_html(c) for c in past)+'</div></details>'\n"""
new = """   else:\n    if sport=='ufc':\n     content+=ufc_dashboard_record(cards,upcoming)\n     content+='<div class=\"section-kicker\">Current board</div><h2>Upcoming picks</h2>'\n     content+='<div class=\"grid upcoming-grid\">'+''.join(card_html(c) for c in sorted(upcoming,key=lambda x:dt(x['start'])))+'</div>' if upcoming else '<div class=\"empty-compact\">No upcoming matchups qualify in the latest published scan.</div>'\n     content+='<div class=\"section-kicker\">Tracked results</div><h2>Completed selections</h2><p class=\"muted\">One unit per tracked selection at the original recorded moneyline. Pending or void results are excluded from ROI.</p>'+ufc_results_table(cards)+ufc_withdrawn_details(cards)\n    else:\n     content+='<h2>Upcoming selections</h2><div class=\"grid\">'+''.join(card_html(c) for c in upcoming)+'</div>' if upcoming else '<div class=\"empty\">No upcoming matchups qualify in the latest published scan.</div>'\n     if past:content+='<details open><summary>Selection history and results ('+str(len(past))+')</summary><p class=\"muted\">Original recorded selection and price, one unit per fight. Unconfirmed results remain pending. Recovered previews are labelled separately from confirmed email deliveries.</p><div class=\"grid\">'+''.join(card_html(c) for c in past)+'</div></details>'\n"""
if old not in s:
    raise SystemExit('page rendering anchor missing')
s=s.replace(old,new,1)

P.write_text(s)
print('patched',P)
