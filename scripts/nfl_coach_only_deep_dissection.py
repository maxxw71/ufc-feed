from pathlib import Path
import os,json,itertools
import numpy as np
import pandas as pd

REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
CTX=Path('/home/appwiza-runner/nfl-context-data')
OUT=CTX/'coach_only_deep_dissection'; OUT.mkdir(parents=True,exist_ok=True)
DATA=CTX/'coach_quality_expansion'/'coach_quality_enriched_team_sides.parquet'
CO=REPO/'nfl/coach_only_deployment_audit/live_ready.csv'
H=REPO/'nfl/live_candidate_finalization/live_ready.csv'


def num(x): return pd.to_numeric(x,errors='coerce')
def implied(ml):
    ml=num(ml);return np.where(ml<0,(-ml)/((-ml)+100),100/(ml+100))
def profit(win,ml):
    ml=num(ml);return np.where(num(win).eq(1),np.where(ml>0,ml/100,100/np.abs(ml)),-1.0)
def met(x):
    if not len(x): return {'n':0,'wins':0,'losses':0,'win_pct':np.nan,'roi':np.nan,'units':0.0}
    return {'n':len(x),'wins':int(x.win.sum()),'losses':int(len(x)-x.win.sum()),'win_pct':float(x.win.mean()),'roi':float(x.profit.mean()),'units':float(x.profit.sum())}
def mask_cond(x,c,op,t):
    if c not in x:return pd.Series(False,index=x.index)
    v=num(x[c])
    if op=='>=':return v>=float(t)
    if op=='<=':return v<=float(t)
    if op=='>':return v>float(t)
    if op=='<':return v<float(t)
    if op=='==':return v==float(t)
    return pd.Series(False,index=x.index)
def apply_conds(x,conds):
    m=pd.Series(True,index=x.index)
    for c,op,t in conds:m &= mask_cond(x,c,op,t)
    return m

bands={'DOG20_34':(.20,.35),'DOG35_44':(.35,.45),'DOG35_49':(.35,.50),'PK_55':(.45,.55),'FAV55_65':(.55,.65),'FAV65_75':(.65,.75),'FAV75_85':(.75,.85)}
tracks={'LONG':((2006,2013),(2014,2019),(2020,2025)),'MODERN':((2012,2017),(2018,2020),(2021,2025))}

d=pd.read_parquet(DATA);d=d[d.season.between(2006,2025)].copy();d['market_prob']=implied(d.moneyline);d['win']=num(d.win);d['profit']=profit(d.win,d.moneyline);d=d[d.win.isin([0,1])&d.moneyline.notna()].copy()
co=pd.read_csv(CO);hreg=pd.read_csv(H)

# -------- reconstruct CO bet sets --------
def co_set(r):
    lo,hi=bands[r.price_band];x=d[(d.market_prob>=lo)&(d.market_prob<hi)&(num(d.prior_games)>=3)].copy()
    if r.venue=='AWAY':x=x[num(x.is_home).ne(1)]
    elif r.venue=='HOME':x=x[num(x.is_home).eq(1)]
    conds=json.loads(r.conditions);x=x[apply_conds(x,conds)].copy();return x
cosets={str(r.method_id):co_set(r) for _,r in co.iterrows()}

# -------- weekly / segment ROI by split --------
weekly=[];segments=[]
for _,r in co.iterrows():
    mid=str(r.method_id);x=cosets[mid];tr,va,ho=tracks[r.track]
    splitdefs=[('ALL',2006,2025),('TRAIN',*tr),('VALIDATION',*va),('HOLDOUT',*ho)]
    for split,a,b in splitdefs:
        z=x[x.season.between(a,b)]
        for w in range(4,19):
            m=met(z[num(z.week)==w]);weekly.append({'method_id':mid,'split':split,'week':w,**m})
        for name,w1,w2 in [('W4_6',4,6),('W7_10',7,10),('W11_14',11,14),('W15_18',15,18)]:
            m=met(z[num(z.week).between(w1,w2)]);segments.append({'method_id':mid,'split':split,'segment':name,**m})
pd.DataFrame(weekly).to_csv(OUT/'weekly_roi.csv',index=False);segdf=pd.DataFrame(segments);segdf.to_csv(OUT/'segment_roi.csv',index=False)

# -------- timing veto audit: select weak segment on train+validation, confirm holdout --------
timing=[]
for _,r in co.iterrows():
    mid=str(r.method_id);x=cosets[mid];tr,va,ho=tracks[r.track]
    train=x[x.season.between(*tr)];val=x[x.season.between(*va)];hold=x[x.season.between(*ho)]
    bt,bv,bh=met(train),met(val),met(hold)
    for name,w1,w2 in [('W4_6',4,6),('W7_10',7,10),('W11_14',11,14),('W15_18',15,18)]:
        rt=met(train[num(train.week).between(w1,w2)]);rv=met(val[num(val.week).between(w1,w2)]);rh=met(hold[num(hold.week).between(w1,w2)])
        st=met(train[~num(train.week).between(w1,w2)]);sv=met(val[~num(val.week).between(w1,w2)]);sh=met(hold[~num(hold.week).between(w1,w2)])
        if rt['n']<5 or rv['n']<4 or rh['n']<4:continue
        # segment is only a veto candidate if materially worse in both pre-holdout eras.
        train_bad=(rt['roi']<=bt['roi']-.12 and rt['win_pct']<=bt['win_pct']-.05)
        val_bad=(rv['roi']<=bv['roi']-.10 and rv['win_pct']<=bv['win_pct']-.04)
        selected=train_bad and val_bad
        confirmed=selected and rh['roi']<=bh['roi']-.05 and sh['roi']>=bh['roi']-.02
        timing.append({'method_id':mid,'segment':name,'selected_preholdout':selected,'confirmed_holdout':confirmed,
                       'base_train_roi':bt['roi'],'segment_train_n':rt['n'],'segment_train_win_pct':rt['win_pct'],'segment_train_roi':rt['roi'],'safe_train_roi':st['roi'],
                       'base_validation_roi':bv['roi'],'segment_validation_n':rv['n'],'segment_validation_win_pct':rv['win_pct'],'segment_validation_roi':rv['roi'],'safe_validation_roi':sv['roi'],
                       'base_holdout_roi':bh['roi'],'segment_holdout_n':rh['n'],'segment_holdout_win_pct':rh['win_pct'],'segment_holdout_roi':rh['roi'],'safe_holdout_roi':sh['roi']})
timdf=pd.DataFrame(timing);timdf.to_csv(OUT/'timing_veto_audit.csv',index=False)

# -------- loss-forensic veto mining --------
# Coaching vetoes keep the method philosophically coach-only. Football/context vetoes are a separate safety layer.
exclude={'season','week','win','profit','moneyline','market_prob','prior_games','game_id','team','opponent','is_home'}
coach_features=[c for c in d.columns if (c.startswith('coachq_') or c.startswith('opp_coachq_') or c.startswith('adv_') and any(k in c for k in ['hc_','oc_','dc_','staff_'])) and pd.api.types.is_numeric_dtype(d[c]) and num(d[c]).notna().sum()>=300]
context_keywords=['rank_edge_','recent_edge_','adv_travel','travel_','fatigue','rest','qb_prior_starts','returning_','unavail','injur','continuity','tz_shift','road_miles','dome_game','grass_surface','base_vs_open_prob_move']
context_features=[c for c in d.columns if c not in exclude and pd.api.types.is_numeric_dtype(d[c]) and any(k in c.lower() for k in context_keywords) and num(d[c]).notna().sum()>=500]
# cap features by availability to reduce multiplicity
context_features=sorted(context_features,key=lambda c:num(d[c]).notna().sum(),reverse=True)[:120]
coach_features=sorted(set(coach_features))[:100]

vrows=[]
for _,r in co.iterrows():
    mid=str(r.method_id);x=cosets[mid];tr,va,ho=tracks[r.track]
    train=x[x.season.between(*tr)];val=x[x.season.between(*va)];hold=x[x.season.between(*ho)]
    bt,bv,bh=met(train),met(val),met(hold)
    for family,features in [('COACH',coach_features),('CONTEXT',context_features)]:
      for c in features:
        tv=num(train[c]).dropna()
        if len(tv)<25:continue
        for q in [.20,.35,.50,.65,.80]:
          t=float(tv.quantile(q))
          for op in ['>=','<=']:
            trisk=train[mask_cond(train,c,op,t)];tsafe=train[~mask_cond(train,c,op,t)]
            vrisk=val[mask_cond(val,c,op,t)];vsafe=val[~mask_cond(val,c,op,t)]
            if len(trisk)<6 or len(vrisk)<4 or len(tsafe)<12 or len(vsafe)<10:continue
            mt,ms,mv,mvs=met(trisk),met(tsafe),met(vrisk),met(vsafe)
            # pre-holdout selection: loss-heavy AND safe-set improves/holds in both train and validation.
            tlift=(1-mt['win_pct'])-(1-bt['win_pct']);vlift=(1-mv['win_pct'])-(1-bv['win_pct'])
            if tlift<.08 or vlift<.08:continue
            if mt['roi']>bt['roi']-.08 or mv['roi']>bv['roi']-.08:continue
            if ms['roi']<bt['roi']-.02 or mvs['roi']<bv['roi']-.02:continue
            hrisk=hold[mask_cond(hold,c,op,t)];hsafe=hold[~mask_cond(hold,c,op,t)]
            if len(hrisk)<4 or len(hsafe)<8:continue
            mh,mhs=met(hrisk),met(hsafe);hlift=(1-mh['win_pct'])-(1-bh['win_pct'])
            confirmed=(hlift>=.05 and mh['roi']<=bh['roi']-.05 and mhs['roi']>=bh['roi']-.02)
            vrows.append({'method_id':mid,'family':family,'feature':c,'op':op,'threshold':t,'confirmed':confirmed,
                          'base_n':met(x)['n'],'base_win_pct':met(x)['win_pct'],'base_roi':met(x)['roi'],
                          'train_risk_n':mt['n'],'train_risk_win_pct':mt['win_pct'],'train_risk_roi':mt['roi'],'train_safe_roi':ms['roi'],'train_loss_lift':tlift,
                          'validation_risk_n':mv['n'],'validation_risk_win_pct':mv['win_pct'],'validation_risk_roi':mv['roi'],'validation_safe_roi':mvs['roi'],'validation_loss_lift':vlift,
                          'holdout_risk_n':mh['n'],'holdout_risk_win_pct':mh['win_pct'],'holdout_risk_roi':mh['roi'],'holdout_safe_roi':mhs['roi'],'holdout_loss_lift':hlift,
                          'retained_share':len(hsafe)/len(hold) if len(hold) else np.nan})
veto=pd.DataFrame(vrows)
if len(veto):
    veto['veto_score']=veto.confirmed.astype(int)*10+veto.holdout_loss_lift.fillna(0)+(veto.holdout_safe_roi-veto.base_roi).fillna(0)+veto.retained_share.fillna(0)*.1
    veto=veto.sort_values(['confirmed','veto_score'],ascending=[False,False]);veto.to_csv(OUT/'veto_candidates.csv',index=False);veto[veto.confirmed].to_csv(OUT/'confirmed_vetoes.csv',index=False)

# -------- reconstruct independent H live-ready signals --------
hsets={};hmeta={}
for _,r in hreg.iterrows():
    conds=[]
    for j in [1,2,3]:
        f=r.get(f'feature{j}');op=r.get(f'op{j}');t=r.get(f'threshold{j}')
        if isinstance(f,str) and f and f!='nan' and pd.notna(t):conds.append((f,op,float(t)))
    x=d[(d.market_prob>=float(r.live_p_lo))&(d.market_prob<float(r.live_p_hi))&(num(d.prior_games)>=3)].copy()
    if r.venue=='AWAY':x=x[num(x.is_home).ne(1)]
    elif r.venue=='HOME':x=x[num(x.is_home).eq(1)]
    x=x[apply_conds(x,conds)].copy()
    # Apply only non-coaching hard vetoes for the strict independent H set.
    independent=True
    if str(r.hard_veto_source).upper()=='COACHING': independent=False
    elif isinstance(r.hard_veto_feature,str) and r.hard_veto_feature and r.hard_veto_feature!='nan' and pd.notna(r.hard_veto_threshold):
        veto_m=mask_cond(x,r.hard_veto_feature,r.hard_veto_op,float(r.hard_veto_threshold));x=x[~veto_m].copy()
    mid=str(r.candidate_id);hsets[mid]=x;hmeta[mid]={'independent':independent,'holdout_roi':r.holdout_roi,'full_roi':r.full_roi}

# -------- CO x H consensus --------
over=[]
strict_h=[k for k,v in hmeta.items() if v['independent']]
for _,cr in co.iterrows():
    cm=str(cr.method_id);cx=cosets[cm];cindex=set((cx.game_id.astype(str)+'|'+cx.team.astype(str)).tolist())
    tr,va,ho=tracks[cr.track]
    for hm in strict_h:
        hx=hsets[hm];hindex=set((hx.game_id.astype(str)+'|'+hx.team.astype(str)).tolist());keys=cindex & hindex
        if not keys:continue
        zz=cx[(cx.game_id.astype(str)+'|'+cx.team.astype(str)).isin(keys)].copy();m=met(zz);mh=met(zz[zz.season.between(*ho)])
        over.append({'coach_method':cm,'h_method':hm,'n':m['n'],'wins':m['wins'],'losses':m['losses'],'win_pct':m['win_pct'],'roi':m['roi'],'holdout_n':mh['n'],'holdout_win_pct':mh['win_pct'],'holdout_roi':mh['roi']})
# any independent H agrees
anyrows=[]
all_hkeys={k:set((v.game_id.astype(str)+'|'+v.team.astype(str)).tolist()) for k,v in hsets.items() if k in strict_h}
for _,cr in co.iterrows():
    cm=str(cr.method_id);cx=cosets[cm];agree=[]
    for ix,row in cx.iterrows():
        key=str(row.game_id)+'|'+str(row.team);cnt=sum(key in s for s in all_hkeys.values())
        if cnt>=1:agree.append((ix,cnt))
    if agree:
        ids=[i for i,_ in agree];z=cx.loc[ids].copy();z['h_agreement_count']=[c for _,c in agree];m=met(z);tr,va,ho=tracks[cr.track];mh=met(z[z.season.between(*ho)])
        anyrows.append({'coach_method':cm,'min_h_agreement':1,'n':m['n'],'wins':m['wins'],'losses':m['losses'],'win_pct':m['win_pct'],'roi':m['roi'],'holdout_n':mh['n'],'holdout_win_pct':mh['win_pct'],'holdout_roi':mh['roi'],'avg_h_agreements':float(z.h_agreement_count.mean())})
        z2=z[z.h_agreement_count>=2];m2=met(z2);mh2=met(z2[z2.season.between(*ho)])
        if len(z2):anyrows.append({'coach_method':cm,'min_h_agreement':2,'n':m2['n'],'wins':m2['wins'],'losses':m2['losses'],'win_pct':m2['win_pct'],'roi':m2['roi'],'holdout_n':mh2['n'],'holdout_win_pct':mh2['win_pct'],'holdout_roi':mh2['roi'],'avg_h_agreements':float(z2.h_agreement_count.mean())})
overdf=pd.DataFrame(over);anydf=pd.DataFrame(anyrows)
if len(overdf):overdf.sort_values(['holdout_n','holdout_roi','n'],ascending=[False,False,False]).to_csv(OUT/'coach_h_pair_overlap.csv',index=False)
if len(anydf):anydf.sort_values(['min_h_agreement','holdout_n','holdout_roi'],ascending=[True,False,False]).to_csv(OUT/'coach_any_h_consensus.csv',index=False)

# -------- final dissection recommendation --------
recs=[]
for _,r in co.iterrows():
    mid=str(r.method_id);x=cosets[mid];base=met(x);tr,va,ho=tracks[r.track];hold=met(x[x.season.between(*ho)])
    tv=timdf[(timdf.method_id==mid)&(timdf.confirmed_holdout==True)] if len(timdf) else pd.DataFrame()
    vv=veto[(veto.method_id==mid)&(veto.confirmed==True)] if len(veto) else pd.DataFrame()
    coachv=vv[vv.family=='COACH'] if len(vv) else pd.DataFrame();ctxv=vv[vv.family=='CONTEXT'] if len(vv) else pd.DataFrame()
    # select a single strongest veto only; never stack unvalidated filters.
    top=None
    if len(coachv):top=coachv.iloc[0]
    elif len(ctxv):top=ctxv.iloc[0]
    status='READY_CORE'
    reason=[]
    if len(tv):status='READY_WITH_TIMING_FILTER';reason.append('confirmed_weak_segment:'+','.join(tv.segment.tolist()))
    if top is not None:
        status='READY_WITH_SINGLE_VETO' if status=='READY_CORE' else 'READY_WITH_TIMING_AND_SINGLE_VETO'
        reason.append(f"{top.family}:{top.feature}{top.op}{top.threshold:.6g}")
    # downgrade if holdout is low after audit threshold or severe segment instability with no confirmed fix
    if hold['n']<20 or hold['roi']<.12:status='WATCH_AFTER_DISSECTION';reason.append('holdout_below_final_bar')
    cons=anydf[(anydf.coach_method==mid)&(anydf.min_h_agreement==1)] if len(anydf) else pd.DataFrame()
    recs.append({'method_id':mid,'base_n':base['n'],'base_win_pct':base['win_pct'],'base_roi':base['roi'],'holdout_n':hold['n'],'holdout_win_pct':hold['win_pct'],'holdout_roi':hold['roi'],
                 'confirmed_timing_filters':'|'.join(tv.segment.tolist()) if len(tv) else '',
                 'selected_veto_family':top.family if top is not None else '', 'selected_veto_feature':top.feature if top is not None else '', 'selected_veto_op':top.op if top is not None else '', 'selected_veto_threshold':top.threshold if top is not None else np.nan,
                 'consensus_h_n':int(cons.iloc[0].n) if len(cons) else 0,'consensus_h_win_pct':float(cons.iloc[0].win_pct) if len(cons) else np.nan,'consensus_h_roi':float(cons.iloc[0].roi) if len(cons) else np.nan,'consensus_h_holdout_n':int(cons.iloc[0].holdout_n) if len(cons) else 0,'consensus_h_holdout_roi':float(cons.iloc[0].holdout_roi) if len(cons) else np.nan,
                 'final_status':status,'reason':';'.join(reason)})
recdf=pd.DataFrame(recs);recdf.to_csv(OUT/'final_dissection.csv',index=False)

summary={'coach_live_ready_input':int(len(co)),'confirmed_timing_filters':int(timdf.confirmed_holdout.sum()) if len(timdf) else 0,'confirmed_vetoes':int(veto.confirmed.sum()) if len(veto) else 0,'confirmed_coach_vetoes':int(((veto.confirmed)&(veto.family=='COACH')).sum()) if len(veto) else 0,'confirmed_context_vetoes':int(((veto.confirmed)&(veto.family=='CONTEXT')).sum()) if len(veto) else 0,'independent_h_methods':len(strict_h),'coach_h_pairs_with_overlap':int(len(overdf)) if len(overdf) else 0,'final_status_counts':recdf.final_status.value_counts().to_dict()}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
lines=['NFL COACH-ONLY DEEP DISSECTION','',json.dumps(summary,indent=2),'','FINAL RECOMMENDATIONS']
for _,r in recdf.iterrows():
    lines.append(f"{r.method_id}: {r.final_status} | {int(r.base_n)} bets {100*r.base_win_pct:.1f}% win ROI {100*r.base_roi:+.1f}% | hold {int(r.holdout_n)} {100*r.holdout_win_pct:.1f}% ROI {100*r.holdout_roi:+.1f}% | timing={r.confirmed_timing_filters or '-'} veto={r.selected_veto_family+':'+r.selected_veto_feature if r.selected_veto_feature else '-'} | CO+H n={int(r.consensus_h_n)} ROI={100*r.consensus_h_roi:+.1f}% hold_n={int(r.consensus_h_holdout_n)} holdROI={100*r.consensus_h_holdout_roi:+.1f}%")
lines+=['','TOP CONFIRMED VETOES']
if len(veto):
    for _,r in veto[veto.confirmed].head(20).iterrows():lines.append(f"{r.method_id} {r.family} {r.feature} {r.op} {r.threshold:.6g} | hold risk n={int(r.holdout_risk_n)} win={100*r.holdout_risk_win_pct:.1f}% ROI={100*r.holdout_risk_roi:+.1f}% -> safe ROI={100*r.holdout_safe_roi:+.1f}% retain={100*r.retained_share:.0f}%")
lines+=['','TOP CO+H CONSENSUS']
if len(anydf):
    for _,r in anydf[(anydf.min_h_agreement==1)&(anydf.n>=8)].sort_values(['holdout_roi','holdout_n'],ascending=[False,False]).head(15).iterrows():lines.append(f"{r.coach_method}+any independent H | {int(r.wins)}-{int(r.losses)} n={int(r.n)} win={100*r.win_pct:.1f}% ROI={100*r.roi:+.1f}% | hold n={int(r.holdout_n)} win={100*r.holdout_win_pct:.1f}% ROI={100*r.holdout_roi:+.1f}%")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n');print('\n'.join(lines[:120]))
