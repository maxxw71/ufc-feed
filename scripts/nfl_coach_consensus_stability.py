from pathlib import Path
import os,json
import numpy as np
import pandas as pd

REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
CTX=Path('/home/appwiza-runner/nfl-context-data')
OUT=CTX/'coach_consensus_stability';OUT.mkdir(parents=True,exist_ok=True)
DATA=CTX/'coach_quality_expansion'/'coach_quality_enriched_team_sides.parquet'
CO=REPO/'nfl/coach_only_deployment_audit/live_ready.csv'
H=REPO/'nfl/live_candidate_finalization/live_ready.csv'
SIEVE=REPO/'nfl/coach_only_final_sieve/selected_vetoes.csv'

def num(x):return pd.to_numeric(x,errors='coerce')
def implied(ml):ml=num(ml);return np.where(ml<0,(-ml)/((-ml)+100),100/(ml+100))
def profit(win,ml):ml=num(ml);return np.where(num(win).eq(1),np.where(ml>0,ml/100,100/np.abs(ml)),-1.0)
def met(x):
    if not len(x):return {'n':0,'wins':0,'losses':0,'win_pct':np.nan,'roi':np.nan,'units':0.0}
    return {'n':len(x),'wins':int(x.win.sum()),'losses':int(len(x)-x.win.sum()),'win_pct':float(x.win.mean()),'roi':float(x.profit.mean()),'units':float(x.profit.sum())}
def cmask(x,c,op,t):
    if c not in x:return pd.Series(False,index=x.index)
    v=num(x[c]);t=float(t)
    return {'>=':v>=t,'<=':v<=t,'>':v>t,'<':v<t,'==':v==t}.get(op,pd.Series(False,index=x.index))
def apply(x,conds):
    m=pd.Series(True,index=x.index)
    for c,op,t in conds:m &= cmask(x,c,op,t)
    return m
bands={'DOG20_34':(.20,.35),'DOG35_44':(.35,.45),'DOG35_49':(.35,.50),'PK_55':(.45,.55),'FAV55_65':(.55,.65),'FAV65_75':(.65,.75),'FAV75_85':(.75,.85)}

d=pd.read_parquet(DATA);d=d[d.season.between(2006,2025)].copy();d['market_prob']=implied(d.moneyline);d['win']=num(d.win);d['profit']=profit(d.win,d.moneyline);d=d[d.win.isin([0,1])&d.moneyline.notna()].copy()
co=pd.read_csv(CO);h=pd.read_csv(H)
sel=pd.read_csv(SIEVE) if SIEVE.exists() else pd.DataFrame()
cosets={}
for _,r in co.iterrows():
    lo,hi=bands[r.price_band];x=d[(d.market_prob>=lo)&(d.market_prob<hi)&(num(d.prior_games)>=3)].copy()
    if r.venue=='AWAY':x=x[num(x.is_home).ne(1)]
    elif r.venue=='HOME':x=x[num(x.is_home).eq(1)]
    x=x[apply(x,json.loads(r.conditions))].copy();cosets[str(r.method_id)]=x
hsets={}
for _,r in h.iterrows():
    conds=[]
    for j in [1,2,3]:
        f=r.get(f'feature{j}');op=r.get(f'op{j}');t=r.get(f'threshold{j}')
        if isinstance(f,str) and f and f!='nan' and pd.notna(t):conds.append((f,op,float(t)))
    x=d[(d.market_prob>=float(r.live_p_lo))&(d.market_prob<float(r.live_p_hi))&(num(d.prior_games)>=3)].copy()
    if r.venue=='AWAY':x=x[num(x.is_home).ne(1)]
    elif r.venue=='HOME':x=x[num(x.is_home).eq(1)]
    x=x[apply(x,conds)].copy()
    if str(r.hard_veto_source).upper()=='COACHING':continue
    vf=r.get('hard_veto_feature');vo=r.get('hard_veto_op');vt=r.get('hard_veto_threshold')
    if isinstance(vf,str) and vf and vf!='nan' and pd.notna(vt):x=x[~cmask(x,vf,vo,float(vt))].copy()
    hsets[str(r.candidate_id)]=x
pairs=[('CO005','NFL-H017'),('CO017','NFL-H029')]
rows=[];yearly=[];losses=[];teams=[];loo=[]
for cm,hm in pairs:
    cx=cosets[cm];hx=hsets[hm]
    keys=set(cx.game_id.astype(str)+'|'+cx.team.astype(str)) & set(hx.game_id.astype(str)+'|'+hx.team.astype(str))
    z=cx[(cx.game_id.astype(str)+'|'+cx.team.astype(str)).isin(keys)].copy();z['pair']=cm+'+'+hm
    versions=[('RAW',z)]
    sv=sel[sel.method_id.eq(cm)] if len(sel) else pd.DataFrame()
    if len(sv):
        q=sv.iloc[0];versions.append(('CO_VETO',z[~cmask(z,q.feature,q.op,float(q.threshold))].copy()))
    for version,x in versions:
        m=met(x);yy=x.groupby('season').agg(n=('win','size'),wins=('win','sum'),units=('profit','sum')).reset_index();yy['win_pct']=yy.wins/yy.n;yy['roi']=yy.units/yy.n
        active=yy[yy.n>=1];pos=int((active.units>0).sum());act=len(active);maxshare=float(yy.n.max()/len(x)) if len(x) else np.nan
        q={'pair':cm+'+'+hm,'version':version,**m,'positive_seasons':pos,'active_seasons':act,'positive_season_ratio':pos/act if act else np.nan,'max_season_share':maxshare}
        vals=[]
        for season in sorted(x.season.unique()):
            lm=met(x[x.season.ne(season)]);vals.append(lm['roi']);loo.append({'pair':cm+'+'+hm,'version':version,'omitted_season':int(season),**lm})
        q['loo_min_roi']=float(np.nanmin(vals)) if vals else np.nan;q['loo_all_profitable']=bool(np.all(np.array(vals)>0)) if vals else False
        rows.append(q)
        for _,y in yy.iterrows():yearly.append({'pair':cm+'+'+hm,'version':version,'season':int(y.season),'n':int(y.n),'wins':int(y.wins),'win_pct':float(y.win_pct),'roi':float(y.roi),'units':float(y.units)})
        for _,r0 in x[x.win.eq(0)].iterrows():losses.append({'pair':cm+'+'+hm,'version':version,'season':int(r0.season),'week':int(r0.week),'game_id':r0.game_id,'team':r0.team,'opponent':r0.opponent,'moneyline':r0.moneyline})
        for tm,n in x.team.value_counts().items():teams.append({'pair':cm+'+'+hm,'version':version,'team':tm,'n':int(n),'share':float(n/len(x))})
pd.DataFrame(rows).to_csv(OUT/'summary.csv',index=False);pd.DataFrame(yearly).to_csv(OUT/'by_season.csv',index=False);pd.DataFrame(loo).to_csv(OUT/'leave_one_season_out.csv',index=False);pd.DataFrame(losses).to_csv(OUT/'losses.csv',index=False);pd.DataFrame(teams).to_csv(OUT/'team_concentration.csv',index=False)
lines=['COACH + H CONSENSUS STABILITY','']
for r in rows:lines.append(f"{r['pair']} {r['version']} | {r['wins']}-{r['losses']} ({100*r['win_pct']:.1f}%) n={r['n']} ROI={100*r['roi']:+.1f}% | positive seasons={r['positive_seasons']}/{r['active_seasons']} ({100*r['positive_season_ratio']:.0f}%) max season share={100*r['max_season_share']:.1f}% | LOO min ROI={100*r['loo_min_roi']:+.1f}% all profitable={r['loo_all_profitable']}")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
# trigger 2026-09-14b
