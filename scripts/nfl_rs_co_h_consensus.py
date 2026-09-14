from pathlib import Path
import os,json
import numpy as np,pandas as pd
REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'));CTX=Path('/home/appwiza-runner/nfl-context-data');OUT=CTX/'rs_co_h_consensus';OUT.mkdir(parents=True,exist_ok=True)
DATA=CTX/'coach_quality_expansion'/'coach_quality_enriched_team_sides.parquet'
RS=REPO/'nfl/top3_timing_final_audit/shadow_manifest.json';COBASE=REPO/'nfl/coach_only_deployment_audit/live_ready.csv';COFINAL=REPO/'nfl/coach_only_final_sieve/final_coach_only_arsenal_sieve.csv';H=REPO/'nfl/live_candidate_finalization/live_ready.csv'
def num(x):return pd.to_numeric(x,errors='coerce')
def implied(ml):ml=num(ml);return np.where(ml<0,(-ml)/((-ml)+100),100/(ml+100))
def profit(win,ml):ml=num(ml);return np.where(num(win).eq(1),np.where(ml>0,ml/100,100/np.abs(ml)),-1.)
def met(x):
 if not len(x):return dict(n=0,wins=0,losses=0,win_pct=np.nan,roi=np.nan,units=0.)
 return dict(n=len(x),wins=int(x.win.sum()),losses=int(len(x)-x.win.sum()),win_pct=float(x.win.mean()),roi=float(x.profit.mean()),units=float(x.profit.sum()))
def cmask(x,c,o,t):
 if c not in x:return pd.Series(False,index=x.index)
 v=num(x[c]);t=float(t)
 return {'>=':v>=t,'<=':v<=t,'>':v>t,'<':v<t,'==':v==t}.get(str(o),pd.Series(False,index=x.index))
def apply(x,conds):
 m=pd.Series(True,index=x.index)
 for c,o,t in conds:m &= cmask(x,c,o,t)
 return m
def keyset(x):return set((x.game_id.astype(str)+'|'+x.team.astype(str)).tolist())
def season_quality(x):
 if not len(x):return dict(pos_seasons=0,active_seasons=0,pos_ratio=np.nan,max_season_share=np.nan,loo_min_roi=np.nan,loo_all_profitable=False)
 y=x.groupby('season').agg(n=('win','size'),units=('profit','sum'));pos=int((y.units>0).sum());act=len(y);vals=[]
 for s in y.index:vals.append(met(x[x.season.ne(s)])['roi'])
 return dict(pos_seasons=pos,active_seasons=act,pos_ratio=pos/act if act else np.nan,max_season_share=float(y.n.max()/len(x)),loo_min_roi=float(np.nanmin(vals)) if vals else np.nan,loo_all_profitable=bool(np.all(np.array(vals)>0)) if vals else False)
def parse_odds_range(s):
 a,b=[z.strip() for z in str(s).split('to')]
 def p(v):
  z=float(v.replace('+',''))
  return 100/(z+100) if z>=0 else (-z)/((-z)+100)
 p1,p2=p(a),p(b);return min(p1,p2),max(p1,p2)
d=pd.read_parquet(DATA);d=d[d.season.between(2006,2025)].copy();d['win']=num(d.win);d['profit']=profit(d.win,d.moneyline);d['market_prob']=implied(d.moneyline);d=d[d.win.isin([0,1])&num(d.moneyline).notna()].copy()
# RS final filtered sets
rsm=json.loads(RS.read_text());rssets={}
for m in rsm['methods']:
 lo,hi=parse_odds_range(m['odds_range']);x=d[(d.market_prob>=lo)&(d.market_prob<=hi)&(num(d.prior_games)>=3)].copy();
 # RS001 is away-only, RS002/3 any; encoded by source names rather than manifest
 if m['method_id']=='RS001':x=x[num(x.is_home).ne(1)]
 x=x[apply(x,m['conditions'])].copy();v=m.get('single_veto','')
 if v:
  import re
  q=re.match(r'(.+?)\s*(>=|<=|==|>|<)\s*([-0-9.eE]+)$',v)
  if q:x=x[~cmask(x,q.group(1),q.group(2),float(q.group(3)))].copy()
 rssets[m['method_id']]=x
# CO final filtered sets
cb=pd.read_csv(COBASE);cf=pd.read_csv(COFINAL);cosets={};cometa={}
for _,r in cb.iterrows():
 mid=str(r.method_id);f=cf[cf.method_id.eq(mid)]
 if f.empty:continue
 bands={'DOG20_34':(.20,.35),'DOG35_44':(.35,.45),'DOG35_49':(.35,.50),'PK_55':(.45,.55),'FAV55_65':(.55,.65),'FAV65_75':(.65,.75),'FAV75_85':(.75,.85)};lo,hi=bands[r.price_band]
 x=d[(d.market_prob>=lo)&(d.market_prob<hi)&(num(d.prior_games)>=3)].copy()
 if r.venue=='AWAY':x=x[num(x.is_home).ne(1)]
 elif r.venue=='HOME':x=x[num(x.is_home).eq(1)]
 x=x[apply(x,json.loads(r.conditions))].copy();q=f.iloc[0]
 if pd.notna(q.veto_feature):x=x[~cmask(x,q.veto_feature,q.veto_op,q.veto_threshold)].copy()
 cosets[mid]=x;cometa[mid]={'track':r.track}
# H final sets, preserve independence label
h=pd.read_csv(H);hsets={};hmeta={}
for _,r in h.iterrows():
 cond=[]
 for j in [1,2,3]:
  f=r.get(f'feature{j}');o=r.get(f'op{j}');t=r.get(f'threshold{j}')
  if isinstance(f,str) and f and f!='nan' and pd.notna(t):cond.append([f,o,float(t)])
 x=d[(d.market_prob>=float(r.live_p_lo))&(d.market_prob<float(r.live_p_hi))&(num(d.prior_games)>=3)].copy()
 if r.venue=='AWAY':x=x[num(x.is_home).ne(1)]
 elif r.venue=='HOME':x=x[num(x.is_home).eq(1)]
 x=x[apply(x,cond)].copy();vf=r.get('hard_veto_feature');ind=str(r.get('hard_veto_source','')).upper()!='COACHING'
 if isinstance(vf,str) and vf and vf!='nan' and pd.notna(r.get('hard_veto_threshold')):x=x[~cmask(x,vf,r.hard_veto_op,r.hard_veto_threshold)].copy()
 hsets[str(r.candidate_id)]=x;hmeta[str(r.candidate_id)]={'track':r.track,'independent':ind}
# evaluate pair overlap with holdout start = max relevant track starts
track_start={'LONG':2020,'MODERN':2021}
rows=[]
def emit(kind,a,b,x,holdstart,independent=True):
 m=met(x);mh=met(x[x.season>=holdstart]);sq=season_quality(x);rows.append({'kind':kind,'a':a,'b':b,'independent':independent,'holdout_start':holdstart,**m,'holdout_n':mh['n'],'holdout_wins':mh['wins'],'holdout_win_pct':mh['win_pct'],'holdout_roi':mh['roi'],**sq})
for rm,rx in rssets.items():
 rk=keyset(rx)
 for cm,cx in cosets.items():
  keys=rk&keyset(cx);z=rx[(rx.game_id.astype(str)+'|'+rx.team.astype(str)).isin(keys)];emit('RS+CO',rm,cm,z,max(2020,track_start.get(cometa[cm]['track'],2020)))
 for hm,hx in hsets.items():
  keys=rk&keyset(hx);z=rx[(rx.game_id.astype(str)+'|'+rx.team.astype(str)).isin(keys)];emit('RS+H',rm,hm,z,max(2020,track_start.get(hmeta[hm]['track'],2020)),hmeta[hm]['independent'])
pairs=pd.DataFrame(rows);pairs.to_csv(OUT/'pair_consensus.csv',index=False)
# triple consensus: RS + CO + independent H only
tri=[]
for rm,rx in rssets.items():
 rk=keyset(rx)
 for cm,cx in cosets.items():
  rc=rk&keyset(cx)
  if not rc:continue
  for hm,hx in hsets.items():
   if not hmeta[hm]['independent']:continue
   keys=rc&keyset(hx)
   if len(keys)<4:continue
   z=rx[(rx.game_id.astype(str)+'|'+rx.team.astype(str)).isin(keys)];m=met(z);hs=max(2020,track_start.get(cometa[cm]['track'],2020),track_start.get(hmeta[hm]['track'],2020));mh=met(z[z.season>=hs]);sq=season_quality(z);tri.append({'rs':rm,'co':cm,'h':hm,'holdout_start':hs,**m,'holdout_n':mh['n'],'holdout_win_pct':mh['win_pct'],'holdout_roi':mh['roi'],**sq})
tridf=pd.DataFrame(tri);tridf.to_csv(OUT/'triple_consensus.csv',index=False)
# prioritize only meaningful samples
cand=pairs[(pairs.n>=12)&(pairs.holdout_n>=5)&(pairs.roi>0)&(pairs.holdout_roi>0)].copy();cand['score']=cand.holdout_roi*.35+cand.roi*.25+cand.win_pct*.15+cand.pos_ratio*.15+np.log10(cand.n)*.05+cand.loo_min_roi.clip(-1,2)*.05;cand=cand.sort_values('score',ascending=False);cand.to_csv(OUT/'priority_pairs.csv',index=False)
trp=tridf[(tridf.n>=6)&(tridf.holdout_n>=3)&(tridf.roi>0)&(tridf.holdout_roi>0)].copy() if len(tridf) else pd.DataFrame();
if len(trp):trp['score']=trp.holdout_roi*.4+trp.roi*.3+trp.win_pct*.2+np.log10(trp.n)*.1;trp=trp.sort_values('score',ascending=False);trp.to_csv(OUT/'priority_triples.csv',index=False)
summary={'rs_methods':len(rssets),'co_methods':len(cosets),'h_methods':len(hsets),'independent_h':sum(v['independent'] for v in hmeta.values()),'pairs_total':len(pairs),'priority_pairs':len(cand),'triples_total':len(tridf),'priority_triples':len(trp) if len(tridf) else 0};(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
lines=['RS + CO / H CONSENSUS RESEARCH','',json.dumps(summary,indent=2),'','TOP PAIRS']
for _,r in cand.head(20).iterrows():lines.append(f"{r['kind']} {r.a}+{r.b} | {int(r.wins)}-{int(r.losses)} n={int(r.n)} win={100*r.win_pct:.1f}% ROI={100*r.roi:+.1f}% | hold n={int(r.holdout_n)} win={100*r.holdout_win_pct:.1f}% ROI={100*r.holdout_roi:+.1f}% | pos={100*r.pos_ratio:.0f}% LOOmin={100*r.loo_min_roi:+.1f}% independent={r.independent}")
if len(trp):
 lines+=['','TOP TRIPLES']
 for _,r in trp.head(10).iterrows():lines.append(f"{r.rs}+{r.co}+{r.h} | {int(r.wins)}-{int(r.losses)} n={int(r.n)} win={100*r.win_pct:.1f}% ROI={100*r.roi:+.1f}% | hold n={int(r.holdout_n)} win={100*r.holdout_win_pct:.1f}% ROI={100*r.holdout_roi:+.1f}%")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n');print('\n'.join(lines[:80]))
