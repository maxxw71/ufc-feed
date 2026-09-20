#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$HOME/ufc-predictor-v1"
IN="$ROOT/ufc_reach_method_analysis/reach_market_sample.csv"
OUT="$ROOT/new_category_discovery"
CACHE="$OUT/prefight_favorite_features.csv"
mkdir -p "$OUT"

[ -s "$IN" ] || { echo "ERROR: Missing $IN"; exit 2; }
source "$ROOT/venv/bin/activate"

python -u - "$IN" "$CACHE" "$OUT" <<'PY'
import sys,re
from pathlib import Path
from collections import deque
import numpy as np
import pandas as pd

inp,cache,outdir=map(Path,sys.argv[1:4])

def progress(p,msg):
    print(f"[{p:3d}%] {msg}",flush=True)

def b(v):
    if isinstance(v,(bool,np.bool_)): return bool(v)
    return str(v).strip().lower() in {'true','1','yes','w','win'}

def num(v,default=np.nan):
    try: return float(v)
    except Exception: return default

def parse_of(v):
    if pd.isna(v): return 0.0,0.0
    m=re.search(r'(\d+)\s+of\s+(\d+)',str(v),re.I)
    return (float(m.group(1)),float(m.group(2))) if m else (0.0,0.0)

def parse_ctrl(v):
    if pd.isna(v): return 0.0
    m=re.match(r'\s*(\d+):(\d+)\s*$',str(v))
    return (60*int(m.group(1))+int(m.group(2)))/60.0 if m else 0.0

def parse_single(v):
    if pd.isna(v): return 0.0
    m=re.search(r'-?\d+(?:\.\d+)?',str(v))
    return float(m.group()) if m else 0.0

def fight_minutes(r):
    try: rnd=max(1,int(float(r.get('round',1) or 1)))
    except Exception: rnd=1
    m=re.match(r'(\d+):(\d+)',str(r.get('time','0:00')))
    sec=(int(m.group(1))*60+int(m.group(2))) if m else 0
    return max(1,((rnd-1)*300+sec))/60.0

def new_state():
    return {'fights':0,'wins':0,'losses':0,'mins':0.0,
            'sig_l':0.0,'sig_a':0.0,'sig_abs':0.0,'sig_abs_a':0.0,
            'kd':0.0,'kd_abs':0.0,
            'td_l':0.0,'td_a':0.0,'td_allowed':0.0,'td_faced':0.0,
            'sub':0.0,'ctrl':0.0,'ctrl_allowed':0.0,'ground_l':0.0,
            'finish_wins':0,'finish_losses':0,'last_date':None,'recent':deque(maxlen=5)}

def prof(q,target_date):
    if not q or q['fights']<=0 or q['mins']<=0:return None
    sc15=15.0/q['mins']; m=q['mins']
    last3=list(q['recent'])[-3:]
    last5=list(q['recent'])[-5:]
    return {
        'fights':q['fights'],'wins':q['wins'],'losses':q['losses'],
        'win_pct':q['wins']/max(1,q['wins']+q['losses']),
        'last3':float(np.mean(last3)) if last3 else np.nan,
        'last5':float(np.mean(last5)) if last5 else np.nan,
        'layoff':(target_date-q['last_date']).days if q['last_date'] is not None else np.nan,
        'sig_l_pm':q['sig_l']/m,'sig_abs_pm':q['sig_abs']/m,
        'sig_diff_pm':(q['sig_l']-q['sig_abs'])/m,
        'sig_acc':q['sig_l']/q['sig_a'] if q['sig_a']>0 else np.nan,
        'sig_def':1-q['sig_abs']/q['sig_abs_a'] if q['sig_abs_a']>0 else np.nan,
        'kd15':q['kd']*sc15,'kd_abs15':q['kd_abs']*sc15,
        'td_l15':q['td_l']*sc15,'td_a15':q['td_a']*sc15,
        'td_acc':q['td_l']/q['td_a'] if q['td_a']>0 else np.nan,
        'td_def':1-q['td_allowed']/q['td_faced'] if q['td_faced']>0 else np.nan,
        'sub15':q['sub']*sc15,'ctrl15':q['ctrl']*sc15,
        'ctrl_allowed15':q['ctrl_allowed']*sc15,'ground_l15':q['ground_l']*sc15,
        'finish_win_pct':q['finish_wins']/q['wins'] if q['wins']>0 else np.nan,
        'finish_loss_pct':q['finish_losses']/q['losses'] if q['losses']>0 else np.nan,
    }

progress(2,'Starting new-category discovery scan')
if cache.exists() and cache.stat().st_size>0:
    t=pd.read_csv(cache,low_memory=False)
    t['event_date']=pd.to_datetime(t['event_date'],errors='coerce')
    progress(25,f'Reused cached pre-fight feature table: {len(t):,} favorite fights')
else:
    progress(5,'Building pre-fight striking/grappling/form profiles from 2006-2026 history')
    df=pd.read_csv(inp,low_memory=False)
    df['event_date']=pd.to_datetime(df['event_date'],errors='coerce').dt.normalize()
    df=df.dropna(subset=['event_date','player1','player2','player1_url','player2_url']).sort_values(['event_date']).reset_index(drop=True)
    states={}; rows=[]; total=len(df)
    for i,(_,r) in enumerate(df.iterrows(),1):
        d=r.event_date
        u1=str(r.player1_url); u2=str(r.player2_url)
        s1=states.get(u1); s2=states.get(u2)
        p1=prof(s1,d); p2=prof(s2,d)
        fav_is_p1=b(r.get('market_fav_is_p1',False))
        mkt=num(r.get('market_prob'))
        dec=num(r.get('fav_decimal'))
        pnl=num(r.get('profit_100'))
        if pd.notna(mkt) and pd.notna(dec) and pd.notna(pnl) and p1 is not None and p2 is not None:
            fp,op=(p1,p2) if fav_is_p1 else (p2,p1)
            fav_name=r.player1 if fav_is_p1 else r.player2
            opp_name=r.player2 if fav_is_p1 else r.player1
            age1=num(r.get('age1')); age2=num(r.get('age2')); re1=num(r.get('reach1')); re2=num(r.get('reach2'))
            age_adv=(age2-age1) if fav_is_p1 else (age1-age2)
            reach_adv=(re1-re2) if fav_is_p1 else (re2-re1)
            result=str(r.get('result','')).strip().upper()
            fav_won=(result.startswith('W') if fav_is_p1 else result.startswith('L'))
            row={'event_date':d,'favorite':fav_name,'opponent':opp_name,'market_prob':mkt,'fav_decimal':dec,'profit100':pnl,'won':fav_won,'age_adv':age_adv,'reach_adv':reach_adv}
            row.update({f'f_{k}':v for k,v in fp.items()}); row.update({f'o_{k}':v for k,v in op.items()})
            rows.append(row)
        # update both fighter histories with THIS fight only after target profile was captured
        mins=fight_minutes(r); result=str(r.get('result','')).strip().upper(); p1win=result.startswith('W'); p2win=result.startswith('L')
        method=str(r.get('method','')).upper(); is_finish=('KO' in method or 'TKO' in method or 'SUB' in method)
        for side,url,opp_side,won,lost in [(1,u1,2,p1win,p2win),(2,u2,1,p2win,p1win)]:
            q=states.setdefault(url,new_state())
            sig_l=sig_a=sig_abs=sig_abs_a=kd=kd_abs=td_l=td_a=td_allowed=td_faced=sub=ctrl=ctrl_allowed=ground_l=0.0
            for rd in range(1,6):
                a,c=parse_of(r.get(f'p{side}_rd{rd}_Sig_str')); sig_l+=a; sig_a+=c
                a,c=parse_of(r.get(f'p{opp_side}_rd{rd}_Sig_str')); sig_abs+=a; sig_abs_a+=c
                kd+=parse_single(r.get(f'p{side}_rd{rd}_KD')); kd_abs+=parse_single(r.get(f'p{opp_side}_rd{rd}_KD'))
                a,c=parse_of(r.get(f'p{side}_rd{rd}_Td')); td_l+=a; td_a+=c
                a,c=parse_of(r.get(f'p{opp_side}_rd{rd}_Td')); td_allowed+=a; td_faced+=c
                sub+=parse_single(r.get(f'p{side}_rd{rd}_Sub_att'))
                ctrl+=parse_ctrl(r.get(f'p{side}_rd{rd}_Ctrl')); ctrl_allowed+=parse_ctrl(r.get(f'p{opp_side}_rd{rd}_Ctrl'))
                a,_=parse_of(r.get(f'p{side}_rd{rd}_Ground')); ground_l+=a
            q['fights']+=1; q['mins']+=mins; q['sig_l']+=sig_l; q['sig_a']+=sig_a; q['sig_abs']+=sig_abs; q['sig_abs_a']+=sig_abs_a
            q['kd']+=kd; q['kd_abs']+=kd_abs; q['td_l']+=td_l; q['td_a']+=td_a; q['td_allowed']+=td_allowed; q['td_faced']+=td_faced
            q['sub']+=sub; q['ctrl']+=ctrl; q['ctrl_allowed']+=ctrl_allowed; q['ground_l']+=ground_l; q['last_date']=d
            if won:
                q['wins']+=1; q['recent'].append(1); q['finish_wins']+=int(is_finish)
            elif lost:
                q['losses']+=1; q['recent'].append(0); q['finish_losses']+=int(is_finish)
        if i % max(1,total//10)==0:
            progress(5+int(18*i/total),f'Built profiles through {i:,}/{total:,} historical fights')
    t=pd.DataFrame(rows)
    cache.parent.mkdir(parents=True,exist_ok=True); t.to_csv(cache,index=False)
    progress(25,f'Cached {len(t):,} favorite-side pre-fight rows')

if __import__('os').environ.get('UFC_REBUILD_CACHE_ONLY')=='1':
    progress(100,f'Cache rebuild complete: {len(t):,} favorite-side pre-fight rows')
    raise SystemExit(0)

# numeric cleanup
for c in t.columns:
    if c not in {'event_date','favorite','opponent'}:
        t[c]=pd.to_numeric(t[c],errors='coerce')
t['event_date']=pd.to_datetime(t['event_date'],errors='coerce')
t=t.dropna(subset=['event_date','market_prob','profit100'])

# existing-category overlap flags
age=(t.market_prob>=.70)&(t.age_adv>=3)
reach=(t.market_prob>=.65)&(t.reach_adv>=4)
struct=(t.market_prob>=.65)&(t.age_adv>=2)&(t.reach_adv>=6)
wrest=(t.f_fights>=2)&(t.o_fights>=2)&(t.f_td_a15>=5)&(t.f_td_l15>=1)&(t.f_ctrl15>=1)&(t.o_td_a15<=2)&(t.o_ctrl15<=.75)
existing_any=age|reach|struct|wrest

def metrics(mask):
    g=t[mask].copy(); n=len(g)
    if n==0:return None
    old=g[g.event_date<pd.Timestamp('2020-01-01')]; rec=g[g.event_date>=pd.Timestamp('2020-01-01')]
    roi=lambda z: float(z.profit100.sum()/(100*len(z))) if len(z) else np.nan
    return {'n':n,'wins':int(g.won.sum()),'win_rate':float(g.won.mean()),'roi':roi(g),'pre_n':len(old),'pre_roi':roi(old),'recent_n':len(rec),'recent_roi':roi(rec),
            'age_overlap':float(age[mask].mean()),'reach_overlap':float(reach[mask].mean()),'struct_overlap':float(struct[mask].mean()),'wrest_overlap':float(wrest[mask].mean()),'existing_any_overlap':float(existing_any[mask].mean())}

results=[]
def add(family,rule,mask):
    s=metrics(mask.fillna(False))
    if not s:return
    results.append({'family':family,'rule':rule,**s})

mkt=[.50,.55,.60,.65,.70,.75]
mins=[2,3,4]
progress(30,'Scanning striking-differential categories')
for pm in mkt:
  for mf in mins:
    for gap in [.75,1.0,1.25,1.5,2.0]:
      for ff in [-.5,0,.5]:
        add('STRIKING DIFFERENTIAL',f'mkt>={pm:.2f}, prior>={mf}, sigDiffGap>={gap:.2f}/min, favSigDiff>={ff:+.1f}',(t.market_prob>=pm)&(t.f_fights>=mf)&(t.o_fights>=mf)&((t.f_sig_diff_pm-t.o_sig_diff_pm)>=gap)&(t.f_sig_diff_pm>=ff))
progress(40,'Scanning striking-defense and pace categories')
for pm in mkt:
  for mf in mins:
    for dg in [.05,.10,.15,.20]:
      for ag in [.5,1.0,1.5]:
        add('STRIKING DEFENSE',f'mkt>={pm:.2f}, prior>={mf}, sigDefGap>={dg:.2f}, absorbedGap>={ag:.1f}/min',(t.market_prob>=pm)&(t.f_fights>=mf)&(t.o_fights>=mf)&((t.f_sig_def-t.o_sig_def)>=dg)&((t.o_sig_abs_pm-t.f_sig_abs_pm)>=ag))
    for lg in [.5,1.0,1.5]:
      for dg in [.05,.10,.15]:
        add('PACE + DEFENSE',f'mkt>={pm:.2f}, prior>={mf}, landedGap>={lg:.1f}/min, sigDefGap>={dg:.2f}',(t.market_prob>=pm)&(t.f_fights>=mf)&(t.o_fights>=mf)&((t.f_sig_l_pm-t.o_sig_l_pm)>=lg)&((t.f_sig_def-t.o_sig_def)>=dg))
progress(50,'Scanning knockdown/power categories')
for pm in mkt:
  for mf in mins:
    for fk in [.5,.75,1.0,1.25]:
      for oa in [.5,.75,1.0]:
        add('POWER / KNOCKDOWN',f'mkt>={pm:.2f}, prior>={mf}, favKD>={fk:.2f}/15, oppKDabsorbed>={oa:.2f}/15',(t.market_prob>=pm)&(t.f_fights>=mf)&(t.o_fights>=mf)&(t.f_kd15>=fk)&(t.o_kd_abs15>=oa))
progress(60,'Scanning submission and control categories')
for pm in mkt:
  for mf in mins:
    for sub in [.5,1.0,1.5]:
      for tddef in [.60,.70,.80]:
        for ca in [1.0,2.0]:
          add('SUBMISSION PRESSURE',f'mkt>={pm:.2f}, prior>={mf}, sub>={sub:.1f}/15, oppTDdef<={tddef:.2f}, oppCtrlAllowed>={ca:.1f}m/15',(t.market_prob>=pm)&(t.f_fights>=mf)&(t.o_fights>=mf)&(t.f_sub15>=sub)&(t.o_td_def<=tddef)&(t.o_ctrl_allowed15>=ca))
    for fc in [2.0,3.0,4.0]:
      for oa in [1.5,2.5,3.5]:
        add('CONTROL DOMINANCE',f'mkt>={pm:.2f}, prior>={mf}, favCtrl>={fc:.1f}m/15, oppCtrlAllowed>={oa:.1f}m/15',(t.market_prob>=pm)&(t.f_fights>=mf)&(t.o_fights>=mf)&(t.f_ctrl15>=fc)&(t.o_ctrl_allowed15>=oa))
progress(70,'Scanning experience/form categories')
for pm in mkt:
  for eg in [3,5,8,10]:
    for rg in [.34,.50,.67]:
      add('EXPERIENCE + FORM',f'mkt>={pm:.2f}, UFCfightGap>={eg}, last3Gap>={rg:.2f}',(t.market_prob>=pm)&((t.f_fights-t.o_fights)>=eg)&((t.f_last3-t.o_last3)>=rg)&(t.f_fights>=3)&(t.o_fights>=2))
progress(78,'Scanning activity/layoff categories')
for pm in [.50,.55,.60,.65,.70]:
  for fl in [180,270,365]:
    for ol in [365,540,730]:
      add('ACTIVITY / LAYOFF',f'mkt>={pm:.2f}, favLayoff<={fl}d, oppLayoff>={ol}d, prior>=2',(t.market_prob>=pm)&(t.f_fights>=2)&(t.o_fights>=2)&(t.f_layoff<=fl)&(t.o_layoff>=ol))
progress(84,'Scanning finisher-vulnerability categories')
for pm in mkt:
  for fw in [.50,.67,.75]:
    for ol in [.50,.67,.75]:
      add('FINISHER VS VULNERABLE',f'mkt>={pm:.2f}, favFinishWinPct>={fw:.2f}, oppFinishLossPct>={ol:.2f}',(t.market_prob>=pm)&(t.f_wins>=2)&(t.o_losses>=2)&(t.f_finish_win_pct>=fw)&(t.o_finish_loss_pct>=ol))
progress(89,'Scanning multi-skill superiority categories')
for pm in mkt:
  for mf in mins:
    for sg in [.75,1.0,1.25]:
      for tdg in [.05,.10,.15]:
        add('STRIKING + TD DEFENSE',f'mkt>={pm:.2f}, prior>={mf}, sigDiffGap>={sg:.2f}/min, TDdefGap>={tdg:.2f}',(t.market_prob>=pm)&(t.f_fights>=mf)&(t.o_fights>=mf)&((t.f_sig_diff_pm-t.o_sig_diff_pm)>=sg)&((t.f_td_def-t.o_td_def)>=tdg))

r=pd.DataFrame(results)
r.to_csv(outdir/'all_discovery_rules.csv',index=False)
# Conservative discovery filter: >9% overall, positive both eras, adequate sample in both eras.
q=r[(r.n>=45)&(r.pre_n>=18)&(r.recent_n>=18)&(r.roi>=.09)&(r.pre_roi>0)&(r.recent_roi>0)].copy()
q=q.sort_values(['roi','n'],ascending=[False,False])
q.to_csv(outdir/'candidates_roi_9plus.csv',index=False)
ind=q[q.existing_any_overlap<=.60].copy()
ind=ind.sort_values(['roi','n'],ascending=[False,False])
ind.to_csv(outdir/'lower_overlap_candidates.csv',index=False)
progress(95,f'Found {len(q):,} rule variants >=9% ROI with both eras positive; {len(ind):,} have <=60% overlap with existing methods')

print('\nTOP NEW-CATEGORY CANDIDATES (>=9% ROI, BOTH ERAS POSITIVE)')
print('='*145)
if q.empty:
    print('No candidate cleared the conservative filter.')
else:
    for _,z in q.head(50).iterrows():
        print(f"{z.family:<24} | n={int(z.n):3d} {int(z.wins)}-{int(z.n-z.wins)} win={z.win_rate*100:5.1f}% ROI={z.roi*100:+6.2f}% | pre={z.pre_roi*100:+6.2f}% ({int(z.pre_n)}) recent={z.recent_roi*100:+6.2f}% ({int(z.recent_n)}) | existing overlap={z.existing_any_overlap*100:5.1f}% | {z.rule}")
print('\nMOST INDEPENDENT CANDIDATES (<=60% OVERLAP WITH OUR EXISTING METHODS)')
print('='*145)
if ind.empty:
    print('None cleared this independence threshold.')
else:
    for _,z in ind.head(35).iterrows():
        print(f"{z.family:<24} | n={int(z.n):3d} win={z.win_rate*100:5.1f}% ROI={z.roi*100:+6.2f}% | pre={z.pre_roi*100:+6.2f}% recent={z.recent_roi*100:+6.2f}% | overlap={z.existing_any_overlap*100:5.1f}% | {z.rule}")
print('\nBEST BY FAMILY')
print('='*145)
if not q.empty:
    for fam,g in q.groupby('family'):
        z=g.sort_values(['roi','n'],ascending=[False,False]).iloc[0]
        print(f"{fam:<24} | n={int(z.n):3d} ROI={z.roi*100:+6.2f}% | pre={z.pre_roi*100:+6.2f}% recent={z.recent_roi*100:+6.2f}% | overlap={z.existing_any_overlap*100:5.1f}% | {z.rule}")
progress(100,'Discovery scan complete')
print('Saved:',outdir/'candidates_roi_9plus.csv')
print('Saved:',outdir/'lower_overlap_candidates.csv')
PY
