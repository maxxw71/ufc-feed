#!/usr/bin/env python3
from __future__ import annotations
import json, math, re, sqlite3, hashlib
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT=Path.home()/"ufc-predictor-v1"
RAW=ROOT/"raw"/"competitions.csv"
BASE=ROOT/"new_category_discovery"/"prefight_favorite_features.csv"
OUTDIR=ROOT/"feature_expansion"
OUT=OUTDIR/"prefight_favorite_features_v2.csv"
META=OUTDIR/"feature_expansion_meta.json"
OUTDIR.mkdir(parents=True,exist_ok=True)


def norm(s):
    return re.sub(r'[^a-z0-9]+',' ',str(s or '').lower()).strip()

def parse_date(s):
    return pd.to_datetime(s,errors='coerce')

def of(v):
    if pd.isna(v): return (0.0,0.0)
    m=re.search(r'([0-9.]+)\s+of\s+([0-9.]+)',str(v),re.I)
    if m:return float(m.group(1)),float(m.group(2))
    try:return float(v),0.0
    except:return 0.0,0.0

def ctrl(v):
    if pd.isna(v):return 0.0
    s=str(v).strip()
    if ':' in s:
        try:
            a,b=s.split(':',1);return float(a)+float(b)/60
        except:return 0.0
    try:return float(s)
    except:return 0.0

def fight_minutes(row):
    try:
        rnd=int(float(row.get('round',0) or 0)); t=str(row.get('time','0:00'))
        mm,ss=t.split(':'); return max(0.1,(rnd-1)*5+int(mm)+int(ss)/60)
    except:return 15.0

def rd_metric(row,side,r,stem,attempt=False):
    v=row.get(f'{side}_rd{r}_{stem}')
    if stem in ('Sig_str','Total_str','Td','Head','Body','Leg','Distance','Clinch','Ground'):
        a,b=of(v); return b if attempt else a
    if stem=='Ctrl': return ctrl(v)
    try:return float(v) if not pd.isna(v) else 0.0
    except:return 0.0

def is_ko(method):
    s=str(method or '').lower(); return ('ko/tko' in s) or ('tko' in s) or s.startswith('ko')

def is_sub(method): return 'submission' in str(method or '').lower()

def is_dec(method): return 'decision' in str(method or '').lower()

def is_five_round(row):
    tf=str(row.get('time_format','')).lower()
    return '5 rnd' in tf or any(not pd.isna(row.get(f'p1_rd{r}_Sig_str')) for r in (4,5))

class State:
    def __init__(self):
        self.elo=1500.0; self.fights=0; self.wins=0; self.losses=0
        self.opp_elo=[]; self.opp_winpct=[]
        self.ko_losses=0; self.sub_losses=0; self.decision_losses=0
        self.kd_abs=0.0; self.sig_abs=0.0; self.head_abs=0.0; self.mins=0.0
        self.five_round_fights=0
        self.hist=deque(maxlen=8)
        self.last_ko_loss_date=None
    def snap(self, dt):
        recent=list(self.hist)
        last3=recent[-3:]; last5=recent[-5:]
        def avg(items,key):
            vals=[x.get(key,np.nan) for x in items]; vals=[v for v in vals if pd.notna(v)]
            return float(np.mean(vals)) if vals else np.nan
        def cnt(items,key): return int(sum(bool(x.get(key,False)) for x in items))
        days_ko=np.nan
        if self.last_ko_loss_date is not None and pd.notna(dt):
            days_ko=max(0,(dt-self.last_ko_loss_date).days)
        sos=np.mean(self.opp_elo) if self.opp_elo else np.nan
        oppwp=np.mean(self.opp_winpct) if self.opp_winpct else np.nan
        # trajectory: recent minus longer-career per-minute performance
        career_sigdiff=avg(recent,'sig_diff_pm')
        recent_sigdiff=avg(last3,'sig_diff_pm')
        career_ctrl=avg(recent,'ctrl_diff15')
        recent_ctrl=avg(last3,'ctrl_diff15')
        return {
          'elo':self.elo,'sos_opp_elo':sos,'sos_opp_winpct':oppwp,
          'ko_losses':self.ko_losses,'sub_losses':self.sub_losses,'decision_losses':self.decision_losses,
          'ko_loss_last3':cnt(last3,'ko_loss'),'ko_loss_last5':cnt(last5,'ko_loss'),
          'kd_abs_career':self.kd_abs,'kd_abs15_career':15*self.kd_abs/self.mins if self.mins else np.nan,
          'sig_abs_career':self.sig_abs,'sig_abs_pm_career':self.sig_abs/self.mins if self.mins else np.nan,
          'head_abs_pm_career':self.head_abs/self.mins if self.mins else np.nan,
          'days_since_ko_loss':days_ko,'five_round_fights':self.five_round_fights,
          'recent3_sig_diff_pm':recent_sigdiff,'recent5_sig_diff_pm':avg(last5,'sig_diff_pm'),
          'recent3_ctrl_diff15':recent_ctrl,'recent5_ctrl_diff15':avg(last5,'ctrl_diff15'),
          'recent3_kd_abs15':avg(last3,'kd_abs15'),'recent5_kd_abs15':avg(last5,'kd_abs15'),
          'recent3_sig_abs_pm':avg(last3,'sig_abs_pm'),'recent5_sig_abs_pm':avg(last5,'sig_abs_pm'),
          'sig_diff_trend3': recent_sigdiff-career_sigdiff if pd.notna(recent_sigdiff) and pd.notna(career_sigdiff) else np.nan,
          'ctrl_diff_trend3': recent_ctrl-career_ctrl if pd.notna(recent_ctrl) and pd.notna(career_ctrl) else np.nan,
          'r1_sig_diff_pm':avg(recent,'r1_sig_diff_pm'),'r3_sig_diff_pm':avg(recent,'r3_sig_diff_pm'),
          'cardio_sig_decay': (avg(recent,'r3_sig_diff_pm')-avg(recent,'r1_sig_diff_pm')) if pd.notna(avg(recent,'r3_sig_diff_pm')) and pd.notna(avg(recent,'r1_sig_diff_pm')) else np.nan,
          'r1_ctrl_diff':avg(recent,'r1_ctrl_diff'),'r3_ctrl_diff':avg(recent,'r3_ctrl_diff'),
          'cardio_ctrl_decay': (avg(recent,'r3_ctrl_diff')-avg(recent,'r1_ctrl_diff')) if pd.notna(avg(recent,'r3_ctrl_diff')) and pd.notna(avg(recent,'r1_ctrl_diff')) else np.nan,
          'style_strike_volume':avg(recent,'sig_att_pm'),'style_wrestle_attempts':avg(recent,'td_att15'),
          'style_control':avg(recent,'ctrl15'),'style_power':avg(recent,'kd15'),
          'style_submission':avg(recent,'sub15'),'style_ground_share':avg(recent,'ground_share'),
        }

def side_summary(row,side,opp):
    mins=fight_minutes(row)
    sig=sum(rd_metric(row,side,r,'Sig_str') for r in range(1,6)); sigatt=sum(rd_metric(row,side,r,'Sig_str',True) for r in range(1,6))
    osig=sum(rd_metric(row,opp,r,'Sig_str') for r in range(1,6))
    kd=sum(rd_metric(row,side,r,'KD') for r in range(1,6)); okd=sum(rd_metric(row,opp,r,'KD') for r in range(1,6))
    tdatt=sum(rd_metric(row,side,r,'Td',True) for r in range(1,6))
    c=sum(rd_metric(row,side,r,'Ctrl') for r in range(1,6)); oc=sum(rd_metric(row,opp,r,'Ctrl') for r in range(1,6))
    sub=sum(rd_metric(row,side,r,'Sub_att') for r in range(1,6))
    ground=sum(rd_metric(row,side,r,'Ground') for r in range(1,6)); total_sig=max(sig,1)
    head_abs=sum(rd_metric(row,opp,r,'Head') for r in range(1,6))
    def round_sigdiff(r):
        a=rd_metric(row,side,r,'Sig_str'); b=rd_metric(row,opp,r,'Sig_str'); return (a-b)/5
    return {'mins':mins,'sig':sig,'sigatt':sigatt,'sigabs':osig,'kd':kd,'kdabs':okd,'tdatt':tdatt,'ctrl':c,'ctrlopp':oc,'sub':sub,'headabs':head_abs,
      'sig_diff_pm':(sig-osig)/mins,'sig_abs_pm':osig/mins,'sig_att_pm':sigatt/mins,'kd_abs15':15*okd/mins,'kd15':15*kd/mins,'td_att15':15*tdatt/mins,
      'ctrl15':15*c/mins,'ctrl_diff15':15*(c-oc)/mins,'sub15':15*sub/mins,'ground_share':ground/total_sig,
      'r1_sig_diff_pm':round_sigdiff(1),'r3_sig_diff_pm':round_sigdiff(3) if not pd.isna(row.get(f'{side}_rd3_Sig_str')) else np.nan,
      'r1_ctrl_diff':rd_metric(row,side,1,'Ctrl')-rd_metric(row,opp,1,'Ctrl'),
      'r3_ctrl_diff':(rd_metric(row,side,3,'Ctrl')-rd_metric(row,opp,3,'Ctrl')) if not pd.isna(row.get(f'{side}_rd3_Ctrl')) else np.nan}

def main():
    raw=pd.read_csv(RAW,low_memory=False)
    raw['_date']=pd.to_datetime(raw['event_date'],errors='coerce')
    raw=raw[raw['_date'].notna()].sort_values(['_date','event_url','player1','player2']).reset_index(drop=True)
    states=defaultdict(State); snaps={}; div_history=defaultdict(list)
    rows=[]
    for _,r in raw.iterrows():
        dt=r['_date']; n1,n2=norm(r['player1']),norm(r['player2']); s1,s2=states[n1],states[n2]
        p1=s1.snap(dt); p2=s2.snap(dt)
        key=(dt.date().isoformat(),)+tuple(sorted((n1,n2)))
        snaps[key]={'p1name':n1,'p2name':n2,'p1':p1,'p2':p2,'weightclass':str(r.get('weightclass',''))}
        sm1=side_summary(r,'p1','p2'); sm2=side_summary(r,'p2','p1')
        res=str(r.get('result','')).strip().upper(); p1win=res.startswith('W'); p2win=res.startswith('L')
        if not (p1win or p2win):
            # Most UFCStats rows orient player1 as winner; draws/NC do not update W/L or Elo.
            outcome=None
        else: outcome=1.0 if p1win else 0.0
        wp1=s1.wins/s1.fights if s1.fights else 0.5; wp2=s2.wins/s2.fights if s2.fights else 0.5
        s1.opp_elo.append(s2.elo); s2.opp_elo.append(s1.elo); s1.opp_winpct.append(wp2); s2.opp_winpct.append(wp1)
        if outcome is not None:
            e1=1/(1+10**((s2.elo-s1.elo)/400)); k=24
            d=k*(outcome-e1); s1.elo+=d; s2.elo-=d
            if outcome==1: s1.wins+=1;s2.losses+=1
            else:s2.wins+=1;s1.losses+=1
        method=r.get('method','')
        p1loss=(outcome==0); p2loss=(outcome==1)
        for st,sm,loss in ((s1,sm1,p1loss),(s2,sm2,p2loss)):
            st.fights+=1; st.kd_abs+=sm['kdabs']; st.sig_abs+=sm['sigabs']; st.head_abs+=sm['headabs']; st.mins+=sm['mins']
            if is_five_round(r): st.five_round_fights+=1
            if loss and is_ko(method): st.ko_losses+=1; st.last_ko_loss_date=dt
            elif loss and is_sub(method): st.sub_losses+=1
            elif loss and is_dec(method): st.decision_losses+=1
            h=dict(sm); h['ko_loss']=bool(loss and is_ko(method)); st.hist.append(h)
    base=pd.read_csv(BASE,low_memory=False)
    base['_date']=pd.to_datetime(base['event_date'],errors='coerce')
    extras=[]; matched=0
    for _,r in base.iterrows():
        dt=r['_date']; fn,on=norm(r['favorite']),norm(r['opponent'])
        key=(dt.date().isoformat(),)+tuple(sorted((fn,on))) if pd.notna(dt) else None
        z=snaps.get(key)
        out={}
        if z:
            matched+=1
            if z['p1name']==fn: fs,os_=z['p1'],z['p2']
            else: fs,os_=z['p2'],z['p1']
            out['weightclass']=z['weightclass']
            names=set(fs)|set(os_)
            for k in names:
                out['f2_f_'+k]=fs.get(k,np.nan); out['f2_o_'+k]=os_.get(k,np.nan)
                a,b=fs.get(k,np.nan),os_.get(k,np.nan)
                if isinstance(a,(int,float,np.floating)) and isinstance(b,(int,float,np.floating)) and pd.notna(a) and pd.notna(b): out['f2_diff_'+k]=float(a)-float(b)
        extras.append(out)
    ex=pd.DataFrame(extras)
    df=pd.concat([base.drop(columns=['_date']),ex],axis=1)
    # Division-era normalization of stable prefight metrics. Uses only values already point-in-time; group stats are descriptive normalization, not outcomes.
    years=pd.to_datetime(df['event_date'],errors='coerce').dt.year
    df['f2_era5']=(years//5)*5
    normcols=['f_sig_diff_pm','f_td_def','f_ctrl15','f_ctrl_allowed15','f_kd15','f_kd_abs15','f_finish_win_pct','f_finish_loss_pct']
    for c in normcols:
        if c not in df: continue
        grp=df.groupby(['weightclass','f2_era5'])[c]
        mu=grp.transform('mean'); sd=grp.transform('std').replace(0,np.nan)
        df['f2_norm_'+c]=(pd.to_numeric(df[c],errors='coerce')-mu)/sd
    df.to_csv(OUT,index=False)
    meta={'built_at':datetime.utcnow().isoformat()+'Z','source_rows':len(raw),'base_rows':len(base),'matched_rows':matched,'match_rate':matched/len(base) if len(base) else 0,'columns':len(df.columns),'added_columns':len(df.columns)-len(base.columns)+1,'output':str(OUT)}
    META.write_text(json.dumps(meta,indent=2),encoding='utf-8')
    print(json.dumps(meta,indent=2))
    print('new_feature_prefix_count',sum(c.startswith('f2_') for c in df.columns))
    print('sample_new_features',[c for c in df.columns if c.startswith('f2_')][:50])

if __name__=='__main__': main()
