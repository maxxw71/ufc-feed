#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime, timezone
import json, re
import numpy as np
import pandas as pd

ROOT=Path.home()/"ufc-predictor-v1"
SRC=ROOT/"feature_expansion"/"prefight_favorite_features_v5.csv"
RAW=ROOT/"raw"/"competitions.csv"
OUT=ROOT/"feature_expansion"/"prefight_favorite_features_v6.csv"
META=ROOT/"feature_expansion"/"career_context_meta.json"

def norm(s): return re.sub(r'[^a-z0-9]+',' ',str(s or '').lower()).strip()
def clean_wc(s):
    x=str(s or '').lower().replace(' bout','').strip()
    x=x.replace('interim ','').replace('title ','')
    return x

def build_history(raw):
    h={}
    for _,r in raw.iterrows():
        dt=r['_date']; wc=clean_wc(r.get('weightclass','')); method=str(r.get('method','')).lower(); res=str(r.get('result','')).upper()
        p1,p2=norm(r.get('player1')),norm(r.get('player2'))
        for fighter,won in ((p1,res.startswith('W')),(p2,res.startswith('L'))):
            if not fighter: continue
            rec={
              'date':dt,'weightclass':wc,
              'decision':int('decision' in method),
              'split':int('decision - split' in method or 'split decision' in method),
              'majority':int('decision - majority' in method or 'majority decision' in method),
              'decision_win':int(('decision' in method) and won),
              'split_win':int(('split' in method) and won),
              'split_loss':int(('split' in method) and (not won) and (res.startswith('W') or res.startswith('L')))
            }
            h.setdefault(fighter,[]).append(rec)
    for k in h: h[k]=sorted(h[k],key=lambda x:x['date'])
    return h

def snap(hist,dt,current_wc):
    q=[x for x in (hist or []) if pd.notna(x['date']) and x['date']<dt]
    if not q:
        return {'prior_divisions':0,'division_changes':0,'changed_division':0,'days_since_division_change':np.nan,'returning_to_old_division':0,
                'decision_fights':0,'split_decisions':0,'majority_decisions':0,'decision_win_pct':np.nan,'split_win_pct':np.nan,'split_loss_count':0,
                'split_last5':0,'decision_last5':0}
    wcs=[x['weightclass'] for x in q if x['weightclass']]
    uniq=[]
    for w in wcs:
        if w not in uniq: uniq.append(w)
    changes=sum(1 for a,b in zip(wcs,wcs[1:]) if a!=b)
    last_wc=wcs[-1] if wcs else ''
    changed=int(bool(current_wc and last_wc and current_wc!=last_wc))
    last_change_date=None
    if len(wcs)>=2:
        # map sequence back to dated records and locate most recent actual class transition
        prev=None
        for x in q:
            w=x['weightclass']
            if not w: continue
            if prev is not None and w!=prev: last_change_date=x['date']
            prev=w
    days=(dt-last_change_date).days if last_change_date is not None else np.nan
    decisions=[x for x in q if x['decision']]
    splits=[x for x in q if x['split']]
    recent=q[-5:]
    return {
      'prior_divisions':len(set(wcs)),
      'division_changes':changes,
      'changed_division':changed,
      'days_since_division_change':days,
      'returning_to_old_division':int(changed and current_wc in set(wcs[:-1])),
      'decision_fights':len(decisions),
      'split_decisions':len(splits),
      'majority_decisions':sum(x['majority'] for x in q),
      'decision_win_pct':(sum(x['decision_win'] for x in decisions)/len(decisions)) if decisions else np.nan,
      'split_win_pct':(sum(x['split_win'] for x in splits)/len(splits)) if splits else np.nan,
      'split_loss_count':sum(x['split_loss'] for x in q),
      'split_last5':sum(x['split'] for x in recent),
      'decision_last5':sum(x['decision'] for x in recent)
    }

def main():
    base=pd.read_csv(SRC,low_memory=False)
    raw=pd.read_csv(RAW,low_memory=False)
    raw['_date']=pd.to_datetime(raw['event_date'],errors='coerce')
    raw=raw[raw['_date'].notna()].sort_values('_date').reset_index(drop=True)
    hist=build_history(raw)
    rows=[]
    for _,r in base.iterrows():
        dt=pd.to_datetime(r['event_date'],errors='coerce'); fn,on=norm(r['favorite']),norm(r['opponent']); wc=clean_wc(r.get('weightclass',''))
        fs=snap(hist.get(fn),dt,wc); os=snap(hist.get(on),dt,wc); out={}
        for k in sorted(set(fs)|set(os)):
            out['f6_f_'+k]=fs.get(k,np.nan); out['f6_o_'+k]=os.get(k,np.nan)
            a,b=fs.get(k,np.nan),os.get(k,np.nan)
            if isinstance(a,(int,float,np.integer,np.floating)) and isinstance(b,(int,float,np.integer,np.floating)) and pd.notna(a) and pd.notna(b): out['f6_diff_'+k]=float(a)-float(b)
        rows.append(out)
    outdf=pd.concat([base,pd.DataFrame(rows)],axis=1)
    outdf.to_csv(OUT,index=False)
    meta={'built_at':datetime.now(timezone.utc).isoformat(),'rows':len(outdf),'columns':len(outdf.columns),'added_columns':len(outdf.columns)-len(base.columns),
          'strict_before_fight_date':True,'same_event_results_excluded':True,'output':str(OUT)}
    META.write_text(json.dumps(meta,indent=2),encoding='utf-8')
    print(json.dumps(meta,indent=2)); print([c for c in outdf if c.startswith('f6_')])

if __name__=='__main__': main()
