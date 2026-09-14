from pathlib import Path
import json, os
import pandas as pd
import numpy as np
R=Path('/home/anestishkurti92/nfl-predictor-v1/research_v2')
OUT=Path('/home/appwiza-runner/nfl-context-data/legacy_live_home_opener_exact');OUT.mkdir(parents=True,exist_ok=True)
F=R/'home_opener_stress/fixed_rule_equity.csv'
d=pd.read_csv(F,low_memory=False)
# This file is the frozen historical candidate set used by the live home-opener rule.
d=d[d.game_type.eq('REG') & d.completed.eq(True)].copy()
d['edge']=pd.to_numeric(d.prior_win_pct,errors='coerce')-pd.to_numeric(d.opponent_prior_win_pct,errors='coerce')
d['run_rank']=pd.to_numeric(d.last_rank_def_allowed_rush_epa_per_carry,errors='coerce')
d['ml']=pd.to_numeric(d.home_moneyline,errors='coerce')
d['w']=pd.to_numeric(d.win,errors='coerce')
d['p']=pd.to_numeric(d.profit,errors='coerce')

def calc(x):
    x=x[x.ml.notna() & x.w.isin([0,1])].copy()
    if not len(x): return {'n':0,'wins':0,'losses':0,'win_pct':None,'roi':None,'units':0}
    return {'n':len(x),'wins':int(x.w.sum()),'losses':int(len(x)-x.w.sum()),'win_pct':float(x.w.mean()),'roi':float(x.p.mean()),'units':float(x.p.sum())}

def seasons(x):
    z=x[x.ml.notna() & x.w.isin([0,1])].groupby('season').agg(n=('w','size'),wins=('w','sum'),units=('p','sum'))
    z['win_pct']=z.wins/z.n;z['roi']=z.units/z.n
    active=z[z.n>=2]
    return z, int((active.units>0).sum()), len(active)

rules={
 'original': d[(d.run_rank<=10)&(d.edge>0)].copy(),
 'stricter': d[(d.run_rank<=10)&(d.edge>.125)].copy(),
}
rows=[]; yr=[]
for name,x in rules.items():
    allm=calc(x); old=calc(x[x.season<=2019]); recent=calc(x[x.season>=2020]); recent3=calc(x[x.season>=2023]); y,pos,act=seasons(x)
    rows.append({'method':name,**allm,'older_n':old['n'],'older_win_pct':old['win_pct'],'older_roi':old['roi'],'recent_n':recent['n'],'recent_win_pct':recent['win_pct'],'recent_roi':recent['roi'],'recent3_n':recent3['n'],'recent3_win_pct':recent3['win_pct'],'recent3_roi':recent3['roi'],'positive_seasons':pos,'active_seasons':act,'positive_season_ratio':pos/act if act else None})
    y=y.reset_index();y['method']=name;yr.append(y)
    x.to_csv(OUT/f'{name}_bets.csv',index=False)
pd.DataFrame(rows).to_csv(OUT/'summary.csv',index=False)
pd.concat(yr,ignore_index=True).to_csv(OUT/'by_season.csv',index=False)
# Verify whether frozen file itself is already exactly the original rule candidate set.
verify={'fixed_rule_rows':len(d),'all_rows_meet_original_conditions':bool(((d.run_rank<=10)&(d.edge>0)).all()),'min_edge':float(d.edge.min()),'max_run_rank':float(d.run_rank.max())}
(OUT/'verification.json').write_text(json.dumps(verify,indent=2))
lines=['LIVE HOME-OPENER LEGACY RULE RE-AUDIT','',json.dumps(verify,indent=2),'']
for r in rows:
    lines.append(f"{r['method']} | {r['wins']}-{r['losses']} ({100*r['win_pct']:.1f}% win) | n={r['n']} ROI={100*r['roi']:+.1f}% | 2007-19 win={100*r['older_win_pct']:.1f}% ROI={100*r['older_roi']:+.1f}% | 2020-25 win={100*r['recent_win_pct']:.1f}% ROI={100*r['recent_roi']:+.1f}% | 2023-25 win={100*r['recent3_win_pct']:.1f}% ROI={100*r['recent3_roi']:+.1f}% | positive seasons={r['positive_seasons']}/{r['active_seasons']}")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n')
print('\n'.join(lines))
