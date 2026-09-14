from pathlib import Path
import os,json,itertools,runpy
import numpy as np,pandas as pd

REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
CTX=Path('/home/appwiza-runner/nfl-context-data')
OUT=CTX/'streak_priority_deep_dissection';OUT.mkdir(parents=True,exist_ok=True)

# Rebuild the discovery universe so sequence features are exactly the same and remain pregame-safe.
ns=runpy.run_path(str(REPO/'scripts/nfl_streak_bounceback_division_discovery.py'))
d=ns['d'].copy();num=ns['num'];met0=ns['met'];fil=ns['fil'];tracks=ns['tracks'];bands=ns['bands']
d['losses_tmp']=1-d['win']

def met(x):
    if not len(x): return dict(n=0,wins=0,losses=0,win_pct=np.nan,roi=np.nan,units=0.)
    return dict(n=int(len(x)),wins=int(x.win.sum()),losses=int(len(x)-x.win.sum()),win_pct=float(x.win.mean()),roi=float(x.profit.mean()),units=float(x.profit.sum()))
def season_stats(x):
    if not len(x): return dict(positive_season_ratio=np.nan,positive_seasons=0,active_seasons=0,max_season_share=np.nan)
    y=x.groupby('season').agg(n=('win','size'),units=('profit','sum'))
    return dict(positive_season_ratio=float((y.units>0).mean()),positive_seasons=int((y.units>0).sum()),active_seasons=int(len(y)),max_season_share=float(y.n.max()/len(x)))
def cmask(x,c,o,t):
    if c not in x:return pd.Series(False,index=x.index)
    v=num(x[c]);t=float(t)
    return {'>=':v>=t,'<=':v<=t,'>':v>t,'<':v<t,'==':v==t}.get(str(o),pd.Series(False,index=x.index))
def applyconds(x,conds):
    m=pd.Series(True,index=x.index)
    for c,o,t in conds:m &= cmask(x,c,o,t)
    return m
def p_to_american(p):
    if p<.5:return 100*(1-p)/p
    return -100*p/(1-p)
def odds_label(lo,hi):
    a,b=p_to_american(hi),p_to_american(lo)
    def f(v):return f'+{int(round(v))}' if v>=0 else str(int(round(v)))
    return f'{f(a)} to {f(b)}'

METHODS={
 'SB002':dict(track='LONG',band='DOG35_44',venue='ANY',concept='STREAK',conds=[['opp_seq_last3_wins','<=',1.0],['coachq_prev_hc_prior_role_seasons','<=',0.0]]),
 'SB006':dict(track='LONG',band='DOG35_49',venue='ANY',concept='STREAK',conds=[['seq_loss_streak','>=',3.0],['rank_edge_def_allowed_giveaway_rate','<=',-10.0]]),
 'SB008':dict(track='LONG',band='FAV55_65',venue='AWAY',concept='STREAK',conds=[['opp_seq_last5_margin','<=',1.6],['recent_edge_kick_return_average','>=',4.361805555555557]]),
 'SB011':dict(track='LONG',band='FAV65_75',venue='AWAY',concept='STREAK',conds=[['seq_last5_wins','>=',2.0],['recent_edge_penalty_yards','<=',-17.75]]),
 'SB016':dict(track='MODERN',band='DOG35_49',venue='ANY',concept='BOUNCEBACK',conds=[['opp_seq_blowout_win14','>=',1.0],['coachq_prev_oc_prior_win_pct','>=',0.47115384615384615]]),
 'SB025':dict(track='LONG',band='FAV55_65',venue='AWAY',concept='STREAK',conds=[['seq_last3_margin','<=',9.0],['recent_edge_def_allowed_yards_per_play','>=',0.7302054077592606]])
}

def base_set(m,lo=None,hi=None):
    blo,bhi=bands[m['band']];lo=blo if lo is None else lo;hi=bhi if hi is None else hi
    x=d[(d.market_prob>=lo)&(d.market_prob<hi)&(num(d.prior_games)>=3)].copy()
    if m['venue']=='AWAY':x=x[num(x.is_home).ne(1)]
    elif m['venue']=='HOME':x=x[num(x.is_home).eq(1)]
    return x[applyconds(x,m['conds'])].copy()

sets={k:base_set(v) for k,v in METHODS.items()}
base_rows=[];season_rows=[];week_rows=[];loss_rows=[];team_rows=[]
for mid,m in METHODS.items():
    x=sets[mid];tr,va,ho=tracks[m['track']];a=met(x[x.season.between(*tr)]);b=met(x[x.season.between(*va)]);h=met(x[x.season.between(*ho)]);f=met(x);ss=season_stats(x);tv=x.team.value_counts(normalize=True)
    base_rows.append({'method_id':mid,**m,**f,'train_n':a['n'],'train_win_pct':a['win_pct'],'train_roi':a['roi'],'validation_n':b['n'],'validation_win_pct':b['win_pct'],'validation_roi':b['roi'],'holdout_n':h['n'],'holdout_win_pct':h['win_pct'],'holdout_roi':h['roi'],**ss,'top_team':tv.index[0] if len(tv) else None,'top_team_share':float(tv.iloc[0]) if len(tv) else np.nan})
    for season,g in x.groupby('season'):season_rows.append({'method_id':mid,'season':int(season),**met(g)})
    for w in range(4,19):week_rows.append({'method_id':mid,'week':w,**met(x[num(x.week)==w])})
    for _,r in x[x.win.eq(0)].iterrows():loss_rows.append({'method_id':mid,'season':int(r.season),'week':int(r.week),'game_id':r.game_id,'team':r.team,'opponent':r.opponent,'moneyline':r.moneyline})
    for tm,n in x.team.value_counts().items():team_rows.append({'method_id':mid,'team':tm,'n':int(n),'share':float(n/len(x))})
pd.DataFrame(base_rows).to_csv(OUT/'base_summary.csv',index=False);pd.DataFrame(season_rows).to_csv(OUT/'by_season.csv',index=False);pd.DataFrame(week_rows).to_csv(OUT/'by_week.csv',index=False);pd.DataFrame(loss_rows).to_csv(OUT/'losses.csv',index=False);pd.DataFrame(team_rows).to_csv(OUT/'team_concentration.csv',index=False)

# Price windows: choose on train+validation only; then require holdout confirmation.
price_rows=[]
for mid,m in METHODS.items():
    x0=sets[mid];tr,va,ho=tracks[m['track']];bt=met(x0[x0.season.between(*tr)]);bv=met(x0[x0.season.between(*va)]);bh=met(x0[x0.season.between(*ho)]);basepre=(bt['roi']+bv['roi'])/2;basepren=bt['n']+bv['n'];blo,bhi=bands[m['band']];cands=[]
    lows=sorted(set([blo]+[round(z,2) for z in np.arange(blo+.01,bhi-.04+.001,.01)]));highs=sorted(set([bhi]+[round(z,2) for z in np.arange(blo+.05,bhi-.01+.001,.01)]))
    for lo in lows:
      for hi in highs:
        if hi-lo<.05 or lo<blo or hi>bhi:continue
        z=base_set(m,lo,hi);a=met(z[z.season.between(*tr)]);b=met(z[z.season.between(*va)]);h=met(z[z.season.between(*ho)]);pren=a['n']+b['n']
        if a['n']<max(12,int(.45*bt['n'])) or b['n']<max(8,int(.45*bv['n'])) or pren<.60*basepren:continue
        if min(a['roi'],b['roi'])<.04:continue
        cands.append(((a['roi']+b['roi'])/2+.02*pren/basepren,lo,hi,a,b,h))
    final_lo,final_hi=blo,bhi;dec='KEEP_ORIGINAL';selected=None
    if cands:
        cands.sort(key=lambda z:z[0],reverse=True);selected=cands[0];_,lo,hi,a,b,h=selected
        if (a['roi']+b['roi'])/2>=basepre+.03:
            if h['n']>=max(10,int(.45*bh['n'])) and h['roi']>=bh['roi']-.02:
                final_lo,final_hi=lo,hi;dec='TIGHTEN_CONFIRMED'
            else:dec='REVERT_TO_ORIGINAL_AFTER_HOLDOUT_REJECT'
    price_rows.append({'method_id':mid,'final_p_lo':final_lo,'final_p_hi':final_hi,'final_odds':odds_label(final_lo,final_hi),'decision':dec,'base_holdout_roi':bh['roi'],'selected_holdout_roi':selected[5]['roi'] if selected else bh['roi']})
price=pd.DataFrame(price_rows);price.to_csv(OUT/'price_audit.csv',index=False)

# Veto candidates selected on train+validation only. Timing and feature vetoes compete; multi-veto stacks up to 3 allowed.
exclude={'season','week','win','profit','moneyline','market_prob','prior_games','game_id','team','opponent','is_home'}
kw=['coachq_','opp_coachq_','adv_','rank_edge_','recent_edge_','travel','fatigue','rest','qb_prior_starts','returning_','unavail','injur','continuity','tz_shift','road_miles','dome_game','grass_surface','base_vs_open_prob_move']
features=[c for c in d.columns if c not in exclude and pd.api.types.is_numeric_dtype(d[c]) and any(k in c.lower() for k in kw) and num(d[c]).notna().sum()>=500]
features=sorted(features,key=lambda c:num(d[c]).notna().sum(),reverse=True)[:130]

def riskmask(x,c):
    if c['kind']=='TIMING':return num(x.week).between(c['w1'],c['w2'])
    return cmask(x,c['feature'],c['op'],c['threshold'])
def apply_stack(x,stack):
    keep=pd.Series(True,index=x.index)
    for c in stack:keep &= ~riskmask(x,c)
    return x[keep]

cand_rows=[];final_rows=[];stack_rows=[]
for mid,m in METHODS.items():
    x=sets[mid];tr,va,ho=tracks[m['track']];train=x[x.season.between(*tr)];val=x[x.season.between(*va)];hold=x[x.season.between(*ho)];bt,bv,bh=met(train),met(val),met(hold);basepre=(bt['roi']+bv['roi'])/2;basepren=bt['n']+bv['n'];cand=[]
    for c in features:
        tv=num(train[c]).dropna()
        if len(tv)<20:continue
        for q in [.2,.35,.5,.65,.8]:
            t=float(tv.quantile(q))
            for op in ['>=','<=']:
                rm1=cmask(train,c,op,t);rm2=cmask(val,c,op,t);rt,rv=train[rm1],val[rm2];st,sv=train[~rm1],val[~rm2]
                if len(rt)<5 or len(rv)<4 or len(st)<10 or len(sv)<8:continue
                a,b,sa,sb=met(rt),met(rv),met(st),met(sv);tl=(1-a['win_pct'])-(1-bt['win_pct']);vl=(1-b['win_pct'])-(1-bv['win_pct'])
                if tl>=.07 and vl>=.07 and a['roi']<=bt['roi']-.05 and b['roi']<=bv['roi']-.05 and sa['roi']>=bt['roi']-.02 and sb['roi']>=bv['roi']-.02:
                    cand.append({'kind':'FEATURE','feature':c,'op':op,'threshold':t,'pre_score':(sa['roi']+sb['roi'])/2-basepre})
    for label,w1,w2 in [('W4_6',4,6),('W7_10',7,10),('W11_14',11,14),('W15_18',15,18)]:
        rm1=num(train.week).between(w1,w2);rm2=num(val.week).between(w1,w2);rt,rv=train[rm1],val[rm2];st,sv=train[~rm1],val[~rm2]
        if len(rt)>=5 and len(rv)>=4 and len(st)>=10 and len(sv)>=8:
            a,b,sa,sb=met(rt),met(rv),met(st),met(sv);tl=(1-a['win_pct'])-(1-bt['win_pct']);vl=(1-b['win_pct'])-(1-bv['win_pct'])
            if tl>=.07 and vl>=.07 and a['roi']<=bt['roi']-.05 and b['roi']<=bv['roi']-.05 and sa['roi']>=bt['roi']-.02 and sb['roi']>=bv['roi']-.02:
                cand.append({'kind':'TIMING','feature':label,'op':'IN','threshold':f'{w1}-{w2}','w1':w1,'w2':w2,'pre_score':(sa['roi']+sb['roi'])/2-basepre})
    cand=sorted(cand,key=lambda z:z['pre_score'],reverse=True)[:14]
    for c in cand:cand_rows.append({'method_id':mid,**c})
    chosen=[];chosen_pre=basepre;accepted_depth=0;frozen_candidates=[]
    for depth in [1,2,3]:
        depthbest=None
        for combo in itertools.combinations(cand,depth):
            a,b=apply_stack(train,combo),apply_stack(val,combo);ma,mb=met(a),met(b);pren=len(a)+len(b)
            if len(a)<max(12,int(.50*len(train))) or len(b)<max(8,int(.50*len(val))) or pren<.58*basepren:continue
            if ma['roi']<bt['roi']-.02 or mb['roi']<bv['roi']-.02:continue
            pre=(ma['roi']+mb['roi'])/2;impr=pre-basepre
            if impr<(.04+.025*(depth-1)):continue
            score=pre-.02*(depth-1)+.02*(pren/basepren)
            if depthbest is None or score>depthbest[0]:depthbest=(score,combo,ma,mb,pre)
        if depthbest:frozen_candidates.append((depth,depthbest))
    # Open holdout only after one candidate per depth is frozen.
    best_final=None
    for depth,best in frozen_candidates:
        _,combo,ma,mb,pre=best;hz=apply_stack(hold,combo);mh=met(hz);fullz=apply_stack(x,combo);mf=met(fullz)
        confirm=mh['n']>=max(10,int(.50*bh['n'])) and mh['roi']>=bh['roi']+.02 and mf['n']>=max(55,int(.55*len(x)))
        stack_rows.append({'method_id':mid,'depth':depth,'vetoes':' | '.join(f"{c['kind']}:{c['feature']} {c['op']} {c['threshold']}" for c in combo),'pre_roi':pre,'holdout_n':mh['n'],'holdout_roi':mh['roi'],'full_n':mf['n'],'full_win_pct':mf['win_pct'],'full_roi':mf['roi'],'confirmed':confirm})
        if confirm and (best_final is None or mh['roi']>best_final[0]+.015):best_final=(mh['roi'],combo,mf,mh,pre)
    if best_final:
        _,combo,mf,mh,pre=best_final;veto_text=' | '.join(f"{c['kind']}:{c['feature']} {c['op']} {c['threshold']}" for c in combo);status='SHADOW_READY';vc=len(combo)
    else:
        combo=[];mf=met(x);mh=bh;pre=basepre;veto_text='';vc=0;status='SHADOW_READY' if (mf['n']>=85 and mh['roi']>=.10) else 'WATCH'
    ss=season_stats(apply_stack(x,combo) if combo else x);pr=price[price.method_id.eq(mid)].iloc[0]
    final_rows.append({'method_id':mid,'concept':m['concept'],'base_record':f"{int(x.win.sum())}-{int(len(x)-x.win.sum())}",'base_win_pct':float(x.win.mean()),'base_roi':float(x.profit.mean()),'base_holdout_roi':bh['roi'],'final_record':f"{mf['wins']}-{mf['losses']}",'final_win_pct':mf['win_pct'],'final_roi':mf['roi'],'final_holdout_n':mh['n'],'final_holdout_win_pct':mh['win_pct'],'final_holdout_roi':mh['roi'],'positive_season_ratio':ss['positive_season_ratio'],'veto_count':vc,'vetoes':veto_text,'final_odds':pr.final_odds,'price_decision':pr.decision,'status':status})

pd.DataFrame(cand_rows).to_csv(OUT/'veto_candidates.csv',index=False);pd.DataFrame(stack_rows).to_csv(OUT/'stack_audit.csv',index=False);final=pd.DataFrame(final_rows);final.to_csv(OUT/'final_sieve.csv',index=False)
summary={'methods':len(final),'shadow_ready':int((final.status=='SHADOW_READY').sum()),'watch':int((final.status=='WATCH').sum()),'multi_veto_accepted':int((final.veto_count>1).sum()),'single_veto_accepted':int((final.veto_count==1).sum()),'no_veto':int((final.veto_count==0).sum())}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2));lines=['NFL STREAK PRIORITY DEEP DISSECTION','',json.dumps(summary,indent=2),'','FINAL']
for _,r in final.sort_values(['status','final_roi'],ascending=[True,False]).iterrows():lines.append(f"{r.method_id} {r.concept} | base {r.base_record} win={100*r.base_win_pct:.1f}% ROI={100*r.base_roi:+.1f}% hold={100*r.base_holdout_roi:+.1f}% | final {r.final_record} win={100*r.final_win_pct:.1f}% ROI={100*r.final_roi:+.1f}% hold n={int(r.final_holdout_n)} win={100*r.final_holdout_win_pct:.1f}% ROI={100*r.final_holdout_roi:+.1f}% pos={100*r.positive_season_ratio:.0f}% | vetoes={int(r.veto_count)} [{r.vetoes}] | odds={r.final_odds} {r.price_decision} | {r.status}")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
