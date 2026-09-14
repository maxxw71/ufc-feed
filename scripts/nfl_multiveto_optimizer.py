from pathlib import Path
import os, json, itertools, runpy
import numpy as np
import pandas as pd

REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
CTX=Path('/home/appwiza-runner/nfl-context-data')
OUT=CTX/'multiveto_optimizer';OUT.mkdir(parents=True,exist_ok=True)

# Reuse the exact REG-only reconstruction and base RS signals from the existing deep dissection.
ns=runpy.run_path(str(REPO/'scripts/nfl_top3_run_schedule_dissection.py'))
sets=ns['sets']; tracks=ns['tracks']; met=ns['met']; cmask=ns['cmask']

vc=pd.read_csv(REPO/'nfl/top3_run_schedule_dissection/veto_candidates.csv')
tc=pd.read_csv(REPO/'nfl/top3_timing_final_audit/confirmed_timing_filters.csv')
current=pd.read_csv(REPO/'nfl/top3_timing_final_audit/final_methods.csv')


def boolish(v):
    if isinstance(v,bool):return v
    return str(v).strip().lower() in {'true','1','yes'}

def risk_mask(x,c):
    if c['kind']=='FEATURE':
        return cmask(x,c['feature'],c['op'],float(c['threshold']))
    return pd.to_numeric(x.week,errors='coerce').between(int(c['week_lo']),int(c['week_hi']))

def apply_stack(x,stack):
    if not stack:return x.copy()
    risk=pd.Series(False,index=x.index)
    for c in stack:risk |= risk_mask(x,c)
    return x[~risk].copy()

def desc(c):
    if c['kind']=='FEATURE':return f"{c['feature']} {c['op']} {float(c['threshold']):.6g}"
    return f"W{int(c['week_lo'])}-{int(c['week_hi'])}"

def season_stats(x):
    if not len(x):return (np.nan,0,0,np.nan)
    y=x.groupby('season').profit.sum();act=len(y);pos=int((y>0).sum());worst=float((x.groupby('season').profit.sum()/x.groupby('season').size()).min())
    return pos/act if act else np.nan,pos,act,worst

all_combo=[];results=[];manifest=[]
for mid,x in sets.items():
    tr,va,ho=tracks['LONG']
    train=x[x.season.between(*tr)].copy();val=x[x.season.between(*va)].copy();hold=x[x.season.between(*ho)].copy()
    bt,bv,bh=met(train),met(val),met(hold);base_pre=(bt['roi']+bv['roi'])/2

    # Candidate pool is selected only from pre-holdout evidence. Holdout confirmation columns are ignored here.
    q=vc[vc.method_id.eq(mid)].copy()
    if len(q):
        q=q.sort_values('pre_score',ascending=False).drop_duplicates(['feature','op'],keep='first').head(12)
    pool=[]
    for _,r in q.iterrows():
        pool.append({'kind':'FEATURE','feature':r.feature,'op':r.op,'threshold':float(r.threshold),'pre_score_source':float(r.pre_score)})
    tq=tc[(tc.method_id.eq(mid)) & tc.selected_preholdout.map(boolish)].copy()
    for _,r in tq.iterrows():
        pool.append({'kind':'TIMING','window':r.window,'week_lo':int(r.week_lo),'week_hi':int(r.week_hi),'pre_score_source':float(r.pre_score)})

    # Search up to 3 vetoes. Selection is entirely train+validation.
    best_by_k={0:{'stack':[],'pre_avg':base_pre,'robust_score':base_pre,'train':bt,'val':bv,'retain_train':1.0,'retain_val':1.0}}
    for k in [1,2,3]:
        candidates=[]
        for combo in itertools.combinations(pool,k):
            # Avoid redundant nested timing windows in the same stack.
            timings=[c for c in combo if c['kind']=='TIMING']
            redundant=False
            if len(timings)>1:
                for a,b in itertools.combinations(timings,2):
                    if not (a['week_hi']<b['week_lo'] or b['week_hi']<a['week_lo']):redundant=True
            if redundant:continue
            st=apply_stack(train,combo);sv=apply_stack(val,combo);mt,mv=met(st),met(sv)
            rt=mt['n']/bt['n'] if bt['n'] else 0;rv=mv['n']/bv['n'] if bv['n'] else 0
            if mt['n']<15 or mv['n']<12 or rt<.55 or rv<.55:continue
            if mt['roi']<bt['roi']-.02 or mv['roi']<bv['roi']-.02:continue
            pre_avg=(mt['roi']+mv['roi'])/2
            if pre_avg<base_pre+.05:continue
            # reward two-era balance, sample retention; penalize extra complexity.
            robust=pre_avg + .20*min(mt['roi'],mv['roi']) + .03*((mt['win_pct']+mv['win_pct'])/2) + .03*min(rt,rv) - .015*k
            rec={'method_id':mid,'k':k,'stack':combo,'pre_avg':pre_avg,'robust_score':robust,'train':mt,'val':mv,'retain_train':rt,'retain_val':rv}
            candidates.append(rec)
            all_combo.append({'method_id':mid,'k':k,'stack':' | '.join(desc(c) for c in combo),'train_n':mt['n'],'train_win_pct':mt['win_pct'],'train_roi':mt['roi'],'validation_n':mv['n'],'validation_win_pct':mv['win_pct'],'validation_roi':mv['roi'],'retain_train':rt,'retain_validation':rv,'pre_avg_roi':pre_avg,'robust_score':robust})
        if candidates:
            candidates=sorted(candidates,key=lambda r:r['robust_score'],reverse=True)
            best_by_k[k]=candidates[0]

    # Complexity-regularized selection: each added veto must buy at least +3pp pre-holdout ROI.
    selected=best_by_k[0]
    for k in [1,2,3]:
        if k not in best_by_k:continue
        cand=best_by_k[k]
        needed=.05 if selected['stack']==[] else .03
        if cand['pre_avg'] >= selected['pre_avg'] + needed:
            selected=cand

    sf=apply_stack(x,selected['stack']);sh=apply_stack(hold,selected['stack']);mf,mh=met(sf),met(sh);ratio,pos,act,worst=season_stats(sf)
    cur=current[current.method_id.eq(mid)].iloc[0]
    current_hold=float(cur.holdout_roi);current_roi=float(cur.roi)
    hold_retain=mh['n']/bh['n'] if bh['n'] else 0

    # Do NOT tune after seeing holdout. Either the frozen stack confirms or it is rejected.
    if len(selected['stack'])>=2:
        accepted=(mh['n']>=10 and hold_retain>=.50 and mh['roi']>=current_hold+.05 and mf['roi']>=current_roi+.03)
        status='MULTI_VETO_ACCEPTED' if accepted else 'MULTI_VETO_REJECTED_KEEP_CURRENT'
    elif len(selected['stack'])==1:
        accepted=(mh['n']>=10 and hold_retain>=.50 and mh['roi']>=current_hold-.02)
        status='SINGLE_VETO_PREHOLD_SELECTED_CONFIRMED' if accepted else 'PREHOLD_SELECTED_REJECTED_KEEP_CURRENT'
    else:
        accepted=False;status='NO_STACK_BEATS_CURRENT'

    # Leave-one-season-out stability for the frozen selected stack.
    loo=[]
    for season in sorted(sf.season.unique()):
        mm=met(sf[sf.season.ne(season)]);loo.append(mm['roi'])
    loo_min=float(np.nanmin(loo)) if loo else np.nan

    results.append({'method_id':mid,'selected_veto_count':len(selected['stack']),'selected_stack':' | '.join(desc(c) for c in selected['stack']),
                    'base_train_roi':bt['roi'],'base_validation_roi':bv['roi'],'base_holdout_roi':bh['roi'],
                    'selected_train_n':selected['train']['n'],'selected_train_roi':selected['train']['roi'],'selected_validation_n':selected['val']['n'],'selected_validation_roi':selected['val']['roi'],
                    'selected_pre_avg_roi':selected['pre_avg'],'full_n':mf['n'],'full_wins':mf['wins'],'full_losses':mf['losses'],'full_win_pct':mf['win_pct'],'full_roi':mf['roi'],
                    'holdout_n':mh['n'],'holdout_win_pct':mh['win_pct'],'holdout_roi':mh['roi'],'holdout_retained_share':hold_retain,
                    'positive_season_ratio':ratio,'positive_seasons':pos,'active_seasons':act,'worst_season_roi':worst,'loo_min_roi':loo_min,
                    'current_single_roi':current_roi,'current_single_holdout_roi':current_hold,'status':status})
    if status=='MULTI_VETO_ACCEPTED':
        manifest.append({'method_id':mid,'mode':'SHADOW_ONLY','vetoes':[desc(c) for c in selected['stack']],'historical_record':f"{mf['wins']}-{mf['losses']}",'historical_win_pct':mf['win_pct'],'historical_roi':mf['roi'],'holdout_roi':mh['roi'],'holdout_retained_share':hold_retain})

pd.DataFrame(all_combo).sort_values(['method_id','k','robust_score'],ascending=[True,True,False]).to_csv(OUT/'all_preholdout_combinations.csv',index=False)
res=pd.DataFrame(results);res.to_csv(OUT/'selected_stacks.csv',index=False)
(OUT/'accepted_multi_veto_shadow.json').write_text(json.dumps({'mode':'SHADOW_ONLY','policy':'complexity_regularized_multi_veto','methods':manifest},indent=2))
summary={'methods':len(res),'multi_veto_accepted':int((res.status=='MULTI_VETO_ACCEPTED').sum()),'multi_veto_rejected':int((res.status=='MULTI_VETO_REJECTED_KEEP_CURRENT').sum()),'other':int((~res.status.isin(['MULTI_VETO_ACCEPTED','MULTI_VETO_REJECTED_KEEP_CURRENT'])).sum())}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
lines=['NFL COMPLEXITY-REGULARIZED MULTI-VETO OPTIMIZER','',json.dumps(summary,indent=2),'']
for _,r in res.iterrows():
    lines.append(f"{r.method_id}: {r.status} | vetoes={int(r.selected_veto_count)} [{r.selected_stack}] | pre avg={100*r.selected_pre_avg_roi:+.1f}% | full {int(r.full_wins)}-{int(r.full_losses)} win={100*r.full_win_pct:.1f}% ROI={100*r.full_roi:+.1f}% | hold n={int(r.holdout_n)} ROI={100*r.holdout_roi:+.1f}% retain={100*r.holdout_retained_share:.0f}% | current single hold={100*r.current_single_holdout_roi:+.1f}% | LOO min={100*r.loo_min_roi:+.1f}%")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
