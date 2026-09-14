from pathlib import Path
import json, os, re, importlib.util, sys
import numpy as np
import pandas as pd

# Re-audit the currently live legacy email rules against the richer REG-only dataset.
ROOT=Path('/home/anestishkurti92/nfl-predictor-v1')
R=ROOT/'research_v2'
CTX=Path('/home/appwiza-runner/nfl-context-data')
OUT=CTX/'legacy_live_method_reaudit'; OUT.mkdir(parents=True,exist_ok=True)
SCHED=ROOT/'data/raw/schedules_2006_2026.parquet'
BASE=CTX/'coaching_everything'/'coaching_enriched_team_sides_2006_2025.parquet'
ODDS=R/'historical_odds/all_open_close_quotes.csv'

ALIASES={'SD':'LAC','OAK':'LV','STL':'LA','LAR':'LA'}
def num(x): return pd.to_numeric(x,errors='coerce')
def profit(win,odds):
    odds=num(odds); win=num(win)
    return np.where(win.eq(1),np.where(odds>0,odds/100,100/np.abs(odds)),-1.0)
def metrics(x):
    if not len(x): return {'n':0,'wins':0,'losses':0,'win_pct':np.nan,'roi':np.nan,'units':0.0}
    return {'n':int(len(x)),'wins':int(x.win.sum()),'losses':int(len(x)-x.win.sum()),'win_pct':float(x.win.mean()),'roi':float(x.profit_units.mean()),'units':float(x.profit_units.sum())}

d=pd.read_parquet(BASE)
d=d[d.season.between(2006,2025)].copy()
d=d[num(d.win).isin([0,1]) & num(d.moneyline).notna()].copy()
d['win']=num(d.win); d['moneyline']=num(d.moneyline); d['profit_units']=profit(d.win,d.moneyline)
if 'prior_games' in d.columns:d=d[num(d.prior_games)>=0].copy()

s=pd.read_parquet(SCHED)
if 'game_type' in s.columns: s=s[s.game_type.eq('REG')]
elif 'season_type' in s.columns: s=s[s.season_type.eq('REG')]
reg_ids=set(s.game_id.astype(str))
d=d[d.game_id.astype(str).isin(reg_ids)].copy()

scanner=R/'nfl_home_opener_scanner.py'; late=R/'late_season_method.py'
scanner_text=scanner.read_text() if scanner.exists() else ''
late_text=late.read_text() if late.exists() else ''
(OUT/'current_live_source_snapshot.txt').write_text('=== nfl_home_opener_scanner.py ===\n'+scanner_text+'\n\n=== late_season_method.py ===\n'+late_text)

rule_names=[]
for name in ['original','stricter','pass_defense_offense24']:
    if name in scanner_text: rule_names.append(name)
late_rule='late_season'
m=re.search(r"RULE\s*=\s*['\"]([^'\"]+)",late_text)
if m: late_rule=m.group(1)

sch=s.copy()
for c in ['home_team','away_team']:
    if c in sch: sch[c]=sch[c].replace(ALIASES)
rows=[]
for season in sorted(x for x in sch.season.dropna().unique() if 2007<=int(x)<=2025):
    cur=sch[sch.season.eq(season)].sort_values(['week','game_id']).copy()
    openers=cur.drop_duplicates('home_team',keep='first')
    prior=sch[sch.season.eq(season-1)].copy()
    if prior.empty: continue
    sides=[]
    for side in ['home','away']:
        if side=='home':
            z=prior[['game_id','home_team','away_team','home_score','away_score']].rename(columns={'home_team':'team','away_team':'opp','home_score':'pts','away_score':'opp_pts'})
        else:
            z=prior[['game_id','away_team','home_team','away_score','home_score']].rename(columns={'away_team':'team','home_team':'opp','away_score':'pts','home_score':'opp_pts'})
        sides.append(z)
    ps=pd.concat(sides,ignore_index=True)
    ps['winx']=(num(ps.pts)>num(ps.opp_pts)).astype(float); ps['tiex']=(num(ps.pts)==num(ps.opp_pts)).astype(float)
    rec=ps.groupby('team').agg(w=('winx','sum'),t=('tiex','sum'),n=('game_id','count'))
    rec['pct']=(rec.w+.5*rec.t)/rec.n
    pd0=d[d.season.eq(season-1)].copy()
    rankmap={}
    if {'pre_def_allowed_rush_epa_per_carry','team'}.issubset(pd0.columns):
        last=pd0.sort_values('week').groupby('team').tail(1)
        v=num(last.pre_def_allowed_rush_epa_per_carry)
        rankmap=dict(zip(last.team, v.rank(method='average').values))
    elif 'rank_def_allowed_rush_epa_per_carry' in pd0.columns:
        last=pd0.sort_values('week').groupby('team').tail(1)
        rankmap=dict(zip(last.team,num(last.rank_def_allowed_rush_epa_per_carry)))
    for _,g in openers.iterrows():
        ht=g.home_team; at=g.away_team
        if ht not in rec.index or at not in rec.index or ht not in rankmap: continue
        gap=float(rec.at[ht,'pct']-rec.at[at,'pct']); rr=float(rankmap[ht])
        q=d[(d.game_id.astype(str)==str(g.game_id)) & d.team.eq(ht)]
        if q.empty: continue
        rr0=q.iloc[0].copy()
        for rid,ok in [('original',rr<=10 and gap>0),('stricter',rr<=10 and gap>.125)]:
            if ok:
                rows.append({'method':rid,'game_id':g.game_id,'season':season,'week':int(g.week),'team':ht,'opponent':at,'moneyline':float(rr0.moneyline),'win':float(rr0.win),'profit_units':float(rr0.profit_units),'record_gap':gap,'run_def_rank':rr})

for _,q in d[d.week.eq(1) & num(d.is_home).eq(1)].iterrows():
    candidates={
      'pass_rank':['prior_rank_def_allowed_pass_epa_per_dropback','last_rank_def_allowed_pass_epa_per_dropback','rank_def_allowed_pass_epa_per_dropback'],
      'hit_rank':['prior_rank_def_allowed_sack_or_hit_rate','last_rank_def_allowed_sack_or_hit_rate','rank_def_allowed_sack_or_hit_rate'],
      'off_rank':['prior_rank_off_epa_per_play','last_rank_off_epa_per_play','rank_off_epa_per_play']}
    vals={}
    for k,cs in candidates.items():
        vals[k]=next((q[c] for c in cs if c in d.columns and pd.notna(q[c])),np.nan)
    if all(pd.notna(vals[k]) for k in vals) and float(vals['pass_rank'])<=10 and float(vals['hit_rank'])<=16 and float(vals['off_rank'])<=24:
        rows.append({'method':'pass_defense_offense24','game_id':q.game_id,'season':int(q.season),'week':int(q.week),'team':q.team,'opponent':q.opponent,'moneyline':float(q.moneyline),'win':float(q.win),'profit_units':float(q.profit_units),'record_gap':np.nan,'run_def_rank':np.nan})

bets=pd.DataFrame(rows)
if len(bets): bets=bets.drop_duplicates(['method','game_id','team'])

late_summary={'rule':late_rule,'status':'source_captured'}
try:
    spec=importlib.util.spec_from_file_location('late_live',late)
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    late_summary['constants']={k:getattr(mod,k) for k in dir(mod) if k.isupper() and isinstance(getattr(mod,k),(str,int,float,bool,tuple,list))}
except Exception as e:
    late_summary['import_error']=type(e).__name__+': '+str(e)

summary_rows=[]; yearly=[]
if len(bets):
    for method,x in bets.groupby('method'):
        m=metrics(x); older=metrics(x[x.season<=2019]); recent=metrics(x[x.season>=2020])
        yrs=x.groupby('season').agg(n=('win','size'),wins=('win','sum'),units=('profit_units','sum'))
        yrs['win_pct']=yrs.wins/yrs.n; yrs['roi']=yrs.units/yrs.n
        active=yrs[yrs.n>=2]
        pos=int((active.units>0).sum()); act=len(active)
        summary_rows.append({'method':method,**m,'older_n':older['n'],'older_win_pct':older['win_pct'],'older_roi':older['roi'],'recent_n':recent['n'],'recent_win_pct':recent['win_pct'],'recent_roi':recent['roi'],'recent_roi_delta':recent['roi']-older['roi'] if older['n'] and recent['n'] else np.nan,'positive_seasons':pos,'active_seasons':act,'positive_season_ratio':pos/act if act else np.nan})
        for _,r in yrs.reset_index().iterrows(): yearly.append({'method':method,'season':int(r.season),'n':int(r.n),'wins':int(r.wins),'win_pct':float(r.win_pct),'roi':float(r.roi),'units':float(r.units)})

pd.DataFrame(summary_rows).to_csv(OUT/'legacy_live_methods_summary.csv',index=False)
pd.DataFrame(yearly).to_csv(OUT/'legacy_live_methods_by_season.csv',index=False)
if len(bets): bets.to_csv(OUT/'legacy_live_method_bets.csv',index=False)
(OUT/'late_method_source_meta.json').write_text(json.dumps(late_summary,indent=2,default=str))

trace=[]
state=R/'home_opener_email_state'
for p in [state/'ledger.json',state/'status.json',state/'preview.txt']:
    if not p.exists(): continue
    try:
        if p.suffix=='.json':
            obj=json.loads(p.read_text()); text=json.dumps(obj,indent=2)
        else:text=p.read_text()
        chunks=[line for line in text.splitlines() if 'LAC' in line or 'Chargers' in line or 'ARI' in line or 'Cardinals' in line]
        if chunks: trace.append({'file':str(p),'matches':chunks[:100]})
    except Exception as e:trace.append({'file':str(p),'error':str(e)})
(OUT/'chargers_2026_trace.json').write_text(json.dumps(trace,indent=2))

report=['NFL CURRENT LIVE LEGACY METHOD RE-AUDIT','',f'live_rules_detected={rule_names+[late_rule]}','REG-only historical reconstruction; win percentage is reported alongside ROI.','']
for r in summary_rows:
    report.append(f"{r['method']} | n={r['n']} W-L={r['wins']}-{r['losses']} win={100*r['win_pct']:.1f}% ROI={100*r['roi']:+.1f}% | 2006-19 win={100*r['older_win_pct']:.1f}% ROI={100*r['older_roi']:+.1f}% | 2020-25 win={100*r['recent_win_pct']:.1f}% ROI={100*r['recent_roi']:+.1f}% | positive seasons={r['positive_seasons']}/{r['active_seasons']}")
report+=['','LATE METHOD META',json.dumps(late_summary,indent=2,default=str),'','CHARGERS TRACE',json.dumps(trace,indent=2)]
(OUT/'report.txt').write_text('\n'.join(report)+'\n')
print('\n'.join(report[:40]))
