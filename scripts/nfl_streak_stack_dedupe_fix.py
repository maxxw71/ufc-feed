from pathlib import Path
import os,json,itertools,runpy
import numpy as np,pandas as pd

REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
OUT=Path('/home/appwiza-runner/nfl-context-data/streak_priority_deep_dissection')
OUT.mkdir(parents=True,exist_ok=True)

# Rebuild exact pregame-safe universe used by discovery.
ns=runpy.run_path(str(REPO/'scripts/nfl_streak_bounceback_division_discovery.py'))
d=ns['d'].copy();num=ns['num'];tracks=ns['tracks'];bands=ns['bands']

def met(x):
    if not len(x):return dict(n=0,wins=0,losses=0,win_pct=np.nan,roi=np.nan,units=0.)
    return dict(n=int(len(x)),wins=int(x.win.sum()),losses=int(len(x)-x.win.sum()),win_pct=float(x.win.mean()),roi=float(x.profit.mean()),units=float(x.profit.sum()))
def cmask(x,c,o,t):
    if c not in x:return pd.Series(False,index=x.index)
    v=num(x[c]);t=float(t)
    return {'>=':v>=t,'<=':v<=t,'>':v>t,'<':v<t,'==':v==t}.get(str(o),pd.Series(False,index=x.index))
def applyconds(x,conds):
    m=pd.Series(True,index=x.index)
    for c,o,t in conds:m &= cmask(x,c,o,t)
    return m
def apply_stack(x,combo):
    keep=pd.Series(True,index=x.index)
    for c in combo:
        if c['kind']=='TIMING':risk=num(x.week).between(int(c['w1']),int(c['w2']))
        else:risk=cmask(x,c['feature'],c['op'],float(c['threshold']))
        keep &= ~risk
    return x[keep]
def season_ratio(x):
    if not len(x):return np.nan
    y=x.groupby('season').profit.sum();return float((y>0).mean())

# Same six priority methods.
METHODS={
 'SB002':dict(track='LONG',band='DOG35_44',venue='ANY',concept='STREAK',conds=[['opp_seq_last3_wins','<=',1.0],['coachq_prev_hc_prior_role_seasons','<=',0.0]]),
 'SB006':dict(track='LONG',band='DOG35_49',venue='ANY',concept='STREAK',conds=[['seq_loss_streak','>=',3.0],['rank_edge_def_allowed_giveaway_rate','<=',-10.0]]),
 'SB008':dict(track='LONG',band='FAV55_65',venue='AWAY',concept='STREAK',conds=[['opp_seq_last5_margin','<=',1.6],['recent_edge_kick_return_average','>=',4.361805555555557]]),
 'SB011':dict(track='LONG',band='FAV65_75',venue='AWAY',concept='STREAK',conds=[['seq_last5_wins','>=',2.0],['recent_edge_penalty_yards','<=',-17.75]]),
 'SB016':dict(track='MODERN',band='DOG35_49',venue='ANY',concept='BOUNCEBACK',conds=[['opp_seq_blowout_win14','>=',1.0],['coachq_prev_oc_prior_win_pct','>=',0.47115384615384615]]),
 'SB025':dict(track='LONG',band='FAV55_65',venue='AWAY',concept='STREAK',conds=[['seq_last3_margin','<=',9.0],['recent_edge_def_allowed_yards_per_play','>=',0.7302054077592606]])
}
def base_set(m):
    lo,hi=bands[m['band']];x=d[(d.market_prob>=lo)&(d.market_prob<hi)&(num(d.prior_games)>=3)].copy()
    if m['venue']=='AWAY':x=x[num(x.is_home).ne(1)]
    elif m['venue']=='HOME':x=x[num(x.is_home).eq(1)]
    return x[applyconds(x,m['conds'])].copy()

cand=pd.read_csv(REPO/'nfl/streak_priority_deep_dissection/veto_candidates.csv')
old=pd.read_csv(REPO/'nfl/streak_priority_deep_dissection/final_sieve.csv')
price=pd.read_csv(REPO/'nfl/streak_priority_deep_dissection/price_audit.csv') if (REPO/'nfl/streak_priority_deep_dissection/price_audit.csv').exists() else pd.DataFrame()

# Canonicalize candidates: no exact dupes; a stack may contain at most one veto per feature.
# This removes nested conditions like points_against<=-16 AND <=-21 which add zero new information.
rows=[];audit=[]
for mid,m in METHODS.items():
    x=base_set(m);tr,va,ho=tracks[m['track']];train=x[x.season.between(*tr)];val=x[x.season.between(*va)];hold=x[x.season.between(*ho)]
    bt,bv,bh=met(train),met(val),met(hold);basepre=(bt['roi']+bv['roi'])/2;basepren=len(train)+len(val)
    cc=cand[cand.method_id.eq(mid)].copy()
    cc=cc.drop_duplicates(['kind','feature','op','threshold']).sort_values('pre_score',ascending=False).head(14)
    cands=[]
    for _,r in cc.iterrows():
        q={'kind':str(r.kind),'feature':str(r.feature),'op':str(r.op),'threshold':r.threshold,'pre_score':float(r.pre_score)}
        if q['kind']=='TIMING':
            try:q['w1'],q['w2']=[int(z) for z in str(r.threshold).split('-')]
            except:continue
        cands.append(q)
    frozen=[]
    for depth in [1,2,3]:
        best=None
        for combo in itertools.combinations(cands,depth):
            # New anti-redundancy rule: same feature cannot appear twice in a stack.
            feats=[c['feature'] for c in combo]
            if len(set(feats))!=len(feats):continue
            a,b=apply_stack(train,combo),apply_stack(val,combo);ma,mb=met(a),met(b);pren=len(a)+len(b)
            if len(a)<max(12,int(.50*len(train))) or len(b)<max(8,int(.50*len(val))) or pren<.58*basepren:continue
            if ma['roi']<bt['roi']-.02 or mb['roi']<bv['roi']-.02:continue
            pre=(ma['roi']+mb['roi'])/2;impr=pre-basepre
            if impr<(.04+.025*(depth-1)):continue
            score=pre-.02*(depth-1)+.02*(pren/basepren)
            if best is None or score>best[0]:best=(score,combo,ma,mb,pre)
        if best:frozen.append((depth,best))
    accepted=[]
    for depth,best in frozen:
        _,combo,ma,mb,pre=best;hz=apply_stack(hold,combo);mh=met(hz);fz=apply_stack(x,combo);mf=met(fz)
        confirmed=mh['n']>=max(10,int(.50*bh['n'])) and mh['roi']>=bh['roi']+.02 and mf['n']>=max(55,int(.55*len(x)))
        audit.append({'method_id':mid,'depth':depth,'vetoes':' | '.join(f"{c['kind']}:{c['feature']} {c['op']} {c['threshold']}" for c in combo),'pre_roi':pre,'holdout_n':mh['n'],'holdout_win_pct':mh['win_pct'],'holdout_roi':mh['roi'],'full_n':mf['n'],'full_win_pct':mf['win_pct'],'full_roi':mf['roi'],'confirmed':confirmed})
        if confirmed:accepted.append((mh['roi'],depth,combo,mf,mh,pre))
    # Complexity-regularized choice: only add depth if it beats a simpler confirmed stack by >=1.5pp holdout ROI.
    chosen=None
    for item in sorted(accepted,key=lambda z:(z[1],-z[0])):
        if chosen is None:chosen=item
        elif item[0]>=chosen[0]+.015:chosen=item
    if chosen:
        _,depth,combo,mf,mh,pre=chosen;veto=' | '.join(f"{c['kind']}:{c['feature']} {c['op']} {c['threshold']}" for c in combo);vc=depth;fz=apply_stack(x,combo)
    else:
        mf=met(x);mh=bh;veto='';vc=0;fz=x
    oldrow=old[old.method_id.eq(mid)].iloc[0] if (old.method_id==mid).any() else pd.Series(dtype=object)
    odds=oldrow.get('final_odds',m['band']);pdec=oldrow.get('price_decision','KEEP_ORIGINAL')
    status='SHADOW_READY' if (mf['n']>=70 and mh['roi']>=.10) else 'WATCH'
    rows.append({'method_id':mid,'concept':m['concept'],'base_record':f"{bt['wins']+bv['wins']+bh['wins']}-{bt['losses']+bv['losses']+bh['losses']}",'base_win_pct':float(x.win.mean()),'base_roi':float(x.profit.mean()),'base_holdout_roi':bh['roi'],'final_record':f"{mf['wins']}-{mf['losses']}",'final_win_pct':mf['win_pct'],'final_roi':mf['roi'],'final_holdout_n':mh['n'],'final_holdout_win_pct':mh['win_pct'],'final_holdout_roi':mh['roi'],'positive_season_ratio':season_ratio(fz),'veto_count':vc,'vetoes':veto,'final_odds':odds,'price_decision':pdec,'status':status})

final=pd.DataFrame(rows);final.to_csv(OUT/'final_sieve.csv',index=False);pd.DataFrame(audit).to_csv(OUT/'deduped_stack_audit.csv',index=False)
summary={'methods':len(final),'shadow_ready':int((final.status=='SHADOW_READY').sum()),'watch':int((final.status=='WATCH').sum()),'multi_veto_accepted':int((final.veto_count>1).sum()),'single_veto_accepted':int((final.veto_count==1).sum()),'no_veto':int((final.veto_count==0).sum()),'redundant_same_feature_stacks_forbidden':True}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
lines=['NFL STREAK PRIORITY DEEP DISSECTION — DEDUPED STACKS','',json.dumps(summary,indent=2),'','FINAL']
for _,r in final.iterrows():lines.append(f"{r.method_id} {r.concept} | base {r.base_record} win={100*r.base_win_pct:.1f}% ROI={100*r.base_roi:+.1f}% hold={100*r.base_holdout_roi:+.1f}% | final {r.final_record} win={100*r.final_win_pct:.1f}% ROI={100*r.final_roi:+.1f}% | hold n={int(r.final_holdout_n)} win={100*r.final_holdout_win_pct:.1f}% ROI={100*r.final_holdout_roi:+.1f}% | pos={100*r.positive_season_ratio:.0f}% | vetoes={int(r.veto_count)} [{r.vetoes}] | {r.status}")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
