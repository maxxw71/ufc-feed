from __future__ import annotations

import io, json, re, zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

OUT = Path('ufc_recent_exactid_backfill')
OUT.mkdir(exist_ok=True)
STAKE=50.0; MARKET_MIN=.70; GAP_MIN=3.0; STRONG_GAP=4.0
COMP_URL='https://raw.githubusercontent.com/DanMcInerney/mma-ai/main/data/raw/ufcstats/competitions.csv'
IND_URL='https://raw.githubusercontent.com/DanMcInerney/mma-ai/main/data/raw/ufcstats/individuals.csv'
ODDS_URL='https://www.kaggle.com/api/v1/datasets/download/binduvr/ufc-betting-odds'


def canon_url(u):
    s=str(u or '').strip().replace('https://','http://')
    return s.rstrip('/')

def norm_name(s):
    s=str(s or '').lower().replace('’',"'").replace('-',' ')
    s=re.sub(r'\b(jr|sr|ii|iii|iv)\b',' ',s)
    s=re.sub(r'[^a-z0-9]+',' ',s)
    return re.sub(r'\s+',' ',s).strip()

def exact_age(dob,event_date):
    return (pd.Timestamp(event_date)-pd.Timestamp(dob)).days/365.2425

def american_profit(stake,d): return stake*(float(d)-1.0)

def bootstrap_ci(profits,n_boot=10000,seed=42):
    arr=np.asarray(profits,float)
    if len(arr)<10:return np.nan,np.nan
    rng=np.random.default_rng(seed); n=len(arr); vals=[]
    for _ in range(n_boot):
        s=rng.choice(arr,size=n,replace=True); vals.append(s.sum()/(n*STAKE))
    return float(np.quantile(vals,.025)),float(np.quantile(vals,.975))

def metrics(df):
    if df.empty:return {'bets':0,'wins':0,'win_rate':np.nan,'profit':0.0,'roi':np.nan,'ci_low':np.nan,'ci_high':np.nan}
    p=float(df.profit.sum()); lo,hi=bootstrap_ci(df.profit.to_numpy())
    return {'bets':len(df),'wins':int(df.won.sum()),'win_rate':float(df.won.mean()),'profit':p,'roi':p/(len(df)*STAKE),'ci_low':lo,'ci_high':hi}


def load_results():
    c=pd.read_csv(COMP_URL,low_memory=False)
    c['event_date']=pd.to_datetime(c['event_date'],errors='coerce').dt.normalize()
    c=c[(c.event_date>=pd.Timestamp('2024-01-01'))&(c.event_date<=pd.Timestamp('2026-09-11'))].copy()
    c['p1_url']=c['player1_url'].map(canon_url); c['p2_url']=c['player2_url'].map(canon_url)
    c['p1_norm']=c['player1'].map(norm_name); c['p2_norm']=c['player2'].map(norm_name)
    c['url_pair']=c.apply(lambda r: tuple(sorted((r.p1_url,r.p2_url))),axis=1)
    c=c[c['result'].astype(str).isin(['W','L','D','NC'])].copy()

    ind=pd.read_csv(IND_URL,low_memory=False)
    ind['url_c']=ind['url'].map(canon_url)
    ind['dob_p']=pd.to_datetime(ind['dob'].replace('--',np.nan),errors='coerce')
    dob={r.url_c:pd.Timestamp(r.dob_p) for _,r in ind.dropna(subset=['dob_p']).iterrows()}
    return c,dob


def load_odds():
    r=requests.get(ODDS_URL,timeout=180); r.raise_for_status()
    z=zipfile.ZipFile(io.BytesIO(r.content)); csv=next(n for n in z.namelist() if n.lower().endswith('.csv'))
    with z.open(csv) as fh:x=pd.read_csv(fh,low_memory=False)
    x['event_date']=pd.to_datetime(x['event_date'],errors='coerce').dt.normalize()
    x['adding_date']=pd.to_datetime(x['adding_date'],errors='coerce',utc=True)
    x['odds_1']=pd.to_numeric(x['odds_1'],errors='coerce'); x['odds_2']=pd.to_numeric(x['odds_2'],errors='coerce')
    x=x.dropna(subset=['event_date','fighter_1_url','fighter_2_url','odds_1','odds_2'])
    x=x[(x.event_date>=pd.Timestamp('2024-01-01'))&(x.event_date<=pd.Timestamp('2026-09-11'))&(x.odds_1>1)&(x.odds_2>1)].copy()
    x['u1']=x['fighter_1_url'].map(canon_url); x['u2']=x['fighter_2_url'].map(canon_url)
    x['n1']=x['fighter_1'].map(norm_name); x['n2']=x['fighter_2'].map(norm_name)
    x['url_pair']=x.apply(lambda r:tuple(sorted((r.u1,r.u2))),axis=1)

    sel=[]
    for (pair,ev),g in x.groupby(['url_pair','event_date'],sort=False):
        us=g[g.region.fillna('').astype(str).str.lower().eq('us')]
        if not us.empty:g=us
        cutoff=pd.Timestamp(ev,tz='UTC')+pd.Timedelta(hours=36)
        timely=g[g.adding_date.notna()&(g.adding_date<=cutoff)]
        if not timely.empty:
            latest=timely.adding_date.max(); snap=timely[timely.adding_date==latest].copy(); mode='pre_or_event_snapshot'
        else:
            latest=g.adding_date.max() if g.adding_date.notna().any() else pd.NaT
            snap=g[g.adding_date==latest].copy() if pd.notna(latest) else g.copy(); mode='historical_import_fallback'
        # Normalize every sportsbook row to the same fighter URL orientation
        # before aggregating. The raw source can reverse fighter_1/fighter_2 across
        # books; aggregating unaligned odds_1/odds_2 creates false favorite sides.
        r0=snap.iloc[0]; ref1,ref2=r0.n1,r0.n2
        direct=snap.n1.eq(ref1)&snap.n2.eq(ref2)
        reverse=snap.n1.eq(ref2)&snap.n2.eq(ref1)
        invalid=~(direct|reverse)
        if invalid.any():
            snap=snap.loc[~invalid].copy()
            direct=snap.n1.eq(ref1)&snap.n2.eq(ref2)
            reverse=snap.n1.eq(ref2)&snap.n2.eq(ref1)
        if snap.empty: continue
        snap['aligned_odds_1']=np.where(direct,snap.odds_1,snap.odds_2).astype(float)
        snap['aligned_odds_2']=np.where(direct,snap.odds_2,snap.odds_1).astype(float)
        i1=1/snap.aligned_odds_1; i2=1/snap.aligned_odds_2; tot=i1+i2
        nv1=float((i1/tot).median()); nv2=float((i2/tot).median()); z=nv1+nv2; nv1/=z; nv2/=z
        if nv1>=nv2: favside=1; mp=nv1; best=float(snap.aligned_odds_1.max())
        else: favside=2; mp=nv2; best=float(snap.aligned_odds_2.max())
        sel.append({'event_date':ev,'fighter_1':r0.fighter_1,'fighter_2':r0.fighter_2,'n1':r0.n1,'n2':r0.n2,'u1':r0.u1,'u2':r0.u2,'url_pair':pair,'favorite_side_odds':favside,'market_prob':mp,'favorite_decimal_odds':best,'snapshot_mode':mode,'orientation_reversed_rows':int(reverse.sum()),'orientation_invalid_rows':int(invalid.sum()),'fight_url':r0.get('fight_url'),'source':';'.join(sorted(set(snap.source.dropna().astype(str)))),'region':';'.join(sorted(set(snap.region.dropna().astype(str))))})
    return pd.DataFrame(sel)


def main():
    comp,dob=load_results(); odds=load_odds()
    pair_index={}
    for _,r in comp.iterrows():pair_index.setdefault(r.url_pair,[]).append(r)
    rows=[]; unmatched=[]; reason_counts={}
    for _,o in odds.iterrows():
        cands=pair_index.get(o.url_pair,[])
        if not cands:
            reason_counts['url_pair_not_found']=reason_counts.get('url_pair_not_found',0)+1; unmatched.append({**o.to_dict(),'reason':'url_pair_not_found'}); continue
        od=pd.Timestamp(o.event_date).normalize(); r=min(cands,key=lambda rr:abs((pd.Timestamp(rr.event_date).normalize()-od).days))
        delta=int((pd.Timestamp(r.event_date).normalize()-od).days)
        if abs(delta)>2:
            reason_counts['date_gt_2']=reason_counts.get('date_gt_2',0)+1; unmatched.append({**o.to_dict(),'reason':'date_gt_2','delta':delta}); continue
        d1=dob.get(r.p1_url); d2=dob.get(r.p2_url)
        if d1 is None or d2 is None or pd.isna(d1) or pd.isna(d2):
            reason_counts['missing_dob']=reason_counts.get('missing_dob',0)+1; unmatched.append({**o.to_dict(),'reason':'missing_dob'}); continue
        # Source odds_1/odds_2 follow fighter_1/fighter_2 NAMES. Recent source
        # rows can have fighter URLs swapped, so URLs are used only to identify
        # the unordered fight pair; price orientation is validated by names.
        direct=(o.n1==r.p1_norm and o.n2==r.p2_norm); reverse=(o.n1==r.p2_norm and o.n2==r.p1_norm)
        if not direct and not reverse:
            reason_counts['name_orientation_failed']=reason_counts.get('name_orientation_failed',0)+1
            unmatched.append({**o.to_dict(),'reason':'name_orientation_failed','result_player1':r.player1,'result_player2':r.player2})
            continue
        fav_is_p1=(int(o.favorite_side_odds)==1) if direct else (int(o.favorite_side_odds)==2)
        age1=exact_age(d1,r.event_date); age2=exact_age(d2,r.event_date)
        fav_age=age1 if fav_is_p1 else age2; dog_age=age2 if fav_is_p1 else age1; younger=dog_age-fav_age
        res=str(r.result)
        if res not in ['W','L']:
            reason_counts['draw_or_nc']=reason_counts.get('draw_or_nc',0)+1; continue
        p1_won=(res=='W'); won=p1_won if fav_is_p1 else (not p1_won)
        profit=american_profit(STAKE,o.favorite_decimal_odds) if won else -STAKE
        rows.append({'event_date':pd.Timestamp(r.event_date),'year':int(pd.Timestamp(r.event_date).year),'player1':r.player1,'player2':r.player2,'player1_url':r.p1_url,'player2_url':r.p2_url,'favorite':r.player1 if fav_is_p1 else r.player2,'underdog':r.player2 if fav_is_p1 else r.player1,'favorite_age':fav_age,'underdog_age':dog_age,'younger_advantage':younger,'market_prob':float(o.market_prob),'favorite_decimal_odds':float(o.favorite_decimal_odds),'won':bool(won),'profit':float(profit),'date_offset_days':delta,'snapshot_mode':o.snapshot_mode,'fight_url':o.fight_url,'source':o.source,'region':o.region})
    df=pd.DataFrame(rows).drop_duplicates(subset=['event_date','player1_url','player2_url']).sort_values('event_date').reset_index(drop=True)
    df.to_csv(OUT/'matched_recent_fights.csv',index=False); pd.DataFrame(unmatched).to_csv(OUT/'unmatched_odds.csv',index=False)
    hybrid=df[(df.market_prob>=MARKET_MIN)&(df.younger_advantage>=GAP_MIN)].copy(); strong=df[(df.market_prob>=MARKET_MIN)&(df.younger_advantage>=STRONG_GAP)].copy()
    hybrid.to_csv(OUT/'hybrid_3yr_bets.csv',index=False); strong.to_csv(OUT/'hybrid_4yr_bets.csv',index=False)
    def summ(d):
        rs=[]
        for y in sorted(d.year.unique()):rs.append({'segment':str(int(y)),**metrics(d[d.year==y])})
        rs.append({'segment':'2024-current',**metrics(d)}); return pd.DataFrame(rs)
    s3=summ(hybrid); s4=summ(strong); s3.to_csv(OUT/'hybrid_3yr_summary.csv',index=False); s4.to_csv(OUT/'hybrid_4yr_summary.csv',index=False)
    lines=['UFC 2024-CURRENT EXACT-ID BACKFILL','= '*38,'',f'Completed UFCStats mirror rows 2024-current: {len(comp)}',f'Unique odds fights 2024-current: {len(odds)}',f'Exact URL-pair + date + DOB matched fights: {len(df)}',f'Coverage vs completed mirror rows: {len(df)/len(comp)*100:.1f}%','','3+ YEAR HYBRID RULE','-'*76]
    for _,r in s3.iterrows():
        ci='n/a' if pd.isna(r.ci_low) else f'[{r.ci_low*100:+.2f}%, {r.ci_high*100:+.2f}%]'; lines.append(f'{r.segment:<14} bets={int(r.bets):4d} win={r.win_rate*100:7.2f}% ROI={r.roi*100:+8.2f}% P/L=${r.profit:+,.2f} 95%CI={ci}')
    lines+=['','4+ YEAR STRONG TIER','-'*76]
    for _,r in s4.iterrows(): lines.append(f'{r.segment:<14} bets={int(r.bets):4d} win={r.win_rate*100:7.2f}% ROI={r.roi*100:+8.2f}% P/L=${r.profit:+,.2f}')
    lines+=['','UNMATCHED REASONS','-'*76]
    for k,v in sorted(reason_counts.items(),key=lambda kv:-kv[1]):lines.append(f'{k}: {v}')
    (OUT/'report.txt').write_text('\n'.join(lines)+'\n'); print((OUT/'report.txt').read_text(),flush=True)
    (OUT/'summary.json').write_text(json.dumps({'generated_at':datetime.now(timezone.utc).isoformat(),'completed_rows':len(comp),'odds_fights':len(odds),'matched_fights':len(df),'coverage_pct':len(df)/len(comp)*100,'reasons':reason_counts,'hybrid_3yr':s3.to_dict(orient='records'),'hybrid_4yr':s4.to_dict(orient='records')},indent=2,default=str))

if __name__=='__main__':main()
