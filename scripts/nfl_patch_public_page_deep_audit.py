from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
from html import escape
import csv, math, os, re

PAGE = Path(os.environ.get('NFL_PUBLIC_PAGE','/srv/appwiza-sports/public/nfl/index.html'))
PUBLIC = PAGE.parent
CONTEXT = Path(os.environ.get('NFL_BOARD_CONTEXT','nfl/live_2026_current_snapshot/current_board_fresh_context.csv'))
START='<!-- APPWIZA_NFL_DEEP_AUDIT_START -->'
END='<!-- APPWIZA_NFL_DEEP_AUDIT_END -->'

CSS=r'''
.deep-audit-panel{margin:24px 0 34px}.deep-audit-head{background:#f7fbff;border:1px solid #cfe0ef;border-radius:14px;padding:18px 20px;margin-bottom:13px}.deep-audit-head h2{margin:4px 0 6px}.deep-audit-head p{margin:0;color:#5a6a7e;font-size:13px;line-height:1.5}.deep-audit-table-wrap{overflow:auto;background:#fff;border:1px solid #dce3ec;border-radius:13px;box-shadow:0 4px 16px rgba(19,35,59,.04)}.deep-audit-table{width:100%;border-collapse:collapse;min-width:980px}.deep-audit-table th,.deep-audit-table td{padding:11px 12px;text-align:left;border-bottom:1px solid #e7edf3;font-size:13px;vertical-align:top}.deep-audit-table th{background:#eef3f8;color:#41536c;font-size:11px;text-transform:uppercase;letter-spacing:.05em}.deep-audit-table tr:last-child td{border-bottom:0}.da-badge{display:inline-block;padding:5px 9px;border-radius:999px;font-size:11px;font-weight:850;letter-spacing:.04em;text-transform:uppercase}.da-keep{background:#e6f7ef;color:#086b45}.da-veto{background:#fdebea;color:#a32018}.da-stale{background:#edf1f5;color:#536176}.da-monitor{background:#fff6da;color:#765700}.da-sub{display:block;color:#69788b;font-size:11px;line-height:1.4;margin-top:3px}.da-override{margin-top:12px;padding:11px 13px;border-left:4px solid #142137;background:#f2f5f8;color:#42536a;font-size:12px}.da-override strong{color:#142137}.da-value{font-variant-numeric:tabular-nums}.da-kicker{color:#167597;font-size:12px;letter-spacing:.09em;text-transform:uppercase;font-weight:800}.da-summary{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.da-chip{border:1px solid #d8e1eb;background:#fff;border-radius:8px;padding:7px 10px;font-size:12px;color:#40516a}.da-chip b{color:#142137}.deep-veto-callout{margin:16px 0 0;background:#fff4f3;border:1px solid #efc7c4;border-radius:12px;padding:13px 15px;color:#6b3d39;font-size:13px}.deep-veto-callout strong{color:#9d221a}
'''

def f(v):
    try:
        x=float(v); return x if math.isfinite(x) else None
    except Exception:return None

def i(v):
    x=f(v); return int(x) if x is not None else None

def b(v):
    s=str(v).strip().lower()
    if s in {'true','1','yes'}:return True
    if s in {'false','0','no'}:return False
    return None

def fmt(v,d=1,signed=False):
    x=f(v)
    if x is None:return '—'
    return f'{x:+.{d}f}' if signed else f'{x:.{d}f}'

def price(v):
    x=f(v)
    return '—' if x is None else f'{x:+.0f}'

def classify(r):
    status=str(r.get('target_status','')).upper()
    if status!='UPCOMING':
        return 'stale','NOT ACTIONABLE','Authoritative schedule says this row is completed/stale or needs identity review.'
    if b(r.get('preseason_policy_pass')) is False:
        return 'veto','NO BET','Refined veto: fails the validated 2+ preseason-win requirement.'
    games=i(r.get('current_games_available')) or 0
    wins=i(r.get('current_wins')) or 0
    margin=f(r.get('current_point_margin_avg'))
    if b(r.get('preseason_policy_pass')) is True and games and wins>0 and margin is not None and margin>0:
        return 'keep','KEEP','Passes the refined preseason gate; current 2026 evidence is directionally supportive.'
    if b(r.get('preseason_policy_pass')) is True:
        return 'keep','KEEP','Passes the refined preseason gate; early-season results remain context only.'
    return 'monitor','MONITOR','No validated second-stage veto currently applies.'

def coach_names(r):
    return '/'.join([
        r.get('live_coachq_head_coach') or r.get('head_coach') or '—',
        r.get('live_coachq_offensive_coordinator') or r.get('offensive_coordinator') or '—',
        r.get('live_coachq_defensive_coordinator') or r.get('defensive_coordinator') or '—'])

def efficiency(r):
    pe=f(r.get('current_passing_epa_avg')); ru=f(r.get('current_rushing_epa_avg')); cp=f(r.get('current_passing_cpoe_avg'))
    if pe is None and ru is None and cp is None:return '—'
    return f'Pass EPA {fmt(pe,2,True)} · Rush EPA {fmt(ru,2,True)} · CPOE {fmt(cp,2,True)}'

def build(rows):
    table=[]; keeps=[]; vetoes=[]; stale=[]
    for r in rows:
        cls,label,reason=classify(r)
        if cls=='keep': keeps.append(r.get('selection') or r.get('team'))
        elif cls=='veto': vetoes.append(r.get('selection') or r.get('team'))
        elif cls=='stale': stale.append(r.get('selection') or r.get('team'))
        changes=i(r.get('live_coachq_staff_change_count'))
        upgrades=i(r.get('live_coachq_staff_upgrade_count'))
        downs=i(r.get('live_coachq_staff_downgrade_count'))
        mean=f(r.get('live_coachq_staff_quality_mean')); minimum=f(r.get('live_coachq_staff_quality_min'))
        staffmeta=(('stable' if changes==0 else f'{changes} changes') if changes is not None else 'changes —')+f' · mean {fmt(mean,3)} · min {fmt(minimum,3)} · {upgrades or 0}↑/{downs or 0}↓'
        games=i(r.get('current_games_available')) or 0; wins=i(r.get('current_wins')) or 0; losses=i(r.get('current_losses')) or 0
        current='No prior 2026 game' if games==0 else f'{wins}-{losses} · avg margin {fmt(r.get("current_point_margin_avg"),1,True)}'
        preseason=f"{r.get('preseason_record') or '—'} · {'PASS' if b(r.get('preseason_policy_pass')) is True else 'FAIL' if b(r.get('preseason_policy_pass')) is False else '—'}"
        table.append('<tr>'
            f'<td><strong>{escape(r.get("selection") or r.get("team") or "—")}</strong><span class="da-sub">vs {escape(r.get("opponent") or "—")} · Week {escape(str(r.get("schedule_week") or "—"))} · {price(r.get("moneyline"))} · {escape(r.get("method") or "—")}</span></td>'
            f'<td><span class="da-badge da-{cls}">{escape(label)}</span><span class="da-sub">{escape(reason)}</span></td>'
            f'<td>{escape(coach_names(r))}<span class="da-sub">{escape(staffmeta)}</span></td>'
            f'<td class="da-value">{escape(preseason)}</td>'
            f'<td class="da-value">{escape(current)}<span class="da-sub">{escape(efficiency(r))}</span></td>'
            '</tr>')
    chips=(f'<span class="da-chip"><b>{len(keeps)}</b> keep</span>'
           f'<span class="da-chip"><b>{len(vetoes)}</b> refined veto</span>'
           f'<span class="da-chip"><b>{len(stale)}</b> stale/final</span>')
    veto_text=''
    if vetoes:
        veto_text='<div class="deep-veto-callout"><strong>Removed from actionable board by deeper analysis:</strong> '+escape(', '.join(vetoes))+'. The older scanner rule may still appear in a legacy card below until that production rule is formally replaced.</div>'
    return START+f'''<section class="deep-audit-panel"><div class="da-kicker">2026 deep audit</div><div class="deep-audit-head"><h2>Coaching + games already played</h2><p>This second-stage review uses coaching turnover/quality, the validated preseason refinement, authoritative game identity, and 2026 performance available before the target game. Week 1–3 stats are context only; mature rolling methods still require ≥3 prior completed games.</p><div class="da-summary">{chips}</div><div class="da-override"><strong>Deep-audit status supersedes the older scanner card for actionability.</strong> Historical method qualification remains visible for transparency.</div>{veto_text}</div><div class="deep-audit-table-wrap"><table class="deep-audit-table"><thead><tr><th>Selection</th><th>Deep audit</th><th>Coaching context (HC / OC / DC)</th><th>Preseason gate</th><th>2026 evidence</th></tr></thead><tbody>{''.join(table)}</tbody></table></div></section>'''+END

if not PAGE.exists(): raise SystemExit(f'Public NFL page missing: {PAGE}')
if not CONTEXT.exists(): raise SystemExit(f'Fresh board context missing: {CONTEXT}')
with CONTEXT.open(newline='',encoding='utf-8') as fh: rows=list(csv.DictReader(fh))
if not rows: raise SystemExit('Fresh board context is empty')

html=PAGE.read_text()
html=re.sub(re.escape(START)+r'.*?'+re.escape(END),'',html,flags=re.S)
link='<link rel="stylesheet" href="/sports/nfl/deep-audit.css?v=20260915">'
html=re.sub(r'<link rel="stylesheet" href="/sports/nfl/deep-audit\.css[^>]*>','',html)
if '</head>' in html: html=html.replace('</head>',link+'</head>',1)
block=build(rows)
anchors=['<div class="section-kicker">Current board</div>','<h2>Available selections</h2>','<footer>']
for anchor in anchors:
    if anchor in html:
        html=html.replace(anchor,block+anchor,1);break
else:
    html=html.replace('</main>',block+'</main>',1)

tmp=PAGE.with_name(PAGE.name+'.deep-audit.tmp');tmp.write_text(html);tmp.replace(PAGE)
(PUBLIC/'deep-audit.css').write_text(CSS)
print('NFL_DEEP_AUDIT_PATCH',{'rows':len(rows),'page':str(PAGE),'bytes':PAGE.stat().st_size})
