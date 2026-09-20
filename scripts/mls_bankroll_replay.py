#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
import mls_arsenal_research as arx

ROOT=Path('/home/anestishkurti92/mls-predictor-v1')
SRC=ROOT/'research/arsenal/latest.json'
OUT=ROOT/'research/bankroll_replay';OUT.mkdir(parents=True,exist_ok=True)

METHODS=[
 {'id':'MLS-R01V2','side':'HOME','lo':.40,'hi':.50,'rules':[('base__edge_last3_xgfpg','>=',.45538),('base__elo_edge','<=',.91),('base__opp_last10_gdpg','<',.6)]},
 {'id':'MLS-R02V2','side':'HOME','lo':.40,'hi':.50,'rules':[('base__edge_last3_xgfpg','>=',.578285),('base__edge_last3_xgapg','>=',.0536)]},
 {'id':'MLS-R03','side':'AWAY','lo':.25,'hi':.35,'rules':[('base__elo_edge','<=',-41.31),('base__edge_last5_xgapg','<',.01113)]},
 {'id':'MLS-A02','side':'HOME','lo':.50,'hi':.60,'rules':[('ctx__edge_gk_save_pct5','<=',-.10148378191856453),('base__sel_last10_ppg','>',1.1)]},
 {'id':'MLS-A03','side':'AWAY','lo':.25,'hi':.35,'rules':[('base__edge_last10_ppg','<=',-.3999999999999999),('base__edge_last10_xgdpg','<',-.13458100000000023)]},
 {'id':'MLS-A04','side':'HOME','lo':.40,'hi':.50,'rules':[('base__edge_last5_xgdpg','<=',-.04208999999999996),('ctx__sel_roster_new_players3','<=',0),('pm__opp_player_weighted_age5','>',28.068592458747545)]},
 {'id':'MLS-A05','side':'AWAY','lo':.25,'hi':.35,'rules':[('ctx__referee_prior_over25_rate','<=',.5348837209302325)]},
 {'id':'MLS-A06','side':'AWAY','lo':.20,'hi':.30,'rules':[('ctx__opp_roster_new_players3','<=',0),('base__edge_last10_xgdpg','<',-.2933600000000003)]},
]

def cond(df,f,op,t):
    v=pd.to_numeric(df[f],errors='coerce')
    return {'>=':v.ge(t),'<=':v.le(t),'>':v.gt(t),'<':v.lt(t)}[op]

def rows_for(s,m):
    so=s[s.outcome.eq(m['side'])].copy()
    mask=so.market_prob.between(m['lo'],m['hi'],inclusive='both')
    for f,op,t in m['rules']: mask &= cond(so,f,op,t)
    cols=['match_id','date','season','outcome','odds','market_prob','profit']
    z=so.loc[mask,cols].copy();z['method_id']=m['id']
    return z

def max_loss_streak(profits):
    best=cur=0
    for p in profits:
        if p<0: cur+=1;best=max(best,cur)
        else: cur=0
    return best

def simulate(rows,mode):
    bankroll=10000.0
    peak=bankroll
    max_dd_amt=0.0;max_dd_pct=0.0
    max_dd_start=None;max_dd_trough=None;max_dd_recovery=None
    current_peak_date=None
    active_dd_peak_bank=peak
    active_dd_start=None
    lowest=bankroll;lowest_date=None
    total_staked=0.0
    rec=[]
    # Group same match so duplicate same-side method signals use the same pre-match bankroll for 1% staking.
    for match_id,g in rows.groupby('match_id',sort=False):
        g=g.copy()
        pre=bankroll
        stake=100.0 if mode=='flat100' else pre*.01
        # Each method signal is a separate wager, same stake determined pre-match.
        match_pnl=float((g.profit*stake).sum())
        total_staked+=stake*len(g)
        bankroll+=match_pnl
        dt=pd.to_datetime(g.date.iloc[0])
        # one record per signal for count/loss-streak visibility; bankroll after group repeated on last only
        for i,(_,r) in enumerate(g.iterrows()):
            rec.append({
                'date':dt.date().isoformat(),'match_id':str(match_id),'method_id':r.method_id,
                'outcome':r.outcome,'odds':float(r.odds),'unit_profit':float(r.profit),
                'stake':stake,'signal_profit':float(r.profit*stake),
                'bankroll_after_match':bankroll if i==len(g)-1 else None
            })
        if bankroll<lowest:
            lowest=bankroll;lowest_date=dt.date().isoformat()
        if bankroll>=peak:
            peak=bankroll;current_peak_date=dt
            active_dd_start=None;active_dd_peak_bank=peak
        else:
            if active_dd_start is None:
                active_dd_start=current_peak_date or dt
                active_dd_peak_bank=peak
            dd=peak-bankroll;ddpct=dd/peak if peak else 0
            if dd>max_dd_amt:
                max_dd_amt=dd;max_dd_pct=ddpct
                max_dd_start=active_dd_start
                max_dd_trough=dt
                max_dd_recovery=None
    # second pass for recovery of max drawdown peak
    if max_dd_start is not None:
        # reconstruct match-level bankroll and find first point after trough >= peak preceding max DD
        bankroll=10000.0;peak=bankroll;target_peak=None
        trough_seen=False
        for match_id,g in rows.groupby('match_id',sort=False):
            pre=bankroll;stake=100.0 if mode=='flat100' else pre*.01
            bankroll+=float((g.profit*stake).sum())
            dt=pd.to_datetime(g.date.iloc[0])
            if dt==max_dd_start:
                target_peak=peak if bankroll<peak else bankroll
            peak=max(peak,bankroll)
            if dt>=max_dd_trough: trough_seen=True
            if trough_seen and target_peak is not None and bankroll>=target_peak:
                max_dd_recovery=dt;break
    signal_profits=list(rows.profit.astype(float))
    annual=[]
    bankroll=10000.0
    for season,g in rows.groupby('season',sort=True):
        start=bankroll;staked=0
        for match_id,mg in g.groupby('match_id',sort=False):
            stake=100.0 if mode=='flat100' else bankroll*.01
            staked+=stake*len(mg)
            bankroll+=float((mg.profit*stake).sum())
        annual.append({'season':int(season),'start_bankroll':start,'end_bankroll':bankroll,
                       'profit':bankroll-start,'total_staked':staked,
                       'return_on_start_bankroll':(bankroll/start-1) if start else None})
    recovery_days=None
    if max_dd_start is not None and max_dd_recovery is not None:
        recovery_days=int((max_dd_recovery-max_dd_start).days)

    # Longest time spent below a prior bankroll peak.
    bankroll=10000.0
    peak=bankroll
    peak_date=None
    underwater_start=None
    longest_underwater_days=0
    longest_underwater_start=None
    longest_underwater_end=None
    longest_underwater_open=False
    for match_id,g in rows.groupby('match_id',sort=False):
        pre=bankroll
        stake=100.0 if mode=='flat100' else pre*.01
        bankroll+=float((g.profit*stake).sum())
        dt=pd.to_datetime(g.date.iloc[0])
        if bankroll>=peak:
            if underwater_start is not None:
                dur=int((dt-underwater_start).days)
                if dur>longest_underwater_days:
                    longest_underwater_days=dur
                    longest_underwater_start=underwater_start
                    longest_underwater_end=dt
                underwater_start=None
            peak=bankroll
            peak_date=dt
        elif underwater_start is None:
            underwater_start=peak_date or dt
    if underwater_start is not None:
        dt=pd.to_datetime(rows.date.iloc[-1])
        dur=int((dt-underwater_start).days)
        if dur>longest_underwater_days:
            longest_underwater_days=dur
            longest_underwater_start=underwater_start
            longest_underwater_end=dt
            longest_underwater_open=True

    # Losing streak with dates.
    cur=0;best=0;cur_start=None;best_start=None;best_end=None
    for _,r in rows.iterrows():
        dt=pd.to_datetime(r.date)
        if float(r.profit)<0:
            if cur==0: cur_start=dt
            cur+=1
            if cur>best:
                best=cur;best_start=cur_start;best_end=dt
        else:
            cur=0;cur_start=None

    return {
      'mode':mode,'starting_bankroll':10000.0,'ending_bankroll':bankroll,
      'net_profit':bankroll-10000.0,'total_staked':total_staked,
      'signals':int(len(rows)),'unique_matches':int(rows.match_id.nunique()),
      'wins':int((rows.profit>0).sum()),'losses':int((rows.profit<0).sum()),
      'max_consecutive_losses':best,
      'max_consecutive_losses_start':None if best_start is None else best_start.date().isoformat(),
      'max_consecutive_losses_end':None if best_end is None else best_end.date().isoformat(),
      'lowest_bankroll':lowest,'lowest_bankroll_date':lowest_date,
      'max_drawdown_amount':max_dd_amt,'max_drawdown_pct':max_dd_pct,
      'max_drawdown_start':None if max_dd_start is None else max_dd_start.date().isoformat(),
      'max_drawdown_trough':None if max_dd_trough is None else max_dd_trough.date().isoformat(),
      'max_drawdown_recovery':None if max_dd_recovery is None else max_dd_recovery.date().isoformat(),
      'max_drawdown_recovery_days':recovery_days,
      'longest_underwater_days':longest_underwater_days,
      'longest_underwater_start':None if longest_underwater_start is None else longest_underwater_start.date().isoformat(),
      'longest_underwater_end':None if longest_underwater_end is None else longest_underwater_end.date().isoformat(),
      'longest_underwater_open':longest_underwater_open,
      'ending_1pct_stake':bankroll*.01 if mode=='pct1' else None,
      'average_stake':total_staked/len(rows) if len(rows) else None,
      'annual':annual,'records':rec
    }

def main():
    src=json.loads(SRC.read_text());d=pd.read_parquet(Path(src['dataset']));s=arx.merged_selection_rows(d)
    rows=pd.concat([rows_for(s,m) for m in METHODS],ignore_index=True)
    rows['date']=pd.to_datetime(rows.date,errors='coerce')
    rows['match_id']=rows.match_id.astype(str)
    rows=rows.sort_values(['date','match_id','method_id']).reset_index(drop=True)

    # Exclude all matches with methods on opposite sides.
    side_count=rows.groupby('match_id').outcome.nunique()
    conflicts=set(side_count[side_count>1].index)
    clean=rows[~rows.match_id.isin(conflicts)].copy().sort_values(['date','match_id','method_id'])
    flat=simulate(clean,'flat100');pct=simulate(clean,'pct1')

    payload={
      'built_at':pd.Timestamp.now('UTC').isoformat(),
      'definition':'Current refined active-method portfolio; opposite-side conflict matches excluded. Each method signal is one wager. Same-match same-side signals are separate wagers.',
      'flat100':flat,'pct1':pct
    }
    (OUT/'latest.json').write_text(json.dumps(payload,indent=2,default=str))
    # CSV with chronological signals for independent audit.
    csv=clean.copy()
    csv.to_csv(OUT/'clean_signals.csv',index=False)
    print(json.dumps({
      'flat100':{k:v for k,v in flat.items() if k not in ['records']},
      'pct1':{k:v for k,v in pct.items() if k not in ['records']}
    },indent=2,default=str))

if __name__=='__main__':main()
