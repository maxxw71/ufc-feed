from pathlib import Path
import os,json,html
import numpy as np,pandas as pd
from datetime import datetime, timezone

REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
OUT=REPO/'nfl/research_page';OUT.mkdir(parents=True,exist_ok=True)
CTX=Path('/home/appwiza-runner/nfl-context-data')
ROOT=Path('/home/anestishkurti92/nfl-predictor-v1')


def esc(x): return html.escape('' if x is None else str(x))
def pct(x):
    try:
        x=float(x)
        if np.isnan(x):return '—'
        return f'{100*x:.1f}%'
    except:return '—'
def roipct(x):
    try:
        x=float(x)
        if np.isnan(x):return '—'
        return f'{100*x:+.1f}%'
    except:return '—'
def read_csv(rel):
    p=REPO/rel
    return pd.read_csv(p) if p.exists() else pd.DataFrame()
def read_json(rel):
    p=REPO/rel
    return json.loads(p.read_text()) if p.exists() else {}
def implied(ml):
    ml=pd.to_numeric(ml,errors='coerce')
    return np.where(ml<0,(-ml)/((-ml)+100),100/(ml+100))
def profit(win,ml):
    ml=pd.to_numeric(ml,errors='coerce');w=pd.to_numeric(win,errors='coerce')
    return np.where(w.eq(1),np.where(ml>0,ml/100,100/np.abs(ml)),-1.0)
def cmask(x,c,o,t):
    if c not in x:return pd.Series(False,index=x.index)
    v=pd.to_numeric(x[c],errors='coerce');t=float(t)
    return {'>=':v>=t,'<=':v<=t,'>':v>t,'<':v<t,'==':v==t}.get(str(o),pd.Series(False,index=x.index))
def method_metrics(x):
    if not len(x):return dict(n=0,wins=0,losses=0,win_pct=np.nan,roi=np.nan)
    return dict(n=len(x),wins=int(x.win.sum()),losses=int(len(x)-x.win.sum()),win_pct=float(x.win.mean()),roi=float(x.profit.mean()))

# Reconstruct H-family win rates from the current REG-only enriched research table so every displayed method has W-L / Win%.
h_display=[]
h=read_csv('nfl/live_candidate_finalization/live_ready.csv')
try:
    d=pd.read_parquet(CTX/'coach_quality_expansion/coach_quality_enriched_team_sides.parquet')
    sched=pd.read_parquet(ROOT/'data/raw/schedules_2006_2026.parquet');gt='game_type' if 'game_type' in sched else 'season_type';reg=set(sched[sched[gt].eq('REG')].game_id.astype(str))
    d=d[d.game_id.astype(str).isin(reg)&d.season.between(2006,2025)].copy();d['win']=pd.to_numeric(d.win,errors='coerce');d['moneyline']=pd.to_numeric(d.moneyline,errors='coerce');d=d[d.win.isin([0,1])&d.moneyline.notna()].copy();d['market_prob']=implied(d.moneyline);d['profit']=profit(d.win,d.moneyline)
    for _,r in h.iterrows():
        x=d[(d.market_prob>=float(r.live_p_lo))&(d.market_prob<float(r.live_p_hi))&(pd.to_numeric(d.prior_games,errors='coerce')>=3)].copy()
        if r.venue=='AWAY':x=x[pd.to_numeric(x.is_home,errors='coerce').ne(1)]
        elif r.venue=='HOME':x=x[pd.to_numeric(x.is_home,errors='coerce').eq(1)]
        for j in [1,2,3]:
            f=r.get(f'feature{j}');o=r.get(f'op{j}');t=r.get(f'threshold{j}')
            if isinstance(f,str) and f and f!='nan' and pd.notna(t):x=x[cmask(x,f,o,t)]
        vf=r.get('hard_veto_feature');vo=r.get('hard_veto_op');vt=r.get('hard_veto_threshold')
        if isinstance(vf,str) and vf and vf!='nan' and pd.notna(vt):x=x[~cmask(x,vf,vo,vt)]
        m=method_metrics(x);ho=method_metrics(x[x.season>=2020 if r.track=='LONG' else x.season>=2021])
        h_display.append(dict(Method=r.candidate_id,Family='H',Status=r.deployment_status,Record=f"{m['wins']}-{m['losses']}",WinPct=m['win_pct'],ROI=m['roi'],HoldoutROI=ho['roi'],Odds=r.live_odds_range,Rule=f"{r.feature1} {r.op1} {r.threshold1}" + (f"; {r.feature2} {r.op2} {r.threshold2}" if isinstance(r.feature2,str) else ''),Veto=(f"{vf} {vo} {vt}" if isinstance(vf,str) and vf and vf!='nan' else '—')))
except Exception as e:
    for _,r in h.iterrows():h_display.append(dict(Method=r.candidate_id,Family='H',Status=r.deployment_status,Record=f"n={int(r.full_n)}",WinPct=np.nan,ROI=r.full_roi,HoldoutROI=r.holdout_roi,Odds=r.live_odds_range,Rule=str(r.feature1),Veto='—'))

# Normalize other families into one research table.
rows=list(h_display)
co=read_csv('nfl/coach_only_final_sieve/final_coach_only_arsenal_sieve.csv')
for _,r in co.iterrows():
    try:n=int(r.base_n);w=round(float(r.base_win_pct)*n);rec=f'{w}-{n-w}'
    except:rec='—'
    rows.append(dict(Method=r.method_id,Family='CO',Status=r.status,Record=rec,WinPct=r.base_win_pct,ROI=r.base_roi,HoldoutROI=r.holdout_roi,Odds='See method registry',Rule='Coach-only signal',Veto=(f"{r.veto_feature} {r.veto_op} {r.veto_threshold}" if pd.notna(r.veto_feature) else '—')))
rs=read_csv('nfl/top3_timing_final_audit/final_methods.csv')
for _,r in rs.iterrows():rows.append(dict(Method=r.method_id,Family='RS',Status=r.status,Record=f"{int(r.wins)}-{int(r.losses)}",WinPct=r.win_pct,ROI=r.roi,HoldoutROI=r.holdout_roi,Odds=r.odds_range,Rule=r['name'],Veto=r.single_veto))
us=read_csv('nfl/unique_situational_deep_dissection/final_sieve.csv')
for _,r in us.iterrows():rows.append(dict(Method=r.method_id,Family='US',Status=r.status,Record=r.final_record,WinPct=r.final_win_pct,ROI=r.final_roi,HoldoutROI=r.final_holdout_roi,Odds=r.final_odds,Rule=r.concept,Veto=r.vetoes if pd.notna(r.vetoes) and str(r.vetoes) else '—'))
sb=read_csv('nfl/streak_priority_deep_dissection/final_sieve.csv')
if len(sb):
    for _,r in sb.iterrows():rows.append(dict(Method=r.method_id,Family='SB',Status=r.status,Record=r.final_record,WinPct=r.final_win_pct,ROI=r.final_roi,HoldoutROI=r.final_holdout_roi,Odds=r.final_odds,Rule=r.concept,Veto=r.vetoes if pd.notna(r.vetoes) and str(r.vetoes) else '—'))
else:
    sbp=read_csv('nfl/streak_bounceback_division_discovery/preferred.csv').head(25)
    for i,(_,r) in enumerate(sbp.iterrows(),1):rows.append(dict(Method=f'SB{i:03d}',Family='SB',Status='DISCOVERY',Record=f"{int(r.wins)}-{int(r.n-r.wins)}",WinPct=r.win_pct,ROI=r.roi,HoldoutROI=r.hold_roi,Odds=r.band,Rule=r.conditions,Veto='Pending deep dissection'))
research=pd.DataFrame(rows)

legacy=pd.concat([read_csv('nfl/legacy_live_home_opener_exact/summary.csv'),read_csv('nfl/legacy_live_secondary_exact/summary.csv')],ignore_index=True)
cons1=read_json('nfl/coach_consensus_priority.json');cons2=read_json('nfl/rs_h_consensus_priority.json')

def table(df,cols,labels=None,cls='research-table'):
    if df is None or not len(df):return '<p class="muted">No results published yet.</p>'
    labels=labels or cols
    th=''.join(f'<th>{esc(l)}</th>' for l in labels);body=[]
    for _,r in df.iterrows():
        cells=[]
        for c in cols:
            v=r.get(c,'')
            if c in ['WinPct','win_pct','positive_season_ratio']:v=pct(v)
            elif c in ['ROI','HoldoutROI','roi','recent_roi','holdout_roi','final_roi','final_holdout_roi']:v=roipct(v)
            cells.append(f'<td>{esc(v)}</td>')
        body.append('<tr>'+''.join(cells)+'</tr>')
    return f'<div class="table-wrap"><table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'

def consensus_cards():
    items=[]
    for p in cons1.get('pairs',[]):
        m=p.get('with_coach_veto',p.get('raw',{}));items.append(('CO+H',f"{p.get('coach_method')} + {p.get('football_method')}",m.get('wins'),m.get('losses'),m.get('win_pct'),m.get('roi'),m.get('holdout_roi')))
    for p in cons2.get('priority',[]):items.append(('RS+H',f"{p.get('rs_method')} + {p.get('h_method')}",p.get('wins'),p.get('losses'),p.get('win_pct'),p.get('roi'),p.get('holdout_roi')))
    out=[]
    for fam,name,w,l,wp,roi,ho in items:
        out.append(f'<div class="card"><span class="pill">{esc(fam)}</span><h3>{esc(name)}</h3><div class="big">{esc(w)}-{esc(l)} · {pct(wp)}</div><div>ROI <b>{roipct(roi)}</b> · Holdout <b>{roipct(ho)}</b></div></div>')
    return ''.join(out) or '<p class="muted">No priority consensus yet.</p>'

legacy_html=''
if len(legacy):
    legacy2=pd.DataFrame({'Method':legacy.method,'Record':[f"{int(w)}-{int(l)}" for w,l in zip(legacy.wins,legacy.losses)],'WinPct':legacy.win_pct,'ROI':legacy.roi,'RecentROI':legacy.recent_roi,'PositiveSeasons':[f"{int(p)}/{int(a)}" for p,a in zip(legacy.positive_seasons,legacy.active_seasons)]})
    legacy_html=table(legacy2,['Method','Record','WinPct','ROI','RecentROI','PositiveSeasons'],['Method','W-L','Win %','ROI','2020–25 ROI','Positive seasons'])

family_counts=research.groupby('Family').size().to_dict() if len(research) else {}
updated=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

page=f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NFL Research Lab | Appwiza</title>
<style>
:root{{--bg:#07111f;--panel:#0d1a2b;--panel2:#101f33;--line:#24364e;--text:#e9f0f8;--muted:#95a7bd;--green:#41d39f;--orange:#ffac66;--red:#ff6f7d;--blue:#6fb6ff}}*{{box-sizing:border-box}}body{{margin:0;background:linear-gradient(180deg,#07111f,#081523 30%,#06101b);color:var(--text);font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif}}a{{color:var(--blue);text-decoration:none}}.wrap{{max-width:1500px;margin:auto;padding:28px 22px 60px}}.top{{display:flex;gap:16px;justify-content:space-between;align-items:center;flex-wrap:wrap}}h1{{font-size:clamp(30px,4vw,52px);margin:8px 0}}h2{{margin-top:34px}}.badge{{background:#3a2512;border:1px solid #805326;color:#ffd7aa;padding:8px 12px;border-radius:999px;font-weight:800}}.sub,.muted{{color:var(--muted)}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin:20px 0}}.card{{background:linear-gradient(145deg,var(--panel),var(--panel2));border:1px solid var(--line);border-radius:14px;padding:16px;box-shadow:0 8px 30px #0003}}.big{{font-size:24px;font-weight:800;margin:8px 0}}.pill{{display:inline-block;font-size:12px;font-weight:800;background:#142a42;border:1px solid #284866;border-radius:999px;padding:4px 8px;color:#b9d9ff}}.notice{{background:#102519;border:1px solid #275b3a;padding:14px 16px;border-radius:12px;color:#b9f3d1}}.controls{{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}}input,select{{background:#0d1a2b;border:1px solid var(--line);color:var(--text);padding:10px 12px;border-radius:9px}}.table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:12px;background:#091522}}table{{border-collapse:collapse;width:100%;min-width:980px}}th,td{{padding:10px 12px;border-bottom:1px solid #172b42;text-align:left;font-size:13px;vertical-align:top}}th{{position:sticky;top:0;background:#10233a;color:#c9ddf5;z-index:1}}tr:hover td{{background:#0d1c2d}}.footer{{margin-top:40px;color:var(--muted);font-size:13px}}@media(max-width:700px){{.wrap{{padding:18px 10px 40px}}}}
</style></head><body><main class="wrap"><div class="top"><div><div class="badge">RESEARCH ONLY — NOT LIVE BETS</div><h1>NFL Research Lab</h1><div class="sub">Experimental methods, validation, holdout performance, veto research and independent consensus. Updated {updated}.</div></div><div><a href="/nfl/">← Official NFL Tracker</a></div></div>
<div class="notice"><b>Separation rule:</b> nothing on this page is automatically added to the official /nfl tracker, email alerts or bet tracker. A method moves live only after the research/holdout review is frozen and explicitly promoted.</div>
<h2>Research inventory</h2><div class="grid">{''.join(f'<div class="card"><span class="pill">{esc(k)}</span><div class="big">{v}</div><div class="muted">tracked research methods</div></div>' for k,v in family_counts.items())}</div>
<h2>Priority independent consensus</h2><div class="grid">{consensus_cards()}</div>
<h2>Current legacy live methods — historical audit</h2><p class="muted">Shown for reference. These are separate from the experimental research families below.</p>{legacy_html}
<h2>Research methods</h2><div class="controls"><input id="q" placeholder="Search method, family, rule or veto"><select id="fam"><option value="">All families</option>{''.join(f'<option>{esc(k)}</option>' for k in sorted(family_counts))}</select><select id="status"><option value="">All statuses</option><option>LIVE_READY</option><option>SHADOW_READY</option><option>CO_CORE_READY_WITH_VETO</option><option>CO_CORE_READY_UNFILTERED</option><option>WATCH</option><option>DISCOVERY</option></select></div>
{table(research,['Method','Family','Status','Record','WinPct','ROI','HoldoutROI','Odds','Rule','Veto'],['Method','Family','Status','W-L / n','Win %','ROI','Holdout ROI','Odds','Signal','Veto(s)'],'research-table')}
<h2>Research protocol</h2><div class="grid"><div class="card"><h3>Pregame only</h3><p class="muted">Signals must be knowable before kickoff. Regular-season-only and maturity gates are enforced where applicable.</p></div><div class="card"><h3>Holdout confirmation</h3><p class="muted">Price windows and veto stacks are selected before holdout. Attractive filters that fail later years are rejected.</p></div><div class="card"><h3>Multi-veto allowed</h3><p class="muted">More than one veto is permitted when incremental improvement survives train, validation and frozen holdout without collapsing sample size.</p></div><div class="card"><h3>Win % + ROI</h3><p class="muted">Every method shows historical W-L / win rate alongside ROI. Historical win rate is not presented as a calibrated probability for the next game.</p></div></div>
<div class="footer">Appwiza NFL Research Lab · This page is a research dashboard, not a guarantee of profitability.</div></main>
<script>const q=document.getElementById('q'),fam=document.getElementById('fam'),st=document.getElementById('status');function filter(){{const qq=q.value.toLowerCase(),ff=fam.value.toLowerCase(),ss=st.value.toLowerCase();document.querySelectorAll('.research-table tbody tr').forEach(r=>{{const t=r.innerText.toLowerCase();const c=r.children;const f=c[1]?.innerText.toLowerCase()||'';const s=c[2]?.innerText.toLowerCase()||'';r.style.display=(!qq||t.includes(qq))&&(!ff||f===ff)&&(!ss||s===ss)?'':'none';}})}}[q,fam,st].forEach(x=>x.addEventListener('input',filter));</script></body></html>'''
(OUT/'index.html').write_text(page)
print(f'Wrote {OUT/"index.html"} with {len(research)} research rows and {len(legacy)} legacy methods')
