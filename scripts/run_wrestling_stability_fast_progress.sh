#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$HOME/ufc-predictor-v1"
IN="$ROOT/ufc_reach_method_analysis/reach_market_sample.csv"
OUTDIR="$ROOT/wrestling_stability_fast"
CACHE="$ROOT/wrestling_stability_positive_roi/all_favorite_sides_with_prefight_wrestling.csv"
mkdir -p "$OUTDIR"

[ -s "$IN" ] || { echo "ERROR: Missing $IN"; exit 2; }
source "$ROOT/venv/bin/activate"

python -u - "$IN" "$CACHE" "$OUTDIR" <<'PY'
import sys,re,time
from pathlib import Path
import numpy as np
import pandas as pd

inp=Path(sys.argv[1]); cache=Path(sys.argv[2]); outdir=Path(sys.argv[3])

def progress(p,msg):
    print(f"[{p:3d}%] {msg}", flush=True)

progress(2,"Starting wrestling stability analysis...")

if cache.exists() and cache.stat().st_size>0:
    t=pd.read_csv(cache,low_memory=False)
    t['event_date']=pd.to_datetime(t['event_date'],errors='coerce')
    progress(20,f"Reused cached pre-fight wrestling table: {len(t):,} favorite-side rows")
else:
    progress(5,"Building pre-fight wrestling profiles from 2006-2026 history...")
    df=pd.read_csv(inp,low_memory=False)
    df['event_date']=pd.to_datetime(df['event_date'],errors='coerce').dt.normalize()
    df=df.dropna(subset=['event_date','player1','player2']).sort_values('event_date').reset_index(drop=True)

    def b(v):
        if isinstance(v,bool): return v
        return str(v).strip().lower() in {'true','1','yes','w','win'}
    def parse_of(v):
        if pd.isna(v): return 0.0,0.0
        m=re.search(r'(\d+)\s+of\s+(\d+)',str(v),re.I)
        return (float(m.group(1)),float(m.group(2))) if m else (0.0,0.0)
    def parse_num(v):
        if pd.isna(v): return 0.0
        m=re.search(r'-?\d+(?:\.\d+)?',str(v))
        return float(m.group()) if m else 0.0
    def parse_ctrl(v):
        if pd.isna(v): return 0.0
        m=re.match(r'\s*(\d+):(\d+)\s*$',str(v))
        return 60*int(m.group(1))+int(m.group(2)) if m else 0.0
    def fight_seconds(r):
        rnd=max(1,int(parse_num(r.get('round',1)) or 1))
        m=re.match(r'(\d+):(\d+)',str(r.get('time','0:00')))
        sec=int(m.group(1))*60+int(m.group(2)) if m else 0
        return max(1,(rnd-1)*300+sec)

    records=[]
    total=len(df)
    marks={max(1,int(total*x/5)):x for x in range(1,6)}
    for i,(_,r) in enumerate(df.iterrows(),1):
        dur=fight_seconds(r); mins=dur/60.0; p1won=b(r.get('p1_won',False))
        for side in (1,2):
            opp=2 if side==1 else 1
            td_l=td_a=opp_td_l=opp_td_a=0.0; sub=rev=ctrl=opp_ctrl=ground_l=0.0
            for rd in range(1,6):
                a,c=parse_of(r.get(f'p{side}_rd{rd}_Td')); td_l+=a; td_a+=c
                a,c=parse_of(r.get(f'p{opp}_rd{rd}_Td')); opp_td_l+=a; opp_td_a+=c
                sub+=parse_num(r.get(f'p{side}_rd{rd}_Sub_att'))
                rev+=parse_num(r.get(f'p{side}_rd{rd}_Rev'))
                ctrl+=parse_ctrl(r.get(f'p{side}_rd{rd}_Ctrl'))
                opp_ctrl+=parse_ctrl(r.get(f'p{opp}_rd{rd}_Ctrl'))
                a,_=parse_of(r.get(f'p{side}_rd{rd}_Ground')); ground_l+=a
            records.append({'date':r.event_date,'fighter_url':r[f'player{side}_url'],
                'won':p1won if side==1 else (not p1won),'mins':mins,
                'td_l':td_l,'td_a':td_a,'td_allowed':opp_td_l,'td_faced':opp_td_a,
                'sub':sub,'rev':rev,'ctrl_min':ctrl/60.0,'ctrl_allowed_min':opp_ctrl/60.0,'ground_l':ground_l})
        if i in marks:
            progress(5+marks[i]*3,f"Parsed fight history {i:,}/{total:,}")

    hist=pd.DataFrame(records).sort_values(['fighter_url','date']).reset_index(drop=True)
    groups={k:g.copy() for k,g in hist.groupby('fighter_url')}
    def profile(url,date):
        g=groups.get(url)
        if g is None:return None
        g=g[g.date<date]
        if g.empty:return None
        mins=float(g.mins.sum())
        if mins<=0:return None
        sc=15.0/mins; td_l=float(g.td_l.sum()); td_a=float(g.td_a.sum()); td_allowed=float(g.td_allowed.sum()); td_faced=float(g.td_faced.sum())
        return {'fights':len(g),'td_l15':td_l*sc,'td_a15':td_a*sc,'td_acc':td_l/td_a if td_a>0 else np.nan,
                'td_def':1-td_allowed/td_faced if td_faced>0 else np.nan,'sub15':float(g['sub'].sum())*sc,
                'rev15':float(g.rev.sum())*sc,'ctrl15':float(g.ctrl_min.sum())*sc,'ctrl_allowed15':float(g.ctrl_allowed_min.sum())*sc,
                'ground_l15':float(g.ground_l.sum())*sc}

    rows=[]; total=len(df)
    for i,(_,r) in enumerate(df.iterrows(),1):
        p1=profile(r.player1_url,r.event_date); p2=profile(r.player2_url,r.event_date)
        if p1 is not None and p2 is not None:
            fav_is_p1=b(r.get('market_fav_is_p1',False)); fav_prob=pd.to_numeric(pd.Series([r.get('market_prob')]),errors='coerce').iloc[0]; fav_dec=pd.to_numeric(pd.Series([r.get('fav_decimal')]),errors='coerce').iloc[0]
            if pd.notna(fav_prob) and pd.notna(fav_dec):
                p1won=b(r.get('p1_won',False))
                for side,(me,opp) in enumerate(((p1,p2),(p2,p1)),start=1):
                    is_fav=(side==1 and fav_is_p1) or (side==2 and not fav_is_p1)
                    if not is_fav: continue
                    won=p1won if side==1 else (not p1won)
                    rows.append({'event_date':r.event_date,'fighter':r[f'player{side}'],'opponent':r[f'player{2 if side==1 else 1}'],
                                 'won':won,'market_prob':float(fav_prob),'fav_decimal':float(fav_dec),
                                 **{f'w_{k}':v for k,v in me.items()},**{f'o_{k}':v for k,v in opp.items()}})
        if i % max(1,total//4)==0:
            progress(22+int(12*i/total),f"Built pre-fight profiles {i:,}/{total:,}")
    t=pd.DataFrame(rows)
    t['profit100']=np.where(t.won,100*(t.fav_decimal-1),-100.0)
    cache.parent.mkdir(parents=True,exist_ok=True); t.to_csv(cache,index=False)
    progress(35,f"Cached {len(t):,} favorite-side rows")

for c in ['event_date']:
    t[c]=pd.to_datetime(t[c],errors='coerce')
if 'profit100' not in t.columns:
    t['profit100']=np.where(t.won.astype(bool),100*(pd.to_numeric(t.fav_decimal,errors='coerce')-1),-100.0)

# numpy arrays for fast threshold scan
A={c:pd.to_numeric(t[c],errors='coerce').to_numpy() for c in ['w_fights','o_fights','w_td_l15','w_td_a15','w_ctrl15','o_td_a15','o_ctrl15','market_prob','profit100']}
won=t['won'].astype(bool).to_numpy(); dates=t['event_date'].to_numpy(dtype='datetime64[D]'); split=np.datetime64('2020-01-01')

w_td_l=[1.0,1.25,1.5,1.75,2.0,2.25,2.5]
w_td_a=[3.0,4.0,5.0,6.0]; w_ctrl=[1.5,2.0,2.5,3.0,3.5,4.0]
o_td_a=[0.5,1.0,1.5,2.0,2.5]; o_ctrl=[0.5,0.75,1.0,1.5,2.0]; min_fights=[2,3,4]; prob_mins=[0.50,0.55,0.60,0.65,0.70,0.75,0.80]

res=[]; total_outer=len(w_td_l)
for oi,tl in enumerate(w_td_l,1):
  m_tl=A['w_td_l15']>=tl
  for ta in w_td_a:
    m_ta=m_tl&(A['w_td_a15']>=ta)
    for wc in w_ctrl:
      m_wc=m_ta&(A['w_ctrl15']>=wc)
      for ota in o_td_a:
        m_ota=m_wc&(A['o_td_a15']<=ota)
        for oc in o_ctrl:
          m_oc=m_ota&(A['o_ctrl15']<=oc)
          for mf in min_fights:
            base=m_oc&(A['w_fights']>=mf)&(A['o_fights']>=mf)
            if base.sum()<30: continue
            for pm in prob_mins:
              m=base&(A['market_prob']>=pm); n=int(m.sum())
              if n<1: continue
              p=float(A['profit100'][m].sum()); roi=p/(100*n)
              old=m&(dates<split); rec=m&(dates>=split); on=int(old.sum()); rn=int(rec.sum())
              pre=float(A['profit100'][old].sum()/(100*on)) if on else np.nan
              recent=float(A['profit100'][rec].sum()/(100*rn)) if rn else np.nan
              if roi<=0 or not np.isfinite(pre) or not np.isfinite(recent) or pre<=0 or recent<=0: continue
              res.append({'w_td_l15_min':tl,'w_td_a15_min':ta,'w_ctrl15_min':wc,'opp_td_a15_max':ota,'opp_ctrl15_max':oc,'min_prior_fights':mf,'favorite_prob_min':pm,
                          'n':n,'wins':int(won[m].sum()),'win_rate':float(won[m].mean()),'roi':roi,'pre_n':on,'pre_roi':pre,'recent_n':rn,'recent_roi':recent})
  progress(35+int(50*oi/total_outer),f"Threshold scan {oi}/{total_outer} complete")

r=pd.DataFrame(res)
if r.empty:
    progress(100,"Complete — no positive-ROI rules survived both eras.")
    raise SystemExit(0)

r=r[(r.n>=50)&(r.pre_n>=20)&(r.recent_n>=20)].sort_values(['roi','n'],ascending=[False,False])
r.to_csv(outdir/'positive_roi_candidates.csv',index=False)
progress(90,f"Positive both-era candidates retained: {len(r):,}")

print('\nTOP POSITIVE WRESTLING MISMATCH RULES')
print('='*120)
for _,x in r.head(30).iterrows():
    print(f"fav>={x.favorite_prob_min*100:4.0f}% | TDland>={x.w_td_l15_min:.2f}/15 | TDatt>={x.w_td_a15_min:.1f}/15 | ctrl>={x.w_ctrl15_min:.2f}m/15 | oppTDatt<={x.opp_td_a15_max:.2f}/15 | oppctrl<={x.opp_ctrl15_max:.2f}m/15 | prior>={int(x.min_prior_fights)} | n={int(x.n):3d} win={x.win_rate*100:5.1f}% ROI={x.roi*100:+6.2f}% | pre={x.pre_roi*100:+6.2f}% recent={x.recent_roi*100:+6.2f}%")

progress(100,"Complete.")
print('Saved:',outdir/'positive_roi_candidates.csv')
PY
