from pathlib import Path
import json, math, os
import numpy as np
import pandas as pd

CTX=Path('/home/appwiza-runner/nfl-context-data')
REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
DATA=CTX/'coaching_everything'/'coaching_enriched_team_sides_2006_2025.parquet'
HREG=REPO/'nfl/final_methods/approved_research_methods.csv'
CANON=REPO/'nfl/frozen_holdout_discovery/canonical_frozen_holdout_methods.csv'
TRAVEL=REPO/'nfl/travel_deployment_audit/travel_methods_ready.csv'
COACH=REPO/'nfl/coaching_deployment_audit/coaching_methods_ready.csv'
PRICE=REPO/'nfl/exact_price_windows/method_price_windows.csv'
BASEV=REPO/'nfl/priority_method_loss_forensics/confirmed_holdout_veto_filters.csv'
TRAVELV=REPO/'nfl/travel_deployment_audit/travel_primary_vetoes_ready.csv'
COACHV=REPO/'nfl/coaching_deployment_audit/coaching_primary_vetoes_ready.csv'
NW=REPO/'nfl/next_wave_combinations/next_wave_survivors.csv'
OUT=CTX/'live_candidate_finalization'; OUT.mkdir(parents=True,exist_ok=True)

required=[DATA,HREG,CANON,TRAVEL,COACH,PRICE]
missing=[str(p) for p in required if not p.exists()]
if missing: raise RuntimeError('Required finalization inputs not ready: '+', '.join(missing))

def num(x):return pd.to_numeric(x,errors='coerce')
def implied(o):
    o=num(o);return np.where(o>0,100/(o+100),np.where(o<0,np.abs(o)/(np.abs(o)+100),np.nan))

d=pd.read_parquet(DATA)
d=d[d.season.between(2006,2025)].copy()
if 'prior_games' in d.columns:d=d[num(d.prior_games)>=3].copy()
d['market_prob_use']=num(d.market_prob).fillna(pd.Series(implied(d.moneyline),index=d.index)) if 'market_prob' in d else pd.Series(implied(d.moneyline),index=d.index)
price_bands={'DOG20_34':(.20,.3499),'DOG35_44':(.35,.4499),'DOG35_49':(.35,.4999),'PK_55':(.45,.55),'FAV55_65':(.55,.65),'FAV65_75':(.65,.75),'FAV75_85':(.75,.85)}

def cond(c,op,q):
    if c not in d.columns:return pd.Series(False,index=d.index)
    z=num(d[c]);return z>=float(q) if op=='>=' else z<=float(q)
def rule_mask(r):
    m=pd.Series(True,index=d.index)
    for j in [1,2,3]:
        c=r.get(f'feature{j}')
        if pd.notna(c) and str(c):m &= cond(str(c),r.get(f'op{j}'),r.get(f'threshold{j}'))
    lo=float(r.get('live_p_lo',price_bands[r.price_band][0])); hi=float(r.get('live_p_hi',price_bands[r.price_band][1]))
    m &= num(d.market_prob_use).between(lo,hi,inclusive='both')
    if r.venue=='AWAY':m &= num(d.is_home).eq(0)
    elif r.venue=='HOME':m &= num(d.is_home).eq(1)
    return m

def concentration(mask):
    x=d[mask]
    if not len(x):return (np.nan,np.nan,None)
    tc=x.team.value_counts()
    return float(tc.iloc[0]/len(x)),float(tc.head(3).sum()/len(x)),tc.index[0]
def robust(row):
    vals=[float(row.get(k,np.nan)) for k in ['train_roi','validation_roi','holdout_roi'] if pd.notna(row.get(k,np.nan))]
    floor=min(vals) if vals else -1
    n=max(1,float(row.get('full_n',1)))
    ps=float(row.get('positive_season_ratio', row.get('full_positive_seasons',0)/max(1,row.get('full_active_seasons',1))))
    return floor + .025*math.log10(n) + .08*ps

# H-series candidates.
h=pd.read_csv(HREG)
c=pd.read_csv(CANON)
h=h.merge(c[['method_id','track','train_n','validation_n','holdout_n','full_n','full_positive_seasons','full_active_seasons']].drop_duplicates('method_id'),on='method_id',how='left',suffixes=('','_canon'))
pw=pd.read_csv(PRICE)[['method_id','decision','live_p_lo','live_p_hi','live_odds_range']]
h=h.merge(pw,on='method_id',how='left')
rows=[]
for _,r in h.iterrows():
    if r.registry_status not in ['FINAL_RESEARCH','WATCHLIST']:continue
    pos=float(r.positive_season_ratio)
    row={
      'candidate_id':r.method_id,'source':'H_SERIES','source_status':r.registry_status,'track':r.track,'price_band':r.price_band,'venue':r.venue,
      'feature1':r.feature1,'op1':r.op1,'threshold1':r.threshold1,'feature2':r.get('feature2'),'op2':r.get('op2'),'threshold2':r.get('threshold2'),'feature3':np.nan,'op3':np.nan,'threshold3':np.nan,
      'train_n':r.get('train_n'),'train_roi':r.train_roi,'validation_n':r.get('validation_n'),'validation_roi':r.validation_roi,'holdout_n':r.get('holdout_n'),'holdout_roi':r.holdout_roi,'full_n':r.full_n,'full_roi':r.full_roi,
      'positive_season_ratio':pos,'live_p_lo':r.get('live_p_lo',price_bands[r.price_band][0]),'live_p_hi':r.get('live_p_hi',price_bands[r.price_band][1]),'live_odds_range':r.get('live_odds_range'),'price_decision':r.get('decision')
    }
    rows.append(row)
# Standalone travel READY methods.
t=pd.read_csv(TRAVEL).sort_values(['holdout_roi','full_n'],ascending=[False,False]).reset_index(drop=True)
for i,r in t.iterrows():
    rows.append({'candidate_id':f'NFL-T{i+1:03d}','source':'TRAVEL','source_status':'READY','track':r.track,'price_band':r.price_band,'venue':r.venue,
      'feature1':r.feature,'op1':r.op,'threshold1':r.threshold,'feature2':np.nan,'op2':np.nan,'threshold2':np.nan,'feature3':np.nan,'op3':np.nan,'threshold3':np.nan,
      'train_n':r.train_n,'train_roi':r.train_roi,'validation_n':r.validation_n,'validation_roi':r.validation_roi,'holdout_n':r.holdout_n,'holdout_roi':r.holdout_roi,'full_n':r.full_n,'full_roi':r.full_roi,
      'positive_season_ratio':r.positive_season_ratio,'live_p_lo':price_bands[r.price_band][0],'live_p_hi':price_bands[r.price_band][1],'live_odds_range':'BROAD_BAND_PENDING_PRICE_AUDIT','price_decision':'PENDING_EXACT_PRICE_AUDIT'})
# Coaching READY methods.
co=pd.read_csv(COACH).sort_values(['holdout_roi','full_n'],ascending=[False,False]).reset_index(drop=True)
for i,r in co.iterrows():
    rows.append({'candidate_id':f'NFL-C{i+1:03d}','source':'COACHING','source_status':'READY','track':r.track,'price_band':r.price_band,'venue':r.venue,
      'feature1':r.feature1,'op1':r.op1,'threshold1':r.threshold1,'feature2':r.feature2,'op2':r.op2,'threshold2':r.threshold2,'feature3':np.nan,'op3':np.nan,'threshold3':np.nan,
      'train_n':r.train_n,'train_roi':r.train_roi,'validation_n':r.validation_n,'validation_roi':r.validation_roi,'holdout_n':r.holdout_n,'holdout_roi':r.holdout_roi,'full_n':r.full_n,'full_roi':r.full_roi,
      'positive_season_ratio':r.positive_season_ratio,'live_p_lo':price_bands[r.price_band][0],'live_p_hi':price_bands[r.price_band][1],'live_odds_range':'BROAD_BAND_PENDING_PRICE_AUDIT','price_decision':'PENDING_EXACT_PRICE_AUDIT'})
# Next-wave remains WATCH until this finalizer can reproduce its full rule from available columns and concentration is acceptable.
if NW.exists():
    nw=pd.read_csv(NW).sort_values(['holdout_roi','full_n'],ascending=[False,False]).reset_index(drop=True)
    for _,r in nw.iterrows():
        row={'candidate_id':r.next_method_id,'source':'NEXT_WAVE','source_status':'RESEARCH_SURVIVOR','track':r.track,'price_band':r.price_band,'venue':r.venue,
          'feature1':r.get('feature1'),'op1':r.get('op1'),'threshold1':r.get('threshold1'),'feature2':r.get('feature2'),'op2':r.get('op2'),'threshold2':r.get('threshold2'),'feature3':r.get('feature3'),'op3':r.get('op3'),'threshold3':r.get('threshold3'),
          'train_n':r.train_n,'train_roi':r.train_roi,'validation_n':r.validation_n,'validation_roi':r.validation_roi,'holdout_n':r.holdout_n,'holdout_roi':r.holdout_roi,'full_n':r.full_n,'full_roi':r.full_roi,
          'positive_season_ratio':r.full_positive_seasons/max(1,r.full_active_seasons),'live_p_lo':price_bands[r.price_band][0],'live_p_hi':price_bands[r.price_band][1],'live_odds_range':'PENDING_AUDIT','price_decision':'PENDING_AUDIT'}
        rows.append(row)

cand=pd.DataFrame(rows)
# Concentration and exact historical bet-set signatures where all rule columns are reproducible.
masks={}; top=[]
for i,r in cand.iterrows():
    needed=[str(r[f'feature{j}']) for j in [1,2,3] if pd.notna(r.get(f'feature{j}'))]
    reproducible=all(c in d.columns for c in needed)
    if reproducible:
        m=rule_mask(r); masks[r.candidate_id]=set(zip(d.loc[m,'game_id'].astype(str),d.loc[m,'team'].astype(str)))
        ts,t3,tm=concentration(m)
    else:ts,t3,tm=np.nan,np.nan,None
    top.append((reproducible,ts,t3,tm))
cand[['rule_reproducible','top_team_share','top3_team_share','top_team']]=pd.DataFrame(top,index=cand.index)
cand['robust_score']=cand.apply(robust,axis=1)

# Greedy bet-set de-duplication: >75% Jaccard is treated as the same practical system.
cand=cand.sort_values(['robust_score','full_n'],ascending=[False,False]).reset_index(drop=True)
kept=[]; dup_of=[]; overlap=[]
for _,r in cand.iterrows():
    rid=r.candidate_id; s=masks.get(rid)
    best=None; bestj=0
    if s:
        for kid in kept:
            ks=masks.get(kid)
            if not ks:continue
            j=len(s&ks)/max(1,len(s|ks))
            if j>bestj:bestj=j;best=kid
    if bestj>=.75:
        dup_of.append(best);overlap.append(bestj)
    else:
        kept.append(rid);dup_of.append('');overlap.append(bestj)
cand['duplicate_of']=dup_of;cand['max_jaccard_overlap']=overlap

# Attach conservative veto policy. Never stack newly discovered vetoes automatically.
base_primary={}
if BASEV.exists():
    v=pd.read_csv(BASEV); v=v[v.confirmed_veto.eq(True)].sort_values(['method_id','holdout_roi_change'],ascending=[True,False]).drop_duplicates('method_id')
    for _,r in v.iterrows():base_primary[r.method_id]=('BASE',r.feature_or_combo,r.op,r.threshold)
travel_primary={}
if TRAVELV.exists():
    v=pd.read_csv(TRAVELV)
    for _,r in v.iterrows():travel_primary[r.method_id]=('TRAVEL',r.feature,r.op,r.threshold)
coach_primary={}
if COACHV.exists():
    v=pd.read_csv(COACHV)
    for _,r in v.iterrows():coach_primary[r.method_id]=('COACHING',r.feature,r.op,r.threshold)
vp=[]
for _,r in cand.iterrows():
    mid=r.candidate_id; z=base_primary.get(mid) or travel_primary.get(mid) or coach_primary.get(mid)
    vp.append(z if z else ('','','',np.nan))
cand[['hard_veto_source','hard_veto_feature','hard_veto_op','hard_veto_threshold']]=pd.DataFrame(vp,index=cand.index)

# Initial deployment classes. Travel/coaching/NW stay WATCH until their exact price windows are independently audited.
statuses=[]; reasons=[]
for _,r in cand.iterrows():
    if r.duplicate_of:
        statuses.append('RESEARCH_ONLY'); reasons.append(f'OVERLAPS_{r.duplicate_of}_{r.max_jaccard_overlap:.2f}'); continue
    concentration_ok=(bool(r.rule_reproducible) and (pd.isna(r.top_team_share) or r.top_team_share<=.15) and (pd.isna(r.top3_team_share) or r.top3_team_share<=.35))
    stats_ready=(r.full_n>=120 and r.holdout_n>=30 and r.full_roi>=.15 and r.train_roi>=.10 and r.validation_roi>=.10 and r.holdout_roi>=.15 and r.positive_season_ratio>=.70)
    if r.source=='H_SERIES' and r.source_status=='FINAL_RESEARCH' and stats_ready and concentration_ok and r.price_decision in ['TIGHTEN_LIVE_PRICE','KEEP_ORIGINAL_BAND']:
        statuses.append('LIVE_READY'); reasons.append('STRICT_STATS_PRICE_AND_CONCENTRATION_PASS')
    elif r.full_roi>=.10 and r.holdout_roi>=.10:
        statuses.append('WATCHLIST'); reasons.append('NEEDS_FINAL_PRICE_OR_ADDITIONAL_DEPLOYMENT_AUDIT' if r.source!='H_SERIES' else 'BELOW_STRICT_LIVE_READY_GATE')
    else:
        statuses.append('RESEARCH_ONLY'); reasons.append('BELOW_ROBUSTNESS_GATE')
cand['deployment_status']=statuses;cand['deployment_reason']=reasons
cand.to_csv(OUT/'final_candidate_registry.csv',index=False)
cand[cand.deployment_status.eq('LIVE_READY')].to_csv(OUT/'live_ready.csv',index=False)
cand[cand.deployment_status.eq('WATCHLIST')].to_csv(OUT/'watchlist.csv',index=False)
cand[cand.deployment_status.eq('RESEARCH_ONLY')].to_csv(OUT/'research_only.csv',index=False)
summary={'candidates':int(len(cand)),'live_ready':int((cand.deployment_status=='LIVE_READY').sum()),'watchlist':int((cand.deployment_status=='WATCHLIST').sum()),'research_only':int((cand.deployment_status=='RESEARCH_ONLY').sum()),'duplicates_removed':int((cand.duplicate_of!='').sum())}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
lines=['NFL FINAL LIVE-CANDIDATE REGISTRY','',json.dumps(summary,indent=2),'','LIVE READY']
for _,r in cand[cand.deployment_status.eq('LIVE_READY')].sort_values(['holdout_roi','full_n'],ascending=[False,False]).iterrows():
    rule=f"{r.feature1} {r.op1} {r.threshold1:.5g}"+(f" AND {r.feature2} {r.op2} {r.threshold2:.5g}" if pd.notna(r.feature2) else '')
    lines.append(f"{r.candidate_id} | {r.price_band} {r.venue} {r.live_odds_range} | {rule} | n={int(r.full_n)} ROI={100*r.full_roi:+.1f}% holdout={100*r.holdout_roi:+.1f}%"+(f" | HARD VETO {r.hard_veto_feature} {r.hard_veto_op} {r.hard_veto_threshold}" if r.hard_veto_feature else ''))
lines+=['','WATCHLIST']
for _,r in cand[cand.deployment_status.eq('WATCHLIST')].head(40).iterrows():
    lines.append(f"{r.candidate_id} [{r.source}] n={int(r.full_n)} ROI={100*r.full_roi:+.1f}% holdout={100*r.holdout_roi:+.1f}% | {r.deployment_reason}")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n')
print('\n'.join(lines))
