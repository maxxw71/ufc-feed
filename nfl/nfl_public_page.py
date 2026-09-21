"""Presentation enhancement for the Appwiza NFL public tracker.

Runs after the existing allowlisted publisher. It does not change the research
method registry or send email. It *does* surface the separate deep-audit layer
and can move a scanner-qualified card out of the actionable board when a
validated refinement veto applies or the authoritative game identity is stale.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from html import escape as esc
import csv, json, math, re, traceback

ROOT = Path('/srv/appwiza-sports')
PUBLIC = ROOT / 'public' / 'nfl'
STATE = ROOT / 'state' / 'nfl.json'
LIVE_DIR = Path(__file__).resolve().parent
AUDIT_CSV = LIVE_DIR / 'page_context' / 'current_board_fresh_context.csv'
NY = ZoneInfo('America/New_York')
PAGE_CONTRACT='NFL_PUBLIC_CANONICAL_V2_20260919'

DASH_CSS = r'''/* Appwiza NFL dashboard additions */
.record-strip{display:grid;grid-template-columns:repeat(5,minmax(120px,1fr));gap:12px;margin:18px 0 28px}.record-box{background:#fff;border:1px solid #dce3ec;border-radius:13px;padding:16px;box-shadow:0 4px 16px rgba(19,35,59,.04)}.record-box span{display:block;color:#627187;font-size:12px;text-transform:uppercase;letter-spacing:.05em;font-weight:700}.record-box b{display:block;margin-top:4px;font-size:24px;line-height:1.15;color:#142137}.record-box .good{color:#12734e}.record-box .bad{color:#b42318}.research-cta{display:flex;align-items:center;justify-content:space-between;gap:18px;background:#eef6ff;border:1px solid #cfe2f7;border-radius:14px;padding:17px 19px;margin:18px 0 30px}.research-cta strong{display:block;font-size:17px}.research-cta p{margin:3px 0 0;color:#5a687d;font-size:13px}.research-cta a{flex:0 0 auto;background:#142137;color:#fff;text-decoration:none;border-radius:9px;padding:10px 15px;font-weight:750}.date-block{margin:26px 0 34px}.date-heading{display:flex;align-items:baseline;gap:10px;margin:0 0 12px;padding-bottom:8px;border-bottom:1px solid #d9e0ea}.date-heading h2{margin:0;font-size:22px}.date-heading span{color:#627187;font-size:13px}.history-table-wrap,.audit-table-wrap{overflow:auto;background:#fff;border:1px solid #dce3ec;border-radius:12px;box-shadow:0 4px 16px rgba(19,35,59,.035)}.history-table,.audit-table{width:100%;border-collapse:collapse;min-width:850px}.history-table th,.history-table td,.audit-table th,.audit-table td{text-align:left;padding:11px 12px;border-bottom:1px solid #e7edf3;font-size:13px;vertical-align:middle}.history-table th,.audit-table th{background:#eef3f8;color:#41536c;font-size:11px;text-transform:uppercase;letter-spacing:.05em}.history-table tr:last-child td,.audit-table tr:last-child td{border-bottom:0}.history-table .win{color:#12734e;font-weight:800}.history-table .loss{color:#b42318;font-weight:800}.history-table .push{color:#765b12;font-weight:800}.history-table .pending{color:#627187;font-weight:700}.history-table .profit{font-variant-numeric:tabular-nums;font-weight:750}.downloads-block{margin-top:36px;background:#fff;border:1px solid #dce3ec;border-radius:13px;padding:0 18px}.downloads-block>summary{cursor:pointer;list-style:none;font-size:18px;font-weight:800;padding:17px 0}.downloads-block>summary::-webkit-details-marker{display:none}.downloads-block>summary:after{content:'+';float:right;font-size:22px;color:#627187}.downloads-block[open]>summary:after{content:'–'}.downloads-inner{padding:0 0 18px}.withdrawn{margin-top:22px}.withdrawn summary{font-size:15px;color:#526176}.section-kicker{color:#167597;font-size:12px;letter-spacing:.09em;text-transform:uppercase;font-weight:800;margin-bottom:4px}.grid.upcoming-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.empty-compact{background:#fff;border:1px dashed #aebdd0;border-radius:12px;padding:22px;color:#627187}.method-key{margin-top:36px;background:#fff;border:1px solid #dce3ec;border-radius:13px;padding:0 18px;box-shadow:0 4px 16px rgba(19,35,59,.035)}.method-key>summary{cursor:pointer;list-style:none;display:flex;justify-content:space-between;align-items:center;gap:12px;font-size:18px;font-weight:850;padding:17px 0}.method-key>summary::-webkit-details-marker{display:none}.method-key>summary:after{content:'+';font-size:22px;color:#627187}.method-key[open]>summary:after{content:'–'}.method-key-intro{color:#657489;font-size:12px;line-height:1.6;margin:-3px 0 12px}.method-key-subhead{font-size:12px;text-transform:uppercase;letter-spacing:.08em;font-weight:850;color:#167597;margin:16px 0 8px}.method-key-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px 12px;padding:0 0 8px}.method-key-item{display:flex;gap:10px;align-items:flex-start;border:1px solid #e4eaf1;border-radius:10px;padding:11px 12px;background:#fafcfe}.method-code{flex:0 0 auto;min-width:44px;text-align:center;background:#e8f3fb;color:#126f91;border-radius:7px;padding:5px 6px;font-size:11px;font-weight:900;letter-spacing:.03em}.method-key-item strong{display:block;font-size:13px;color:#263851}.method-key-item p{margin:3px 0 0;color:#657489;font-size:11px;line-height:1.45}.method-status{display:inline-block;margin-top:5px;border-radius:999px;padding:3px 7px;font-size:10px;font-weight:850;background:#f2f4f7;color:#5c6676}.method-status.live{background:#e7f7ef;color:#0b6b46}.method-status.shadow{background:#fff4d9;color:#7a5a00}.page-actions{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0 4px}.page-actions a{background:#fff;border:1px solid #d9e0ea;text-decoration:none;border-radius:9px;padding:8px 12px;font-weight:700;font-size:13px}.card .pick{margin-bottom:5px}.audit-section{margin:8px 0 34px}.audit-intro{display:flex;justify-content:space-between;gap:18px;align-items:flex-start;background:#f8fbfe;border:1px solid #d8e5f0;border-radius:13px;padding:16px 18px;margin:12px 0}.audit-intro p{margin:4px 0 0;color:#5f6e82;font-size:13px;max-width:850px}.audit-badge{display:inline-flex;align-items:center;border-radius:999px;padding:5px 9px;font-size:11px;font-weight:850;letter-spacing:.04em;text-transform:uppercase;white-space:nowrap}.audit-badge.keep{background:#e7f7ef;color:#0b6b46}.audit-badge.veto{background:#fdecec;color:#a22118}.audit-badge.stale{background:#eef1f5;color:#566477}.audit-badge.monitor{background:#fff7df;color:#805d00}.audit-main{font-weight:800;color:#142137}.audit-sub{display:block;color:#68778a;font-size:11px;margin-top:3px;line-height:1.35}.audit-context{margin-top:10px;border-top:1px solid #e2e9f0;padding-top:10px;display:grid;grid-template-columns:1fr 1fr;gap:8px 12px;font-size:12px;color:#59697d}.audit-context b{color:#25364e}.audit-context .wide{grid-column:1/-1}.audited-card{min-width:0}.audited-card>.card{height:auto}.veto-board{margin:18px 0 32px}.veto-card{background:#fff;border:1px solid #efc4c1;border-left:4px solid #b42318;border-radius:12px;padding:15px 16px;margin:10px 0}.veto-card h3{margin:0 0 5px;font-size:17px}.veto-card p{margin:4px 0;color:#59697d;font-size:13px}.veto-card strong{color:#a22118}.audit-table .metric{font-variant-numeric:tabular-nums}.audit-note{color:#6b798b;font-size:12px;margin-top:9px}.deep-audit-label{display:inline-block;background:#142137;color:#fff;border-radius:6px;padding:3px 7px;font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.06em;margin-right:7px}@media(max-width:900px){.record-strip{grid-template-columns:repeat(2,minmax(0,1fr))}.grid.upcoming-grid{grid-template-columns:1fr}.method-key-grid{grid-template-columns:1fr}.research-cta,.audit-intro{align-items:flex-start;flex-direction:column}.audit-context{grid-template-columns:1fr}}@media(max-width:520px){.record-strip{grid-template-columns:1fr 1fr}.record-box{padding:13px}.record-box b{font-size:20px}.research-cta{padding:15px}}
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

def _f(v):
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except Exception:return None

def _i(v):
    x=_f(v)
    return int(x) if x is not None else None

def _bool(v):
    if isinstance(v,bool):return v
    s=str(v).strip().lower()
    if s in {'true','1','yes','y'}:return True
    if s in {'false','0','no','n'}:return False
    return None

def _pct(v, digits=1):
    x=_f(v)
    return '—' if x is None else f'{100*x:.{digits}f}%'

def _num(v, digits=2, signed=False):
    x=_f(v)
    if x is None:return '—'
    return f'{x:+.{digits}f}' if signed else f'{x:.{digits}f}'

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

def _load_audits():
    if not AUDIT_CSV.exists():return []
    try:
        with AUDIT_CSV.open(newline='',encoding='utf-8') as f:
            return list(csv.DictReader(f))
    except Exception:
        return []

def _audit_key(row):
    return str(row.get('selection') or row.get('team') or '').strip().lower()

def _audit_map(rows):
    out={}
    for r in rows:
        for key in {_audit_key(r),str(r.get('team','')).strip().lower()}:
            if key:out[key]=r
    return out

def _row_for_card(card, amap):
    selected=str(card.get('selection') or '').strip().lower()
    if not selected:return None
    if selected in amap:return amap[selected]
    seen=set()
    for r in amap.values():
        marker=id(r)
        if marker in seen:continue
        seen.add(marker)
        labels=[str(r.get('selection') or '').strip().lower(),str(r.get('team') or '').strip().lower()]
        for label in labels:
            if label and (selected==label or (len(label)>=3 and re.search(r'(?<![a-z])'+re.escape(label)+r'(?![a-z])',selected))):
                return r
    return None

def _audit_action(row):
    # Public actionability is fail-closed. A missing deep-audit row or required
    # refinement value is a NO BET, never a scanner-qualified/monitor fallback.
    if not row:return ('veto','NO BET','Missing fresh deep-audit row; fail closed.')
    status=str(row.get('target_status','')).upper()
    if status!='UPCOMING':
        return ('stale','Not actionable','Authoritative schedule says this row is completed, stale, or needs identity review.')
    preseason=_bool(row.get('preseason_policy_pass'))
    if preseason is None:
        return ('veto','NO BET','Required preseason refinement is missing; fail closed.')
    if preseason is False:
        return ('veto','NO BET','Refined veto: selected team did not reach the validated 2+ preseason-win requirement.')
    games=_i(row.get('current_games_available')) or 0
    wins=_i(row.get('current_wins')) or 0
    margin=_f(row.get('current_point_margin_avg'))
    if games and wins>0 and margin is not None and margin>0:
        return ('keep','KEEP','Passes the refined preseason gate and current-season evidence is directionally supportive.')
    return ('keep','KEEP','Passes the required fail-closed refinement; early-season results remain context, not a mature rolling signal.')

def _coach_text(row):
    hc=row.get('live_coachq_head_coach') or row.get('head_coach') or '—'
    oc=row.get('live_coachq_offensive_coordinator') or row.get('offensive_coordinator') or '—'
    dc=row.get('live_coachq_defensive_coordinator') or row.get('defensive_coordinator') or '—'
    changes=_i(row.get('live_coachq_staff_change_count'))
    mean=_f(row.get('live_coachq_staff_quality_mean'))
    return f'HC {hc} · OC {oc} · DC {dc} · '+(('stable staff' if changes==0 else f'{changes} major staff changes') if changes is not None else 'change count —')+f' · quality mean {_num(mean,3)}'

def _season_text(row):
    g=_i(row.get('current_games_available')) or 0
    w=_i(row.get('current_wins')) or 0
    l=_i(row.get('current_losses')) or 0
    m=_f(row.get('current_point_margin_avg'))
    if g<=0:return 'No completed 2026 game before this target.'
    return f'2026 prior record {w}-{l} · avg margin {_num(m,1,True)}'

def _eff_text(row):
    pe=_f(row.get('current_passing_epa_avg'));repa=_f(row.get('current_rushing_epa_avg'));cpoe=_f(row.get('current_passing_cpoe_avg'))
    if pe is None and repa is None and cpoe is None:return 'Current-season efficiency: —'
    return f'Pass EPA {_num(pe,2,True)} · Rush EPA {_num(repa,2,True)} · CPOE {_num(cpoe,2,True)}'

def _audit_context_html(row):
    if not row:return ''
    klass,label,reason=_audit_action(row)
    upgrades=_i(row.get('live_coachq_staff_upgrade_count'));downs=_i(row.get('live_coachq_staff_downgrade_count'))
    coach_delta='—' if upgrades is None and downs is None else f'{upgrades or 0} upgrade / {downs or 0} downgrade'
    preseason=f"{row.get('preseason_record') or '—'} · gate {'PASS' if _bool(row.get('preseason_policy_pass')) is True else 'FAIL' if _bool(row.get('preseason_policy_pass')) is False else '—'}"
    return f'''<div class="audit-context"><div class="wide"><span class="audit-badge {klass}">{esc(label)}</span> <span class="audit-sub">{esc(reason)}</span></div><div><b>Coaching context</b><span class="audit-sub">{esc(_coach_text(row))}</span></div><div><b>Replacement model</b><span class="audit-sub">{esc(coach_delta)}</span></div><div><b>Preseason</b><span class="audit-sub">{esc(preseason)}</span></div><div><b>2026 so far</b><span class="audit-sub">{esc(_season_text(row))}</span></div><div class="wide"><b>Early efficiency</b><span class="audit-sub">{esc(_eff_text(row))}</span></div></div>'''

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

def _method_key():
    live=[
        ('M1','Home opener · Run defense + enriched fail-closed gate','Top-10 prior-season run defense, prior-season record at least .500, 2+ preseason wins, and the validated enriched M1 veto must be available and pass. Missing enriched data = no bet.'),
        ('M2','Home opener · .500 record + larger edge','Prior-season record at least .500, top-10 prior run defense, 2+ preseason wins, and a winning-percentage advantage greater than 12.5 points. M2 is independent of the M1 enriched veto.'),
        ('M3','Week 1 · Defense + offense filter','Week 1 home opener with prior-season pass defense top 10, sack/QB-hit rank top 16, and offense rank 24 or better.'),
        ('M4','Late season · Away favorite','Week 11+ away favorite with a large pass-defense edge, strong OL continuity and a validated price/protection/rest gate.'),
    ]
    shadow=[
        ('H002','Explosive defense + punt returns','Road underdogs combining explosive-play defense and recent punt-return edge; includes a dome veto.','Week 4+'),
        ('H003','Third down + pass rush','Road underdogs with third-down and sack/QB-hit edges; includes a pass-defense EPA veto.','Week 4+'),
        ('H008','Third down + kick returns','Underdogs combining third-down edge with a recent kick-return average advantage.','Week 4+'),
        ('H017','Pass rush + explosive defense','Road underdogs combining recent pressure with explosive-play defense; includes a travel veto.','Week 4+'),
        ('H027','Punt returns + efficiency','Road underdogs with a strong punt-return edge plus recent success-rate edge; includes a DC-tenure veto.','Week 4+'),
        ('H029','Pass-rush defense edge','Road underdogs with a large defensive sack/QB-hit rank edge; includes a punt-return veto.','Week 4+'),
        ('H033','Punt return edge','Road underdogs driven by a large recent punt-return average edge.','Week 4+'),
        ('H036','Explosive + pass defense','Road underdogs with strong explosive-play and pass-EPA defensive edges; includes a travel veto.','Week 4+'),
        ('H005','Third down + offense EPA','Underdogs combining third-down edge with a strong offensive EPA/play rank edge; includes an OC-experience veto.','Week 7+'),
        ('H010','Third down + passing EPA','Road underdogs combining third-down edge with a strong passing-EPA rank edge; includes a DC-context veto.','Week 7+'),
        ('H028','Pass rush + passing EPA','Road underdogs combining recent pressure and passing-EPA edge; includes a time-zone/travel veto.','Week 7+'),
        ('H035','Pass defense + punt net','Road underdogs pairing pass-defense EPA edge with recent punt-net edge; includes an OC-tenure veto.','Week 7+'),
    ]
    live_rows=''.join('<div class="method-key-item"><span class="method-code">'+esc(mid)+'</span><div><strong>'+esc(title)+'</strong><p>'+esc(desc)+'</p><span class="method-status live">LIVE / PRODUCTION</span></div></div>' for mid,title,desc in live)
    shadow_rows=''.join('<div class="method-key-item"><span class="method-code">'+esc(mid)+'</span><div><strong>'+esc(title)+'</strong><p>'+esc(desc)+'</p><span class="method-status shadow">RESEARCH / SHADOW · '+esc(start)+'</span></div></div>' for mid,title,desc,start in shadow)
    return '<details class="method-key" id="method-key"><summary><span>Method Key · NFL</span></summary><p class="method-key-intro">Live M-methods can create official selections. H-series methods are researched and prospectively tracked but remain separate from the live tracker until explicitly promoted.</p><div class="method-key-subhead">Live methods</div><div class="method-key-grid">'+live_rows+'</div><div class="method-key-subhead">H-series research / shadow methods</div><div class="method-key-grid">'+shadow_rows+'</div></details>'

def _downloads(sp):
    raw=sp.downloads_html('nfl') or ''
    if not raw:return ''
    raw=re.sub(r'^<section id="downloads"><h2>Research downloads</h2>','',raw)
    raw=re.sub(r'</section>$','',raw)
    return '<details class="downloads-block" id="downloads"><summary>Research downloads</summary><div class="downloads-inner">'+raw+'</div></details>'

def _card_with_audit(card, sp, amap):
    row=_row_for_card(card,amap)
    return '<div class="audited-card">'+sp.card_html(card)+_audit_context_html(row)+'</div>'

def _group_upcoming(upcoming, sp, amap):
    if not upcoming:return '<div class="empty-compact">No actionable selections after the current deep-audit filters.</div>'
    groups={}
    for c in sorted(upcoming,key=lambda x:_dt(x['start'])):
        local=_dt(c['start']).astimezone(NY);key=local.date()
        groups.setdefault(key,[]).append(c)
    out=[]
    for day,cards in groups.items():
        d=datetime.combine(day,datetime.min.time(),tzinfo=NY)
        out.append('<section class="date-block"><div class="date-heading"><h2>'+esc(d.strftime('%A, %B %d'))+'</h2><span>'+str(len(cards))+' selection'+('s' if len(cards)!=1 else '')+'</span></div><div class="grid upcoming-grid">'+''.join(_card_with_audit(c,sp,amap) for c in cards)+'</div></section>')
    return ''.join(out)

def _deep_audit_table(rows):
    if not rows:return '<div class="empty-compact">Fresh 2026 deep-audit context has not been published yet.</div>'
    body=[]
    order={'UPCOMING':0,'WEEK_MISMATCH_REVIEW':1,'COMPLETED_STALE_BOARD':2}
    for r in sorted(rows,key=lambda x:(order.get(str(x.get('target_status','')),5),_i(x.get('schedule_week')) or 99,str(x.get('selection','')))):
        klass,label,reason=_audit_action(r)
        games=_i(r.get('current_games_available')) or 0;w=_i(r.get('current_wins')) or 0;l=_i(r.get('current_losses')) or 0
        changes=_i(r.get('live_coachq_staff_change_count'))
        mean=_f(r.get('live_coachq_staff_quality_mean'));minimum=_f(r.get('live_coachq_staff_quality_min'))
        hc=r.get('live_coachq_head_coach') or '—';oc=r.get('live_coachq_offensive_coordinator') or '—';dc=r.get('live_coachq_defensive_coordinator') or '—'
        coach=f'{hc} / {oc} / {dc}'
        coach_sub=(('stable' if changes==0 else f'{changes} changes') if changes is not None else 'changes —')+f' · mean {_num(mean,3)} · min {_num(minimum,3)}'
        pre=f"{r.get('preseason_record') or '—'} / {'PASS' if _bool(r.get('preseason_policy_pass')) is True else 'FAIL' if _bool(r.get('preseason_policy_pass')) is False else '—'}"
        current='—' if games==0 else f'{w}-{l} · margin {_num(r.get("current_point_margin_avg"),1,True)}'
        eff=_eff_text(r)
        body.append('<tr>'
            f'<td><strong>{esc(r.get("selection") or r.get("team") or "—")}</strong><span class="audit-sub">vs {esc(r.get("opponent") or "—")} · W{esc(str(r.get("schedule_week") or "—"))} · {_fmt_price(r.get("moneyline"))}</span></td>'
            f'<td><span class="audit-badge {klass}">{esc(label)}</span><span class="audit-sub">{esc(reason)}</span></td>'
            f'<td>{esc(coach)}<span class="audit-sub">{esc(coach_sub)}</span></td>'
            f'<td class="metric">{esc(pre)}</td>'
            f'<td class="metric">{esc(current)}<span class="audit-sub">{esc(eff)}</span></td>'
            '</tr>')
    return '<div class="audit-table-wrap"><table class="audit-table"><thead><tr><th>Selection</th><th>Deep audit</th><th>Coaching context</th><th>Preseason</th><th>2026 evidence</th></tr></thead><tbody>'+''.join(body)+'</tbody></table></div><p class="audit-note">Week 1–3 performance is supporting context only. Mature rolling-stat methods still require at least three completed games. Coach-quality scores use only seasons before 2026.</p>'

def _veto_board(cards, sp, amap):
    if not cards:return ''
    items=[]
    for c in cards:
        r=_row_for_card(c,amap);_,label,reason=_audit_action(r)
        items.append(f'''<div class="veto-card"><h3>{esc(c.get('selection') or 'Selection')} <span class="audit-badge veto">{esc(label)}</span></h3><p>{esc(c.get('fixture') or '')} · {_fmt_price(c.get('price'))}</p><p><strong>Refined veto:</strong> {esc(reason.replace('Refined veto: ','') if reason else '')}</p>{_audit_context_html(r)}</div>''')
    return '<section class="veto-board"><div class="section-kicker">Removed after deeper analysis</div><h2>Refined veto / no bet</h2><p class="muted">These may still satisfy an older scanner rule, but the newer validated refinement removes them from the actionable board.</p>'+''.join(items)+'</section>'

def enhance(now, ledger, sp):
    payload=json.loads(STATE.read_text()) if STATE.exists() else {'cards':[],'updated_at':now.isoformat()}
    cards=payload.get('cards',[]);now_utc=now.astimezone(timezone.utc)
    audits=_load_audits();amap=_audit_map(audits)
    raw_upcoming=[c for c in cards if _dt(c['start'])>now_utc and not c.get('withdrawn')]
    past=[c for c in cards if _dt(c['start'])<=now_utc]
    withdrawn=[c for c in cards if _dt(c['start'])>now_utc and c.get('withdrawn')]
    actionable=[];vetoed=[];stale=[]
    for c in raw_upcoming:
        r=_row_for_card(c,amap);klass,_,_=_audit_action(r)
        if klass=='veto':vetoed.append(c)
        elif klass=='stale':stale.append(c)
        else:actionable.append(c)
    rec=_record(past,ledger)
    wp='—' if rec['win_pct'] is None else f"{rec['win_pct']:.1%}"
    roi='—' if rec['roi'] is None else f"{rec['roi']:+.1%}"
    updated=_dt(payload.get('updated_at',now.isoformat())).astimezone(NY).strftime('%b %d, %Y · %I:%M %p ET')
    nav='<header><nav><a class="brand" href="/">appwiza.com</a><a href="/sports/">Sports</a><a href="/sports/ufc/">UFC</a><a href="/sports/nfl/" aria-current="page">NFL</a><a href="/sports/nfl/research/">Research Lab</a><a href="/sports/boxing/">Boxing</a><a href="#downloads">Downloads</a></nav></header>'
    record=f'''<div class="record-strip"><div class="record-box"><span>Overall record</span><b>{rec['w']}-{rec['l']}{('-'+str(rec['p'])) if rec['p'] else ''}</b></div><div class="record-box"><span>Win rate</span><b>{wp}</b></div><div class="record-box"><span>Tracked ROI</span><b class="{'good' if rec['roi'] is not None and rec['roi']>=0 else 'bad'}">{roi}</b></div><div class="record-box"><span>Net units</span><b class="{'good' if rec['units']>=0 else 'bad'}">{rec['units']:+.2f}u</b></div><div class="record-box"><span>Actionable now</span><b>{len(actionable)}</b></div></div>'''
    research='''<div class="research-cta"><div><strong>NFL Research Lab</strong><p>Shadow methods, holdout performance, veto research and consensus signals live separately from the official tracker.</p></div><a href="/sports/nfl/research/">Open Research Lab →</a></div>'''
    audit_section='<section class="audit-section"><div class="section-kicker">2026 deep audit</div><h2>Coaching + current-season context</h2><div class="audit-intro"><div><strong>Scanner qualification is no longer the final word.</strong><p>This layer adds coaching turnover/quality, validated preseason refinements, authoritative game identity, and 2026 performance already played. A validated veto can move an older scanner-qualified pick out of the actionable board.</p></div><span class="deep-audit-label">Fresh context</span></div>'+_deep_audit_table(audits)+'</section>'
    withdrawn_html=''
    if withdrawn:
        withdrawn_html='<details class="withdrawn"><summary>No longer qualifying / withdrawn ('+str(len(withdrawn))+')</summary><div class="grid">'+''.join(sp.card_html(c) for c in withdrawn)+'</div></details>'
    if stale:
        withdrawn_html+='<details class="withdrawn"><summary>Suppressed stale/identity rows ('+str(len(stale))+')</summary><div class="grid">'+''.join(_card_with_audit(c,sp,amap) for c in stale)+'</div></details>'
    css_version=now.astimezone(NY).strftime('%Y%m%d%H%M')
    page=f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="description" content="Appwiza NFL method watchlist, tracked record and research."><title>NFL watchlist | Appwiza</title><link rel="stylesheet" href="/sports/style.css"><link rel="stylesheet" href="/sports/nfl/dashboard.css?v={css_version}"></head><body>{nav}<main><span class="eyebrow">Appwiza · NFL</span><h1>NFL qualifying matchups</h1><p class="sub">Method-qualified selections with a second-stage 2026 deep audit using coaching context, preseason refinements and games already played.</p><div class="bar"><span>Updated: {esc(updated)}</span><span>{len(actionable)} actionable · {len(vetoed)} refined veto · {len(stale)} stale suppressed</span></div>{record}{research}{audit_section}<div class="section-kicker">Current board</div><h2>Available selections</h2>{_group_upcoming(actionable,sp,amap)}{_veto_board(vetoed,sp,amap)}<div class="section-kicker">Tracked results</div><h2>Completed selections</h2><p class="muted">One unit per tracked selection at the original recorded moneyline. Pending results are excluded from ROI.</p>{_history_table(past,ledger)}{withdrawn_html}{_method_key()}{_downloads(sp)}<footer>Historical method performance does not guarantee future results. Weeks 1–3 current-season stats are context only; mature rolling methods require ≥3 prior games. Research methods remain separate until explicitly promoted.</footer></main></body></html>'''
    _atomic(PUBLIC/'dashboard.css',DASH_CSS)
    _atomic(PUBLIC/'index.html',page)
    return {'actionable':len(actionable),'vetoed':len(vetoed),'stale':len(stale),'completed':len(past),'record':f"{rec['w']}-{rec['l']}",'roi':rec['roi'],'audit_rows':len(audits)}

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
