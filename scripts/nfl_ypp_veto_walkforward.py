import json, math
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path.home()/"nfl-predictor-v1"
IN=ROOT/"rolling_roi_discovery"/"pregame_team_sides_2006_2026.parquet"
OUT=ROOT/"nfl_ypp_veto_walkforward"; OUT.mkdir(parents=True,exist_ok=True)

def met(x):
    n=len(x)
    if not n:return dict(n=0,wins=0,losses=0,win=np.nan,roi=np.nan,profit=0.0)
    p=float(x.bet_profit100.sum()); w=int(x.win.sum())
    return dict(n=n,wins=w,losses=n-w,win=float(x.win.mean()),roi=p/(100*n),profit=p)

def base(df,p):
    z=(df.week>=p['w'])&(df.market_prob>=p['m'])
    if p['loc']=='HOME':z &= df.home_side.eq(1)
    elif p['loc']=='ROAD':z &= df.home_side.eq(0)
    return z

def ypp(df,p):
    return base(df,p)&(df.pre_yards_per_play>=p['oy'])&(df.opp_pre_def_ypp_allowed>=p['dy'])

def veto(df,name):
    ypptrend=df.opp_last3_def_ypp_allowed.ge(df.opp_pre_def_ypp_allowed)
    pdtrend=df.opp_last3_point_diff.le(df.opp_pre_point_diff)
    if name=='NONE':return pd.Series(True,index=df.index)
    if name=='DEF_YPP_TREND':return ypptrend.fillna(False)
    if name=='POINTDIFF_TREND':return pdtrend.fillna(False)
    if name=='BOTH_TRENDS':return (ypptrend&pdtrend).fillna(False)
    raise ValueError(name)

g=pd.read_parquet(IN)
for c in g.columns:
    if c not in {'game_id','team','opponent','opponent_team','roof','surface'}:
        try:g[c]=pd.to_numeric(g[c],errors='ignore')
        except:pass
b=g[g.moneyline.notna()&g.market_prob.notna()&g.win.isin([0.0,1.0])&(g.prior_games>=3)&(g.market_prob>.50)].copy()
for c in ['season','week','market_prob','moneyline','home_side','div_game']:
    if c in b:b[c]=pd.to_numeric(b[c],errors='coerce')

grid=[]
for w in [4,5,6,7,8,9,10,11,12]:
  for m in [.52,.55,.60,.65]:
   for loc in ['ANY','HOME','ROAD']:
    for oy in [5.5,5.8,6.0,6.2]:
     for dy in [5.5,5.8,6.0,6.2]:grid.append(dict(w=w,m=m,loc=loc,oy=oy,dy=dy))

vetoes=['NONE','DEF_YPP_TREND','POINTDIFF_TREND','BOTH_TRENDS']
rows=[]; bets=[]
for year in range(2016,2027):
    tr=b[b.season<year]; te=b[b.season==year]
    # Step 1: select the original YPP rule exactly as before, without any veto influence.
    cand=[]
    for p in grid:
        x=tr[ypp(tr,p).fillna(False)]; m=met(x)
        if m['n']<50 or m['win']<.68 or m['roi']<.03:continue
        recent=x[x.season>=max(2006,year-5)]; rm=met(recent)
        if rm['n']>=15 and rm['roi']<=0:continue
        score=min(m['roi'],.20)*math.sqrt(m['n'])+.25*max(0,m['win']-.68)*math.sqrt(m['n'])
        cand.append((score,p,m))
    if not cand:continue
    cand.sort(key=lambda q:q[0],reverse=True); _,p,pm=cand[0]

    # Step 2: with YPP rule frozen, choose only among four pre-specified vetoes using prior seasons.
    pool=tr[ypp(tr,p).fillna(False)].copy()
    vc=[]
    for vn in vetoes:
        x=pool[veto(pool,vn)]; m=met(x)
        if m['n']<30 or pd.isna(m['roi']):continue
        recent=x[x.season>=max(2006,year-5)]; rm=met(recent)
        if rm['n']>=10 and rm['roi']<=0:continue
        # Modest complexity penalty favors NONE unless a veto has historical evidence.
        penalty=0.0 if vn=='NONE' else .03
        score=min(m['roi'],.25)*math.sqrt(m['n']) + .20*max(0,m['win']-.68)*math.sqrt(m['n']) - penalty
        vc.append((score,vn,m))
    if not vc:continue
    vc.sort(key=lambda q:q[0],reverse=True); _,vn,vm=vc[0]
    z=te[(ypp(te,p)&veto(te,vn)).fillna(False)].copy(); mm=met(z)
    rows.append({'test_season':year,'ypp_params':json.dumps(p,sort_keys=True),'selected_veto':vn,'ypp_train_n':pm['n'],'ypp_train_win':pm['win'],'ypp_train_roi':pm['roi'],'veto_train_n':vm['n'],'veto_train_win':vm['win'],'veto_train_roi':vm['roi'],'test_n':mm['n'],'test_wins':mm['wins'],'test_win':mm['win'],'test_roi':mm['roi'],'test_profit':mm['profit']})
    for _,r in z.iterrows():
        d=r.to_dict(); d['test_season']=year; d['selected_veto']=vn; d['selected_params']=json.dumps(p,sort_keys=True); bets.append(d)

seasons=pd.DataFrame(rows); seasons.to_csv(OUT/'veto_walkforward_by_season.csv',index=False)
bd=pd.DataFrame(bets)
if len(bd):
    bd.to_csv(OUT/'veto_walkforward_bets.csv',index=False)
    bd[bd.win==0].to_csv(OUT/'veto_walkforward_losses.csv',index=False)
    n=len(bd); wins=int(bd.win.sum()); profit=float(bd.bet_profit100.sum()); roi=profit/(100*n)
    by=bd.groupby('test_season',as_index=False).agg(n=('win','size'),wins=('win','sum'),profit=('bet_profit100','sum')); by['roi']=by.profit/(100*by.n)
    srt=bd.sort_values(['test_season','week','game_id','team']); cum=srt.bet_profit100.cumsum(); peak=cum.cummax().clip(lower=0); dd=float((cum-peak).min())
    summary=pd.DataFrame([{'n':n,'wins':wins,'losses':n-wins,'win':wins/n,'roi':roi,'profit':profit,'profitable_seasons':int((by.profit>0).sum()),'seasons':len(by),'max_drawdown':dd}])
else:summary=pd.DataFrame()
summary.to_csv(OUT/'veto_walkforward_summary.csv',index=False)
counts=seasons.selected_veto.value_counts().rename_axis('veto').reset_index(name='seasons_selected') if len(seasons) else pd.DataFrame()
counts.to_csv(OUT/'veto_selection_counts.csv',index=False)
print('STRICT YPP VETO WALK-FORWARD')
print(summary.to_string(index=False))
print('\nVeto choices by season:')
print(seasons[['test_season','selected_veto','veto_train_n','veto_train_win','veto_train_roi','test_n','test_wins','test_win','test_roi','test_profit']].to_string(index=False))
print('\nNo weather variables or weather vetoes are used.')
