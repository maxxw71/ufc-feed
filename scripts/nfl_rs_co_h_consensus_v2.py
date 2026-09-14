from pathlib import Path
import os,json,runpy,re
import numpy as np,pandas as pd
REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'));CTX=Path('/home/appwiza-runner/nfl-context-data');OUT=CTX/'rs_co_h_consensus';OUT.mkdir(parents=True,exist_ok=True)
# Reuse the exact RS reconstruction so run-rate / road-schedule features match the approved RS research.
env=runpy.run_path(str(REPO/'scripts/nfl_top3_run_schedule_dissection.py'));d=env['d'].copy();rsbase=env['sets']
RSFINAL=REPO/'nfl/top3_timing_final_audit/final_methods.csv';COBASE=REPO/'nfl/coach_only_deployment_audit/live_ready.csv';COFINAL=REPO/'nfl/coach_only_final_sieve/final_coach_only_arsenal_sieve.csv';H=REPO/'nfl/live_candidate_finalization/live_ready.csv'
def num(x):return pd.to_numeric(x,errors='coerce')
def met(x):
 if not len(x):return dict(n=0,wins=0,losses=0,win_pct=np.nan,roi=np.nan,units=0.)
 return dict(n=len(x),wins=int(x.win.sum()),losses=int(len(x)-x.win.sum()),win_pct=float(x.win.mean()),roi=float(x.profit.mean()),units=float(x.profit.sum()))
def cmask(x,c,o,t):
 if c not in x:return pd.Series(False,index=x.index)
 v=num(x[c]);t=float(t);return {'>=':v>=t,'<=':v<=t,'>':v>t,'<':v<t,'==':v==t}.get(str(o),pd.Series(False,index=x.index))
def apply(x,conds):
 m=pd.Series(True,index=x.index)
 for c,o,t in conds:m&=cmask(x,c,o,t)
 return m
def key(x):return x.game_id.astype(str)+'|'+x.team.astype(str)
def keyset(x):return set(key(x))
def quality(x):
 if not len(x):return dict(pos_seasons=0,active_seasons=0,pos_ratio=np.nan,max_season_share=np.nan,loo_min_roi=np.nan,loo_all_profitable=False)
 y=x.groupby('season').agg(n=('win','size'),units=('profit','sum'));vals=[met(x[x.season.ne(s)])['roi'] for s in y.index];return dict(pos_seasons=int((y.units>0).sum()),active_seasons=len(y),pos_ratio=float((y.units>0).mean()),max_season_share=float(y.n.max()/len(x)),loo_min_roi=float(np.nanmin(vals)),loo_all_profitable=bool(np.all(np.array(vals)>0)))
# RS final filtered sets from exact reconstructed base sets.
rf=pd.read_csv(RSFINAL);rssets={}
for _,r in rf.iterrows():
 x=rsbase[str(r.method_id)].copy();v=str(r.single_veto) if pd.notna(r.single_veto) else ''
 q=re.match(r'(.+?)\s*(>=|<=|==|>|<)\s*([-0-9.eE]+)$',v)
 if q:x=x[~cmask(x,q.group(1),q.group(2),float(q.group(3)))].copy()
 rssets[str(r.method_id)]=x
# CO final filtered sets.
cb=pd.read_csv(COBASE);cf=pd.read_csv(COFINAL);cosets={};cometa={};bands={'DOG20_34':(.20,.35),'DOG35_44':(.35,.45),'DOG35_49':(.35,.50),'PK_55':(.45,.55),'FAV55_65':(.55,.65),'FAV65_75':(.65,.75),'FAV75_85':(.75,.85)}
for _,r in cb.iterrows():
 f=cf[cf.method_id.eq(str(r.method_id))]
 if f.empty:continue
 lo,hi=bands[r.price_band];x=d[(d.market_prob>=lo)&(d.market_prob<hi)&(num(d.prior_games)>=3)].copy()
 if r.venue=='AWAY':x=x[num(x.is_home).ne(1)]
 elif r.venue=='HOME':x=x[num(x.is_home).eq(1)]
 x=x[apply(x,json.loads(r.conditions))].copy();q=f.iloc[0]
 if pd.notna(q.veto_feature):x=x[~cmask(x,q.veto_feature,q.veto_op,q.veto_threshold)].copy()
 cosets[str(r.method_id)]=x;cometa[str(r.method_id)]={'track':r.track}
# H final sets, with independence label.
h=pd.read_csv(H);hsets={};hmeta={}
for _,r in h.iterrows():
 cond=[]
 for j in [1,2,3]:
  f=r.get(f'feature{j}');o=r.get(f'op{j}');t=r.get(f'threshold{j}')
  if isinstance(f,str) and f and f!='nan' and pd.notna(t):cond.append([f,o,float(t)])
 x=d[(d.market_prob>=float(r.live_p_lo))&(d.market_prob<float(r.live_p_hi))&(num(d.prior_games)>=3)].copy()
 if r.venue=='AWAY':x=x[num(x.is_home).ne(1)]
 elif r.venue=='HOME':x=x[num(x.is_home).eq(1)]
 x=x[apply(x,cond)].copy();ind=str(r.get('hard_veto_source','')).upper()!='COACHING';vf=r.get('hard_veto_feature')
 if isinstance(vf,str) and vf and vf!='nan' and pd.notna(r.get('hard_veto_threshold')):x=x[~cmask(x,vf,r.hard_veto_op,r.hard_veto_threshold)].copy()
 hsets[str(r.candidate_id)]=x;hmeta[str(r.candidate_id)]={'track':r.track,'independent':ind}
track_start={'LONG':2020,'MODERN':2021};rows=[]
def emit(kind,a,b,x,hs,ind=True):
 m=met(x);mh=met(x[x.season>=hs]);rows.append({'kind':kind,'a':a,'b':b,'independent':ind,'holdout_start':hs,**m,'holdout_n':mh['n'],'holdout_wins':mh['wins'],'holdout_win_pct':mh['win_pct'],'holdout_roi':mh['roi'],**quality(x)})
for rm,rx in rssets.items():
 rk=keyset(rx)
 for cm,cx in cosets.items():
  ks=rk&keyset(cx);emit('RS+CO',rm,cm,rx[key(rx).isin(ks)],max(2020,track_start.get(cometa[cm]['track'],2020)))
 for hm,hx in hsets.items():
  ks=rk&keyset(hx);emit('RS+H',rm,hm,rx[key(rx).isin(ks)],max(2020,track_start.get(hmeta[hm]['track'],2020)),hmeta[hm]['independent'])
pairs=pd.DataFrame(rows);pairs.to_csv(OUT/'pair_consensus.csv',index=False)
# Triple intersections use independent H only.
tri=[]
for rm,rx in rssets.items():
 rk=keyset(rx)
 for cm,cx in cosets.items():
  rc=rk&keyset(cx)
  for hm,hx in hsets.items():
   if not hmeta[hm]['independent']:continue
   ks=rc&keyset(hx)
   if len(ks)<4:continue
   z=rx[key(rx).isin(ks)];hs=max(2020,track_start.get(cometa[cm]['track'],2020),track_start.get(hmeta[hm]['track'],2020));m=met(z);mh=met(z[z.season>=hs]);tri.append({'rs':rm,'co':cm,'h':hm,'holdout_start':hs,**m,'holdout_n':mh['n'],'holdout_win_pct':mh['win_pct'],'holdout_roi':mh['roi'],**quality(z)})
tridf=pd.DataFrame(tri);tridf.to_csv(OUT/'triple_consensus.csv',index=False)
# Priority screen is intentionally modest; this is consensus research, not final deployment.
cand=pairs[(pairs.n>=10)&(pairs.holdout_n>=4)&(pairs.roi>0)&(pairs.holdout_roi>0)].copy();cand['score']=cand.holdout_roi*.35+cand.roi*.25+cand.win_pct*.15+cand.pos_ratio*.15+np.log10(cand.n)*.05+cand.loo_min_roi.clip(-1,2)*.05;cand=cand.sort_values('score',ascending=False);cand.to_csv(OUT/'priority_pairs.csv',index=False)
trp=tridf[(tridf.n>=5)&(tridf.holdout_n>=2)&(tridf.roi>0)&(tridf.holdout_roi>0)].copy() if len(tridf) else pd.DataFrame()
if len(trp):trp['score']=trp.holdout_roi*.4+trp.roi*.3+trp.win_pct*.2+np.log10(trp.n)*.1;trp=trp.sort_values('score',ascending=False);trp.to_csv(OUT/'priority_triples.csv',index=False)
summary={'rs_set_sizes':{k:len(v) for k,v in rssets.items()},'co_methods':len(cosets),'h_methods':len(hsets),'independent_h':sum(v['independent'] for v in hmeta.values()),'pairs_total':len(pairs),'nonzero_pairs':int((pairs.n>0).sum()),'priority_pairs':len(cand),'triples_total':len(tridf),'priority_triples':len(trp) if len(tridf) else 0};(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
lines=['RS + CO / H CONSENSUS RESEARCH V2','',json.dumps(summary,indent=2),'','TOP PAIRS']
for _,r in cand.head(25).iterrows():lines.append(f"{r['kind']} {r.a}+{r.b} | {int(r.wins)}-{int(r.losses)} n={int(r.n)} win={100*r.win_pct:.1f}% ROI={100*r.roi:+.1f}% | hold n={int(r.holdout_n)} win={100*r.holdout_win_pct:.1f}% ROI={100*r.holdout_roi:+.1f}% | pos={100*r.pos_ratio:.0f}% LOOmin={100*r.loo_min_roi:+.1f}% independent={r.independent}")
if len(trp):
 lines+=['','TOP TRIPLES']
 for _,r in trp.head(15).iterrows():lines.append(f"{r.rs}+{r.co}+{r.h} | {int(r.wins)}-{int(r.losses)} n={int(r.n)} win={100*r.win_pct:.1f}% ROI={100*r.roi:+.1f}% | hold n={int(r.holdout_n)} win={100*r.holdout_win_pct:.1f}% ROI={100*r.holdout_roi:+.1f}%")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n');print('\n'.join(lines[:100]))
