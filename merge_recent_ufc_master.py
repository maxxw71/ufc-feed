from __future__ import annotations
import json,re
from pathlib import Path
from datetime import datetime,timezone
import numpy as np
import pandas as pd

OUT=Path('ufc_recent_master'); OUT.mkdir(exist_ok=True)
STAKE=50.0


def norm(s):
    s=str(s or '').lower().replace('’',"'").replace('-',' ')
    s=re.sub(r'\b(jr|sr|ii|iii|iv)\b',' ',s)
    s=re.sub(r'[^a-z0-9]+',' ',s)
    return re.sub(r'\s+',' ',s).strip()

def bootstrap_ci(profits,n_boot=10000,seed=42):
    arr=np.asarray(profits,float)
    if len(arr)<10:return np.nan,np.nan
    rng=np.random.default_rng(seed); n=len(arr); vals=[]
    for _ in range(n_boot):
        x=rng.choice(arr,size=n,replace=True); vals.append(x.sum()/(n*STAKE))
    return float(np.quantile(vals,.025)),float(np.quantile(vals,.975))

def metrics(df):
    if df.empty:return {'bets':0,'wins':0,'win_rate':np.nan,'profit':0.0,'roi':np.nan,'ci_low':np.nan,'ci_high':np.nan}
    p=float(df.profit.sum());lo,hi=bootstrap_ci(df.profit)
    return {'bets':len(df),'wins':int(df.won.sum()),'win_rate':float(df.won.mean()),'profit':p,'roi':p/(len(df)*STAKE),'ci_low':lo,'ci_high':hi}

def load_exact():
    p=Path('ufc_recent_exactid_backfill/matched_recent_fights.csv')
    x=pd.read_csv(p,low_memory=False)
    x=x.rename(columns={'player1':'fighter_1','player2':'fighter_2'})
    x['source_dataset']='exact_id_ufcstats_mirror'
    return x

def load_v2():
    p=Path('ufc_recent_daily_backfill_v2/matched_recent_fights.csv')
    x=pd.read_csv(p,low_memory=False)
    x['source_dataset']='global_mma_backfill'
    return x

def standardize(x):
    x=x.copy()
    x['event_date']=pd.to_datetime(x['event_date'],errors='coerce').dt.normalize()
    x['fighter_1_norm']=x['fighter_1'].map(norm)
    x['fighter_2_norm']=x['fighter_2'].map(norm)
    x['pair_key']=x.apply(lambda r:'|'.join(sorted((r.fighter_1_norm,r.fighter_2_norm))),axis=1)
    x['fight_key']=x['event_date'].astype(str)+'|'+x['pair_key']
    keep=['event_date','year','fighter_1','fighter_2','favorite','underdog','favorite_age','underdog_age','younger_advantage','market_prob','favorite_decimal_odds','won','profit','snapshot_mode','source_dataset','fight_key']
    for c in keep:
        if c not in x.columns:x[c]=np.nan
    return x[keep]

def main():
    exact=standardize(load_exact())
    v2=standardize(load_v2())
    # Prefer exact UFCStats URL-linked rows where overlapping; use global MMA rows to fill gaps, especially 2026.
    combined=pd.concat([exact,v2],ignore_index=True)
    combined['priority']=combined.source_dataset.map({'exact_id_ufcstats_mirror':0,'global_mma_backfill':1}).fillna(9)
    combined=combined.sort_values(['fight_key','priority']).drop_duplicates('fight_key',keep='first').drop(columns='priority').sort_values('event_date').reset_index(drop=True)
    combined['year']=combined.event_date.dt.year
    combined.to_csv(OUT/'master_recent_fights.csv',index=False)

    hybrid=combined[(combined.market_prob>=.70)&(combined.younger_advantage>=3)].copy()
    strong=combined[(combined.market_prob>=.70)&(combined.younger_advantage>=4)].copy()
    hybrid.to_csv(OUT/'master_hybrid_3yr_bets.csv',index=False)
    strong.to_csv(OUT/'master_hybrid_4yr_bets.csv',index=False)

    def summary(d):
        rs=[]
        for y in sorted(d.year.dropna().unique()):rs.append({'segment':str(int(y)),**metrics(d[d.year==y])})
        rs.append({'segment':'2024-current',**metrics(d)})
        return pd.DataFrame(rs)
    s3=summary(hybrid);s4=summary(strong)
    s3.to_csv(OUT/'master_hybrid_3yr_summary.csv',index=False);s4.to_csv(OUT/'master_hybrid_4yr_summary.csv',index=False)

    src=combined.groupby(['year','source_dataset']).size().reset_index(name='fights')
    src.to_csv(OUT/'source_breakdown.csv',index=False)

    lines=['UFC RECENT MASTER DATASET — MERGED SOURCES','='*76,'',f'Exact-ID source rows: {len(exact)}',f'Global MMA source rows: {len(v2)}',f'Deduped recent master fights: {len(combined)}',f'Master date range: {combined.event_date.min().date()} to {combined.event_date.max().date()}','','SOURCE BREAKDOWN','-'*76]
    for _,r in src.iterrows():lines.append(f"{int(r.year)} {r.source_dataset}: {int(r.fights)} fights")
    lines+=['','3+ YEAR HYBRID RULE','-'*76]
    for _,r in s3.iterrows():
        ci='n/a' if pd.isna(r.ci_low) else f'[{r.ci_low*100:+.2f}%, {r.ci_high*100:+.2f}%]'
        lines.append(f'{r.segment:<14} bets={int(r.bets):4d} win={r.win_rate*100:7.2f}% ROI={r.roi*100:+8.2f}% P/L=${r.profit:+,.2f} 95%CI={ci}')
    lines+=['','4+ YEAR STRONG TIER','-'*76]
    for _,r in s4.iterrows():lines.append(f'{r.segment:<14} bets={int(r.bets):4d} win={r.win_rate*100:7.2f}% ROI={r.roi*100:+8.2f}% P/L=${r.profit:+,.2f}')
    (OUT/'report.txt').write_text('\n'.join(lines)+'\n');print((OUT/'report.txt').read_text(),flush=True)
    (OUT/'summary.json').write_text(json.dumps({'generated_at':datetime.now(timezone.utc).isoformat(),'master_fights':len(combined),'hybrid_3yr':s3.to_dict(orient='records'),'hybrid_4yr':s4.to_dict(orient='records'),'source_breakdown':src.to_dict(orient='records')},indent=2,default=str))

if __name__=='__main__':main()
