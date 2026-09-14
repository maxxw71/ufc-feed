from pathlib import Path
import json, math, os
import numpy as np
import pandas as pd

ROOT=Path('/home/anestishkurti92/nfl-predictor-v1')
CTX=Path('/home/appwiza-runner/nfl-context-data')
REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
BASE=CTX/'injury_travel_mining'/'complete_pregame_team_sides.parquet'
ODDS=ROOT/'research_v2'/'historical_odds'/'all_open_close_quotes.csv'
REG=REPO/'nfl'/'final_methods'/'approved_research_methods.csv'
CANON=REPO/'nfl'/'frozen_holdout_discovery'/'canonical_frozen_holdout_methods.csv'
OUT=CTX/'exact_price_windows'; OUT.mkdir(parents=True,exist_ok=True)


def num(x): return pd.to_numeric(x,errors='coerce')
def implied(o):
    o=num(o); return np.where(o>0,100/(o+100),np.where(o<0,np.abs(o)/(np.abs(o)+100),np.nan))
def profit(win,odds):
    odds=num(odds); win=num(win)
    return np.where(win.eq(1),np.where(odds>0,odds/100,100/np.abs(odds)),-1.0)
def metrics(x):
    if len(x)==0:return {'n':0,'wins':0,'losses':0,'roi':np.nan,'win_rate':np.nan}
    return {'n':int(len(x)),'wins':int(num(x.win).sum()),'losses':int(len(x)-num(x.win).sum()),'roi':float(num(x.profit_units).mean()),'win_rate':float(num(x.win).mean())}
def amer(p):
    if pd.isna(p):return np.nan
    p=float(p)
    if p<=0 or p>=1:return np.nan
    if p<.5:return 100*(1-p)/p
    if p>.5:return -100*p/(1-p)
    return 100.0
def fmt_odds(p_lo,p_hi):
    a=amer(p_lo); b=amer(p_hi)
    def f(v):
        if pd.isna(v):return '?'
        r=int(round(v))
        return f'+{r}' if r>=0 else str(r)
    # For dogs, show shorter price first (+122 to +186). For favorites, show shallower favorite first (-122 to -186).
    if p_hi<.5:return f'{f(b)} to {f(a)}'
    if p_lo>.5:return f'{f(a)} to {f(b)}'
    return f'{f(a)} to {f(b)}'

price_bands={'DOG20_34':(.20,.3499),'DOG35_44':(.35,.4499),'DOG35_49':(.35,.4999),'PK_55':(.45,.55),'FAV55_65':(.55,.65),'FAV65_75':(.65,.75),'FAV75_85':(.75,.85)}
tracks={'LONG':{'train':(2006,2013),'validation':(2014,2019),'holdout':(2020,2025)},'MODERN':{'train':(2012,2017),'validation':(2018,2020),'holdout':(2021,2025)}}

d=pd.read_parquet(BASE)
d['season']=num(d.season).astype('Int64')
d=d[(num(d.get('completed',1))==1)&num(d.win).isin([0,1])&num(d.moneyline).notna()].copy()
d=d[d.season.between(2006,2025)].copy()
if 'prior_games' in d.columns:d=d[num(d.prior_games)>=3].copy()
d['win']=num(d.win); d['moneyline']=num(d.moneyline); d['profit_units']=profit(d.win,d.moneyline)
d['market_prob_calc']=implied(d.moneyline)
d['market_prob_use']=num(d.market_prob).fillna(pd.Series(d.market_prob_calc,index=d.index)) if 'market_prob' in d.columns else d.market_prob_calc

# Rebuild archived open/current movement fields used by H016/H031/etc.
if ODDS.exists():
    for c in ['open_home','open_away','close_home','close_away','open_prob','close_prob','open_to_close_prob_move','base_vs_open_prob_move']:
        if c in d.columns:d=d.drop(columns=[c])
    q=pd.read_csv(ODDS,low_memory=False)
    q=q[q.market.astype(str).str.lower().eq('moneyline')].copy(); q['american_odds']=num(q.american_odds); q['prob']=implied(q.american_odds)
    q=q[q.phase.astype(str).str.lower().isin(['open','close'])&q.selection.astype(str).str.lower().isin(['home','away'])]
    qa=q.groupby(['game_id','phase','selection'],as_index=False).prob.median()
    w=qa.pivot_table(index='game_id',columns=['phase','selection'],values='prob',aggfunc='first'); w.columns=['_'.join(x) for x in w.columns]; w=w.reset_index()
    d=d.merge(w,on='game_id',how='left'); home=num(d.is_home)==1
    d['open_prob']=np.where(home,num(d.get('open_home')),num(d.get('open_away')))
    d['close_prob']=np.where(home,num(d.get('close_home')),num(d.get('close_away')))
    d['open_to_close_prob_move']=d.close_prob-d.open_prob
    d['base_vs_open_prob_move']=d.market_prob_use-d.open_prob

reg=pd.read_csv(REG)
reg=reg[reg.registry_status.isin(['FINAL_RESEARCH','WATCHLIST'])].copy()
canon=pd.read_csv(CANON)
track_map=canon[['method_id','track']].drop_duplicates('method_id')
reg=reg.merge(track_map,on='method_id',how='left')
reg['track']=reg.track.fillna('LONG')

def cond(c,op,q):
    if c not in d.columns:return pd.Series(False,index=d.index)
    z=num(d[c]); return z>=float(q) if op=='>=' else z<=float(q)
def method_mask(r, include_price=True):
    m=cond(r.feature1,r.op1,r.threshold1)
    if pd.notna(r.get('feature2')):m &= cond(r.feature2,r.op2,r.threshold2)
    if r.venue=='HOME':m &= num(d.is_home).eq(1)
    elif r.venue=='AWAY':m &= num(d.is_home).eq(0)
    if include_price:
        lo,hi=price_bands[r.price_band]; m &= num(d.market_prob_use).between(lo,hi,inclusive='both')
    return m

def pmask(track,p):
    lo,hi=tracks.get(track,tracks['LONG'])[p]; return d.season.between(lo,hi)

rows=[]; candidate_rows=[]
for _,r in reg.iterrows():
    if r.price_band not in price_bands:continue
    track=r.track if r.track in tracks else 'LONG'
    lo0,hi0=price_bands[r.price_band]
    no_price=method_mask(r,False)
    base=no_price & num(d.market_prob_use).between(lo0,hi0,inclusive='both')
    bt=metrics(d[base&pmask(track,'train')]); bv=metrics(d[base&pmask(track,'validation')]); bh=metrics(d[base&pmask(track,'holdout')]); bf=metrics(d[base])
    tr=d[base&pmask(track,'train')]
    vals=num(tr.market_prob_use).dropna()
    chosen=None
    if len(vals)>=25 and bt['n']>=25 and bv['n']>=15:
        # Boundaries come ONLY from training-period price distribution plus the original band edges.
        qvals=list(vals.quantile([.10,.15,.20,.25,.30,.70,.75,.80,.85,.90]).dropna().astype(float))
        lows=sorted(set([lo0]+[x for x in qvals if lo0<x<hi0]))
        highs=sorted(set([hi0]+[x for x in qvals if lo0<x<hi0]))
        candidates=[]
        for lo in lows:
            for hi in highs:
                if not lo<hi:continue
                # Must actually tighten at least one edge.
                if abs(lo-lo0)<1e-9 and abs(hi-hi0)<1e-9:continue
                win=no_price & num(d.market_prob_use).between(lo,hi,inclusive='both')
                mt=metrics(d[win&pmask(track,'train')]); mv=metrics(d[win&pmask(track,'validation')])
                if mt['n']<max(20,.55*bt['n']) or mv['n']<max(12,.55*bv['n']):continue
                # Do not accept an exact-price slice that hurts either pre-holdout era materially.
                if mt['roi'] < bt['roi']-.01 or mv['roi'] < bv['roi']-.01:continue
                gain=.5*((mt['roi']-bt['roi'])+(mv['roi']-bv['roi']))
                retain=.5*(mt['n']/bt['n']+mv['n']/bv['n'])
                # Penalize needless narrowing. We only want a tighter gate when there is evidence of improvement.
                score=gain + .025*retain
                candidates.append((score,gain,retain,lo,hi,mt,mv))
                candidate_rows.append({'method_id':r.method_id,'track':track,'p_lo':lo,'p_hi':hi,'odds_range':fmt_odds(lo,hi),'train_n':mt['n'],'train_roi':mt['roi'],'validation_n':mv['n'],'validation_roi':mv['roi'],'preholdout_gain':gain,'preholdout_retain':retain,'score':score})
        if candidates:
            candidates.sort(key=lambda x:(x[0],x[1],x[2]),reverse=True)
            top=candidates[0]
            # Require at least +2 ROI points average pre-holdout improvement before even proposing a narrower live band.
            if top[1]>=.02:chosen=top
    if chosen is None:
        lo1,hi1=lo0,hi0; pre_gain=0.0; pre_retain=1.0; mt=bt; mv=bv; proposed=False
    else:
        _,pre_gain,pre_retain,lo1,hi1,mt,mv=chosen; proposed=True
    win=no_price & num(d.market_prob_use).between(lo1,hi1,inclusive='both')
    mh=metrics(d[win&pmask(track,'holdout')]); mf=metrics(d[win])
    if not proposed:
        decision='KEEP_ORIGINAL_BAND'
    else:
        retain_h=mh['n']/max(1,bh['n'])
        # Holdout confirms the narrower price gate only if it keeps useful volume and improves ROI by >=2 pts.
        if mh['n']>=15 and retain_h>=.50 and mh['roi']>=bh['roi']+.02 and mh['roi']>=.10:
            decision='TIGHTEN_LIVE_PRICE'
        elif mh['n']<15 or retain_h<.50:
            decision='WATCH_NARROW_WINDOW'
        else:
            decision='KEEP_ORIGINAL_BAND'
    final_lo,final_hi=(lo1,hi1) if decision=='TIGHTEN_LIVE_PRICE' else (lo0,hi0)
    rows.append({
        'method_id':r.method_id,'registry_status':r.registry_status,'track':track,'price_band':r.price_band,
        'original_p_lo':lo0,'original_p_hi':hi0,'original_odds_range':fmt_odds(lo0,hi0),
        'proposed_p_lo':lo1,'proposed_p_hi':hi1,'proposed_odds_range':fmt_odds(lo1,hi1),'preholdout_gain':pre_gain,'preholdout_retain':pre_retain,
        'train_base_n':bt['n'],'train_base_roi':bt['roi'],'train_window_n':mt['n'],'train_window_roi':mt['roi'],
        'validation_base_n':bv['n'],'validation_base_roi':bv['roi'],'validation_window_n':mv['n'],'validation_window_roi':mv['roi'],
        'holdout_base_n':bh['n'],'holdout_base_roi':bh['roi'],'holdout_window_n':mh['n'],'holdout_window_roi':mh['roi'],'holdout_roi_change':mh['roi']-bh['roi'],'holdout_retain':mh['n']/max(1,bh['n']),
        'full_base_n':bf['n'],'full_base_roi':bf['roi'],'full_window_n':mf['n'],'full_window_roi':mf['roi'],
        'decision':decision,'live_p_lo':final_lo,'live_p_hi':final_hi,'live_odds_range':fmt_odds(final_lo,final_hi)
    })

res=pd.DataFrame(rows).sort_values(['decision','holdout_roi_change'],ascending=[True,False])
res.to_csv(OUT/'method_price_windows.csv',index=False)
pd.DataFrame(candidate_rows).to_csv(OUT/'preholdout_price_candidates.csv',index=False)
summary={
    'methods_audited':int(len(res)),
    'tighten_live_price':int((res.decision=='TIGHTEN_LIVE_PRICE').sum()),
    'keep_original_band':int((res.decision=='KEEP_ORIGINAL_BAND').sum()),
    'watch_narrow_window':int((res.decision=='WATCH_NARROW_WINDOW').sum()),
}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
lines=['NFL EXACT PRICE-WINDOW AUDIT','',json.dumps(summary,indent=2),'',
       'All candidate narrower windows were selected using training+validation only; holdout was opened afterward to confirm or reject tightening.','']
for _,r in res.iterrows():
    lines.append(f"{r.method_id} [{r.decision}] {r.original_odds_range} -> {r.live_odds_range} | holdout {100*r.holdout_base_roi:+.1f}% -> {100*r.holdout_window_roi:+.1f}% | n {int(r.holdout_base_n)}->{int(r.holdout_window_n)} | full {100*r.full_base_roi:+.1f}%->{100*r.full_window_roi:+.1f}%")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n')
print('\n'.join(lines))
