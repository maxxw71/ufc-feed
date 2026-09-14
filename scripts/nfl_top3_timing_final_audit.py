from pathlib import Path
import os,runpy,json
import numpy as np
import pandas as pd

REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
CTX=Path('/home/appwiza-runner/nfl-context-data')
OUT=CTX/'top3_timing_final_audit';OUT.mkdir(parents=True,exist_ok=True)
# Use exact final-review reconstruction and one-veto candidate pool.
g=runpy.run_path(str(REPO/'scripts/nfl_top3_run_schedule_final_review.py'))
sets=g['sets'];METHODS=g['METHODS'];tracks=g['tracks'];met=g['met'];cmask=g['cmask'];sv=g['sv'];pf=g['pf'];season_ratio=g['season_ratio']

# Test week/segment exclusions after the signal but BEFORE stacking another filter.
# Timing must prove itself in train + validation and then merely confirm in holdout.
windows=[('W4_6',4,6),('W7_10',7,10),('W11_14',11,14),('W15_18',15,18)]
windows += [(f'W{w}',w,w) for w in range(4,19)]
windows += [(f'W{w}_{w+1}',w,w+1) for w in range(4,18)]
rows=[]
for mid,m in METHODS.items():
    x=sets[mid].copy();tr,va,ho=tracks[m['track']];train=x[x.season.between(*tr)];val=x[x.season.between(*va)];hold=x[x.season.between(*ho)];bt,bv,bh=met(train),met(val),met(hold)
    for name,a,b in windows:
        rt=train[pd.to_numeric(train.week,errors='coerce').between(a,b)];rv=val[pd.to_numeric(val.week,errors='coerce').between(a,b)];rh=hold[pd.to_numeric(hold.week,errors='coerce').between(a,b)]
        st=train[~pd.to_numeric(train.week,errors='coerce').between(a,b)];svv=val[~pd.to_numeric(val.week,errors='coerce').between(a,b)];sh=hold[~pd.to_numeric(hold.week,errors='coerce').between(a,b)]
        mt,mv,mh,ms,msv,msh=met(rt),met(rv),met(rh),met(st),met(svv),met(sh)
        mintrain=5 if a!=b else 3;minval=4 if a!=b else 3;minhold=4 if a!=b else 3
        if mt['n']<mintrain or mv['n']<minval or mh['n']<minhold:continue
        tl=(1-mt['win_pct'])-(1-bt['win_pct']);vl=(1-mv['win_pct'])-(1-bv['win_pct']);hl=(1-mh['win_pct'])-(1-bh['win_pct'])
        selected=(tl>=.08 and vl>=.08 and mt['roi']<=bt['roi']-.10 and mv['roi']<=bv['roi']-.10 and ms['roi']>=bt['roi']-.02 and msv['roi']>=bv['roi']-.02)
        confirmed=selected and hl>=.05 and mh['roi']<=bh['roi']-.05 and msh['roi']>=bh['roi']-.02
        pre_score=tl+vl+(ms['roi']-bt['roi'])+(msv['roi']-bv['roi'])+.05*((len(st)+len(svv))/(len(train)+len(val)))
        rows.append({'method_id':mid,'window':name,'week_lo':a,'week_hi':b,'selected_preholdout':selected,'confirmed_holdout':confirmed,'pre_score':pre_score,'train_risk_n':mt['n'],'train_risk_win_pct':mt['win_pct'],'train_risk_roi':mt['roi'],'train_safe_roi':ms['roi'],'validation_risk_n':mv['n'],'validation_risk_win_pct':mv['win_pct'],'validation_risk_roi':mv['roi'],'validation_safe_roi':msv['roi'],'holdout_risk_n':mh['n'],'holdout_risk_win_pct':mh['win_pct'],'holdout_risk_roi':mh['roi'],'holdout_safe_roi':msh['roi'],'holdout_retained_share':len(sh)/len(hold) if len(hold) else np.nan})
tim=pd.DataFrame(rows);tim.to_csv(OUT/'timing_candidates.csv',index=False)
conf=tim[(tim.selected_preholdout.eq(True))&(tim.confirmed_holdout.eq(True))&(tim.holdout_retained_share>=.65)].copy() if len(tim) else pd.DataFrame()
conf.to_csv(OUT/'confirmed_timing_filters.csv',index=False)

# Timing competes with the feature veto for the SINGLE allowed veto slot. Never stack both.
final=[]
for mid,m in METHODS.items():
    x=sets[mid].copy();choices=[]
    qv=sv[sv.method_id.eq(mid)] if len(sv) else pd.DataFrame()
    if len(qv):
        v=qv.iloc[0];choices.append({'kind':'FEATURE','label':f'{v.feature} {v.op} {v.threshold}','pre_score':float(v.pre_score),'mask':cmask(x,v.feature,v.op,float(v.threshold))})
    qt=conf[conf.method_id.eq(mid)].sort_values('pre_score',ascending=False) if len(conf) else pd.DataFrame()
    if len(qt):
        t=qt.iloc[0];wm=pd.to_numeric(x.week,errors='coerce').between(int(t.week_lo),int(t.week_hi));choices.append({'kind':'TIMING','label':t.window,'pre_score':float(t.pre_score),'mask':wm})
    chosen=max(choices,key=lambda z:z['pre_score']) if choices else None
    if chosen:x=x[~chosen['mask']].copy()
    tr,va,ho=tracks[m['track']];mf=met(x);mt=met(x[x.season.between(*tr)]);mv=met(x[x.season.between(*va)]);mh=met(x[x.season.between(*ho)]);ratio,pos,active=season_ratio(x);tc=x.team.value_counts(normalize=True);top=float(tc.iloc[0]) if len(tc) else np.nan
    robust=(mf['n']>=60 and mh['n']>=15 and mf['roi']>=.15 and mt['roi']>=.08 and mv['roi']>=.08 and mh['roi']>=.15 and ratio>=.70 and top<=.12)
    p=pf[pf.method_id.eq(mid)].iloc[0];odds=str(p.odds_range).replace('-100','+100')
    final.append({'method_id':mid,'source':m['source'],'name':m['name'],'n':mf['n'],'wins':mf['wins'],'losses':mf['losses'],'win_pct':mf['win_pct'],'roi':mf['roi'],'train_roi':mt['roi'],'validation_roi':mv['roi'],'holdout_n':mh['n'],'holdout_win_pct':mh['win_pct'],'holdout_roi':mh['roi'],'positive_season_ratio':ratio,'odds_range':odds,'single_veto_kind':'-' if chosen is None else chosen['kind'],'single_veto':'-' if chosen is None else chosen['label'],'status':'FINAL_SHADOW_READY' if robust else 'WATCH'})
fin=pd.DataFrame(final);fin.to_csv(OUT/'final_methods.csv',index=False)
manifest={'mode':'SHADOW_ONLY','public_website_enabled':False,'email_enabled':False,'bet_tracker_enabled':False,'policy':'One total veto maximum: timing and feature vetoes compete; never stack. Narrowed price windows rejected by holdout revert to original discovery band.','methods':[]}
for _,r in fin[fin.status.eq('FINAL_SHADOW_READY')].iterrows():manifest['methods'].append({'method_id':r.method_id,'source':r.source,'name':r['name'],'odds_range':r.odds_range,'conditions':METHODS[r.method_id]['conds'],'single_veto_kind':None if r.single_veto_kind=='-' else r.single_veto_kind,'single_veto':None if r.single_veto=='-' else r.single_veto,'historical_record':f"{int(r.wins)}-{int(r.losses)}",'historical_win_pct':float(r.win_pct),'historical_roi':float(r.roi),'holdout_roi':float(r.holdout_roi)})
(OUT/'shadow_manifest.json').write_text(json.dumps(manifest,indent=2))
lines=['TOP-3 RUN / SCHEDULE FINAL TIMING AUDIT','',json.dumps({'confirmed_timing_filters':int(len(conf)),'final_shadow_ready':int(fin.status.eq('FINAL_SHADOW_READY').sum()),'public_live':False},indent=2),'','FINAL']
for _,r in fin.iterrows():lines.append(f"{r.method_id} {r['name']} | {int(r.wins)}-{int(r.losses)} ({100*r.win_pct:.1f}%) ROI={100*r.roi:+.1f}% train={100*r.train_roi:+.1f}% val={100*r.validation_roi:+.1f}% hold={100*r.holdout_roi:+.1f}% pos={100*r.positive_season_ratio:.0f}% | odds={r.odds_range} | one veto={r.single_veto_kind}:{r.single_veto} | {r.status}")
if len(conf):
    lines+=['','CONFIRMED TIMING CANDIDATES (compete for one veto slot)']
    for _,r in conf.sort_values(['method_id','pre_score'],ascending=[True,False]).iterrows():lines.append(f"{r.method_id} {r.window}: pre_score={r.pre_score:.3f} hold risk n={int(r.holdout_risk_n)} ROI={100*r.holdout_risk_roi:+.1f}% safe={100*r.holdout_safe_roi:+.1f}% retain={100*r.holdout_retained_share:.0f}%")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
