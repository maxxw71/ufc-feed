from pathlib import Path
import os,json,itertools,math,re
import numpy as np
import pandas as pd

REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
ROOT=Path('/home/anestishkurti92/nfl-predictor-v1')
CTX=Path('/home/appwiza-runner/nfl-context-data')
OUT=CTX/'unique_situational_deep_dissection';OUT.mkdir(parents=True,exist_ok=True)
DATA=CTX/'coach_quality_expansion'/'coach_quality_enriched_team_sides.parquet'
PREF=REPO/'nfl/unique_situational_discovery/preferred.csv'
SCHED=ROOT/'data/raw/schedules_2006_2026.parquet'
HFILE=REPO/'nfl/live_candidate_finalization/live_ready.csv'
COBASE=REPO/'nfl/coach_only_deployment_audit/live_ready.csv'
COFINAL=REPO/'nfl/coach_only_final_sieve/final_coach_only_arsenal_sieve.csv'
RSFILE=REPO/'nfl/top3_timing_final_audit/shadow_manifest.json'

def num(x):return pd.to_numeric(x,errors='coerce')
def implied(ml):
    ml=num(ml);return np.where(ml<0,(-ml)/((-ml)+100),100/(ml+100))
def profit(win,ml):
    ml=num(ml);return np.where(num(win).eq(1),np.where(ml>0,ml/100,100/np.abs(ml)),-1.)
def met(x):
    if not len(x):return dict(n=0,wins=0,losses=0,win_pct=np.nan,roi=np.nan,units=0.)
    return dict(n=int(len(x)),wins=int(x.win.sum()),losses=int(len(x)-x.win.sum()),win_pct=float(x.win.mean()),roi=float(x.profit.mean()),units=float(x.profit.sum()))
def season_stats(x):
    if not len(x):return dict(positive_season_ratio=np.nan,positive_seasons=0,active_seasons=0,max_season_share=np.nan)
    y=x.groupby('season').agg(n=('win','size'),units=('profit','sum'));return dict(positive_season_ratio=float((y.units>0).mean()),positive_seasons=int((y.units>0).sum()),active_seasons=int(len(y)),max_season_share=float(y.n.max()/len(x)))
def cmask(x,c,o,t):
    if c not in x:return pd.Series(False,index=x.index)
    v=num(x[c]);t=float(t)
    if o=='>=':return v>=t
    if o=='<=':return v<=t
    if o=='>':return v>t
    if o=='<':return v<t
    if o=='==':return v==t
    return pd.Series(False,index=x.index)
def applyconds(x,conds):
    m=pd.Series(True,index=x.index)
    for c,o,t in conds:m &= cmask(x,c,o,t)
    return m
def odds_from_p(p):
    if p<.5:return 100*(1-p)/p
    return -100*p/(1-p)
def odds_label(lo,hi):
    vals=[odds_from_p(hi),odds_from_p(lo)]
    return ' to '.join((f'+{int(round(v))}' if v>=0 else str(int(round(v)))) for v in vals)
def parse_veto(v):
    if not isinstance(v,str) or not v:return None
    q=re.match(r'(.+?)\s*(>=|<=|==|>|<)\s*([-0-9.eE]+)$',v.strip())
    return (q.group(1),q.group(2),float(q.group(3))) if q else None

def keyset(x):return set((x.game_id.astype(str)+'|'+x.team.astype(str)).tolist())

bands={'DOG20_34':(.20,.35),'DOG35_44':(.35,.45),'DOG35_49':(.35,.50),'PK_55':(.45,.55),'FAV55_65':(.55,.65),'FAV65_75':(.65,.75),'FAV75_85':(.75,.85)}
tracks={'LONG':((2006,2013),(2014,2019),(2020,2025)),'MODERN':((2012,2017),(2018,2020),(2021,2025))}

# REG-only base universe
d=pd.read_parquet(DATA);d=d[d.season.between(2006,2025)].copy();d['win']=num(d.win);d['moneyline']=num(d.moneyline);d['profit']=profit(d.win,d.moneyline);d['market_prob']=implied(d.moneyline)
s=pd.read_parquet(SCHED);gt='game_type' if 'game_type' in s else 'season_type';s=s[s[gt].eq('REG')&s.season.between(2006,2025)].copy();reg=set(s.game_id.astype(str));d=d[d.game_id.astype(str).isin(reg)&d.win.isin([0,1])&d.moneyline.notna()].copy()
pref=pd.read_csv(PREF).reset_index(drop=True);pref['method_id']=[f'US{i+1:03d}' for i in range(len(pref))]

# reconstruct preferred methods
sets={};meta={}
for _,r in pref.iterrows():
    lo,hi=bands[r.band];x=d[(d.market_prob>=lo)&(d.market_prob<hi)&(num(d.prior_games)>=3)].copy()
    if r.venue=='AWAY':x=x[num(x.is_home).ne(1)]
    elif r.venue=='HOME':x=x[num(x.is_home).eq(1)]
    cond=json.loads(r.conditions);x=x[applyconds(x,cond)].copy();sets[r.method_id]=x
    meta[r.method_id]={'track':r.track,'band':r.band,'venue':r.venue,'concept':r.concept,'conditions':cond,'lo':lo,'hi':hi}

# base audits
base_rows=[];season_rows=[];week_rows=[];loss_rows=[];team_rows=[]
for mid,x in sets.items():
    m=meta[mid];tr,va,ho=tracks[m['track']];a=met(x[x.season.between(*tr)]);b=met(x[x.season.between(*va)]);h=met(x[x.season.between(*ho)]);f=met(x);ss=season_stats(x);tv=x.team.value_counts(normalize=True)
    base_rows.append({'method_id':mid,**m,**f,'train_n':a['n'],'train_win_pct':a['win_pct'],'train_roi':a['roi'],'validation_n':b['n'],'validation_win_pct':b['win_pct'],'validation_roi':b['roi'],'holdout_n':h['n'],'holdout_win_pct':h['win_pct'],'holdout_roi':h['roi'],**ss,'top_team':tv.index[0] if len(tv) else None,'top_team_share':float(tv.iloc[0]) if len(tv) else np.nan,'top3_team_share':float(tv.head(3).sum()) if len(tv) else np.nan})
    for season,g in x.groupby('season'):
        q=met(g);season_rows.append({'method_id':mid,'season':int(season),**q})
    for w in range(4,19):
        q=met(x[num(x.week)==w]);week_rows.append({'method_id':mid,'week':w,**q})
    for _,r0 in x[x.win.eq(0)].iterrows():loss_rows.append({'method_id':mid,'season':int(r0.season),'week':int(r0.week),'game_id':r0.game_id,'team':r0.team,'opponent':r0.opponent,'moneyline':r0.moneyline})
    for tm,n in x.team.value_counts().items():team_rows.append({'method_id':mid,'team':tm,'n':int(n),'share':float(n/len(x))})
base=pd.DataFrame(base_rows);base.to_csv(OUT/'base_summary.csv',index=False);pd.DataFrame(season_rows).to_csv(OUT/'by_season.csv',index=False);pd.DataFrame(week_rows).to_csv(OUT/'by_week.csv',index=False);pd.DataFrame(loss_rows).to_csv(OUT/'losses.csv',index=False);pd.DataFrame(team_rows).to_csv(OUT/'team_concentration.csv',index=False)

# exact price-window audit, freeze on train+validation only
price=[]
for mid,x0 in sets.items():
    m=meta[mid];tr,va,ho=tracks[m['track']];bt=met(x0[x0.season.between(*tr)]);bv=met(x0[x0.season.between(*va)]);bh=met(x0[x0.season.between(*ho)]);basepre=(bt['roi']+bv['roi'])/2;basepren=bt['n']+bv['n']
    candidates=[]
    for lo in sorted(set([m['lo']]+[round(z,2) for z in np.arange(m['lo']+.01,m['hi']-.04+.001,.01)])):
      for hi in sorted(set([m['hi']]+[round(z,2) for z in np.arange(m['lo']+.05,m['hi']-.01+.001,.01)])):
        if hi-lo<.05 or lo<m['lo'] or hi>m['hi']:continue
        z=x0[(x0.market_prob>=lo)&(x0.market_prob<hi)];a=met(z[z.season.between(*tr)]);b=met(z[z.season.between(*va)]);h=met(z[z.season.between(*ho)]);f=met(z)
        pren=a['n']+b['n']
        if a['n']<max(12,int(.45*bt['n'])) or b['n']<max(8,int(.45*bv['n'])) or pren<.60*basepren:continue
        if min(a['roi'],b['roi'])<.04:continue
        score=(a['roi']+b['roi'])/2+.02*(pren/basepren)
        candidates.append((score,lo,hi,a,b,h,f))
    chosen=None
    if candidates:
        candidates.sort(key=lambda z:z[0],reverse=True);best=candidates[0];bestpre=(best[3]['roi']+best[4]['roi'])/2
        if bestpre>=basepre+.03:chosen=best
    if chosen:
        _,lo,hi,a,b,h,f=chosen;confirmed=h['n']>=max(10,int(.45*bh['n'])) and h['roi']>=bh['roi']-.02
        if confirmed:dec='TIGHTEN_CONFIRMED';final_lo,final_hi=lo,hi
        else:dec='REVERT_TO_ORIGINAL_AFTER_HOLDOUT_REJECT';final_lo,final_hi=m['lo'],m['hi']
    else:
        lo,hi=m['lo'],m['hi'];a,b,h,f=bt,bv,bh,met(x0);confirmed=True;dec='KEEP_ORIGINAL';final_lo,final_hi=lo,hi
    price.append({'method_id':mid,'selected_p_lo':lo,'selected_p_hi':hi,'selected_odds':odds_label(lo,hi),'selected_holdout_n':h['n'],'selected_holdout_roi':h['roi'],'base_holdout_roi':bh['roi'],'holdout_confirmed':confirmed,'decision':dec,'final_p_lo':final_lo,'final_p_hi':final_hi,'final_odds':odds_label(final_lo,final_hi)})
price=pd.DataFrame(price);price.to_csv(OUT/'price_audit.csv',index=False)

# pre-holdout veto candidates + timing candidates. Thresholds are selected without holdout.
exclude={'season','week','win','profit','moneyline','market_prob','prior_games','game_id','team','opponent','is_home'}
kw=['coachq_','opp_coachq_','adv_','rank_edge_','recent_edge_','travel','fatigue','rest','qb_prior_starts','returning_','unavail','injur','continuity','tz_shift','road_miles','dome_game','grass_surface','base_vs_open_prob_move']
features=[c for c in d.columns if c not in exclude and pd.api.types.is_numeric_dtype(d[c]) and any(k in c.lower() for k in kw) and num(d[c]).notna().sum()>=500]
features=sorted(features,key=lambda c:num(d[c]).notna().sum(),reverse=True)[:150]
vcands={};vrows=[]
for mid,x in sets.items():
    m=meta[mid];tr,va,ho=tracks[m['track']];train=x[x.season.between(*tr)];val=x[x.season.between(*va)];hold=x[x.season.between(*ho)];bt,bv,bh=met(train),met(val),met(hold);cand=[]
    for c in features:
        tv=num(train[c]).dropna()
        if len(tv)<20:continue
        for q in [.20,.35,.50,.65,.80]:
            t=float(tv.quantile(q))
            for op in ['>=','<=']:
                rt=train[cmask(train,c,op,t)];rv=val[cmask(val,c,op,t)];st=train[~cmask(train,c,op,t)];sv=val[~cmask(val,c,op,t)]
                if len(rt)<5 or len(rv)<4 or len(st)<10 or len(sv)<8:continue
                a,b,sa,sb=met(rt),met(rv),met(st),met(sv);tl=(1-a['win_pct'])-(1-bt['win_pct']);vl=(1-b['win_pct'])-(1-bv['win_pct'])
                if tl<.07 or vl<.07 or a['roi']>bt['roi']-.06 or b['roi']>bv['roi']-.06:continue
                if sa['roi']<bt['roi']-.02 or sb['roi']<bv['roi']-.02:continue
                prescore=(sa['roi']+sb['roi'])/2-(bt['roi']+bv['roi'])/2
                cand.append({'kind':'FEATURE','feature':c,'op':op,'threshold':t,'pre_score':prescore})
    # timing can also be a veto; broad windows only
    for label,w1,w2 in [('W4_6',4,6),('W7_10',7,10),('W11_14',11,14),('W15_18',15,18)]:
        rt=train[num(train.week).between(w1,w2)];rv=val[num(val.week).between(w1,w2)];st=train[~num(train.week).between(w1,w2)];sv=val[~num(val.week).between(w1,w2)]
        if len(rt)>=5 and len(rv)>=4 and len(st)>=10 and len(sv)>=8:
            a,b,sa,sb=met(rt),met(rv),met(st),met(sv);tl=(1-a['win_pct'])-(1-bt['win_pct']);vl=(1-b['win_pct'])-(1-bv['win_pct'])
            if tl>=.07 and vl>=.07 and a['roi']<=bt['roi']-.06 and b['roi']<=bv['roi']-.06 and sa['roi']>=bt['roi']-.02 and sb['roi']>=bv['roi']-.02:
                cand.append({'kind':'TIMING','feature':label,'op':'IN','threshold':f'{w1}-{w2}','pre_score':(sa['roi']+sb['roi'])/2-(bt['roi']+bv['roi'])/2,'w1':w1,'w2':w2})
    cand=sorted(cand,key=lambda z:z['pre_score'],reverse=True)[:18];vcands[mid]=cand
    for c in cand:vrows.append({'method_id':mid,**c})
pd.DataFrame(vrows).to_csv(OUT/'preholdout_veto_candidates.csv',index=False)

def riskmask(x,c):
    if c['kind']=='TIMING':return num(x.week).between(int(c['w1']),int(c['w2']))
    return cmask(x,c['feature'],c['op'],c['threshold'])
def apply_stack(x,stack):
    keep=pd.Series(True,index=x.index)
    for c in stack:keep &= ~riskmask(x,c)
    return x[keep]

# complexity-regularized multi-veto optimizer, max depth 3, selected on train/validation only, then frozen holdout check.
stack_rows=[];final=[]
for mid,x0 in sets.items():
    m=meta[mid];tr,va,ho=tracks[m['track']];train=x0[x0.season.between(*tr)];val=x0[x0.season.between(*va)];hold=x0[x0.season.between(*ho)];bt,bv,bh=met(train),met(val),met(hold);basepre=(bt['roi']+bv['roi'])/2;basepren=bt['n']+bv['n'];best=None
    cand=vcands[mid][:12]
    for depth in [1,2,3]:
      for combo in itertools.combinations(cand,depth):
        a=apply_stack(train,combo);b=apply_stack(val,combo);ma,mb=met(a),met(b);pren=len(a)+len(b)
        if len(a)<max(12,int(.50*len(train))) or len(b)<max(8,int(.50*len(val))) or pren<.58*basepren:continue
        if ma['roi']<bt['roi']-.02 or mb['roi']<bv['roi']-.02:continue
        pre=(ma['roi']+mb['roi'])/2;improve=pre-basepre
        min_imp=.05+.03*(depth-1)
        if improve<min_imp:continue
        score=pre-.025*(depth-1)+.02*(pren/basepren)
        stack_rows.append({'method_id':mid,'depth':depth,'features':' | '.join(f"{c['kind']}:{c['feature']} {c['op']} {c['threshold']}" for c in combo),'pre_roi':pre,'pre_improvement':improve,'pre_retained':pren/basepren,'score':score})
        if best is None or score>best[0]:best=(score,combo,ma,mb)
    if best:
        score,combo,ma,mb=best;hz=apply_stack(hold,combo);fz=apply_stack(x0,combo);mh,mf=met(hz),met(fz);ret=len(hz)/len(hold) if len(hold) else 0
        # full frozen stack must improve holdout and retain enough. Otherwise use base method unchanged.
        accepted=(len(hz)>=max(10,int(.50*len(hold))) and ret>=.50 and mh['roi']>=bh['roi']+.02 and mf['n']>=60)
        if accepted:
            stack=list(combo);status='STACK_ACCEPTED';fx=fz
        else:
            stack=[];status='STACK_REJECTED_USE_BASE';fx=x0
    else:
        stack=[];status='NO_STACK_SELECTED';fx=x0;mh=bh;mf=met(x0);ret=1.0
    ff=met(fx);fss=season_stats(fx);holdf=met(fx[fx.season.between(*ho)]);final.append({'method_id':mid,'stack_status':status,'veto_count':len(stack),'vetoes':' | '.join(f"{c['kind']}:{c['feature']} {c['op']} {c['threshold']}" for c in stack),'filtered_n':ff['n'],'filtered_wins':ff['wins'],'filtered_losses':ff['losses'],'filtered_win_pct':ff['win_pct'],'filtered_roi':ff['roi'],'filtered_holdout_n':holdf['n'],'filtered_holdout_win_pct':holdf['win_pct'],'filtered_holdout_roi':holdf['roi'],**fss})
pd.DataFrame(stack_rows).to_csv(OUT/'stack_candidates.csv',index=False);final=pd.DataFrame(final)

# reconstruct existing arsenal sets for overlap check (H, CO, RS); conservative duplicate guard.
existing={}
if HFILE.exists():
    h=pd.read_csv(HFILE)
    for _,r in h.iterrows():
        x=d[(d.market_prob>=float(r.live_p_lo))&(d.market_prob<float(r.live_p_hi))&(num(d.prior_games)>=3)].copy()
        if r.venue=='AWAY':x=x[num(x.is_home).ne(1)]
        elif r.venue=='HOME':x=x[num(x.is_home).eq(1)]
        cond=[]
        for j in [1,2,3]:
            f=r.get(f'feature{j}');o=r.get(f'op{j}');t=r.get(f'threshold{j}')
            if isinstance(f,str) and f and f!='nan' and pd.notna(t):cond.append([f,o,float(t)])
        x=x[applyconds(x,cond)].copy();v=parse_veto(f"{r.get('hard_veto_feature','')} {r.get('hard_veto_op','')} {r.get('hard_veto_threshold','')}")
        if v:x=x[~cmask(x,*v)].copy()
        existing[str(r.candidate_id)]=x
if COBASE.exists() and COFINAL.exists():
    cb=pd.read_csv(COBASE);cf=pd.read_csv(COFINAL)
    for _,r in cb.iterrows():
        q=cf[cf.method_id.eq(r.method_id)]
        if q.empty:continue
        lo,hi=bands[r.price_band];x=d[(d.market_prob>=lo)&(d.market_prob<hi)&(num(d.prior_games)>=3)].copy()
        if r.venue=='AWAY':x=x[num(x.is_home).ne(1)]
        elif r.venue=='HOME':x=x[num(x.is_home).eq(1)]
        x=x[applyconds(x,json.loads(r.conditions))].copy();z=q.iloc[0]
        if pd.notna(z.veto_feature):x=x[~cmask(x,z.veto_feature,z.veto_op,z.veto_threshold)].copy()
        existing[str(r.method_id)]=x
# RS sets are approximated from final shadow manifest without rebuilding custom run-rate feature names if absent; skip conditions unavailable.
if RSFILE.exists():
    rs=json.loads(RSFILE.read_text())
    for m in rs['methods']:
        lohi={'RS001':(.35,.45),'RS002':(.35,.50),'RS003':(.35,.50)}[m['method_id']]
        x=d[(d.market_prob>=lohi[0])&(d.market_prob<lohi[1])&(num(d.prior_games)>=3)].copy()
        if m['method_id']=='RS001':x=x[num(x.is_home).ne(1)]
        if all(c[0] in x.columns for c in m['conditions']):
            x=x[applyconds(x,m['conditions'])].copy();v=parse_veto(m.get('single_veto',''))
            if v:x=x[~cmask(x,*v)].copy();existing[m['method_id']]=x

over=[];overmax={}
for mid,x in sets.items():
    ks=keyset(x);mx=0.;bestname=''
    for name,z in existing.items():
        kz=keyset(z);inter=len(ks&kz);union=len(ks|kz);j=inter/union if union else 0
        over.append({'method_id':mid,'existing_method':name,'intersection':inter,'jaccard':j})
        if j>mx:mx=j;bestname=name
    overmax[mid]=(mx,bestname)
pd.DataFrame(over).to_csv(OUT/'overlap_audit.csv',index=False)

# final sieve uses accepted stack or base, plus final price decision as a deployment gate. Price and stack were independently selected preholdout.
rows=[]
for _,b in base.iterrows():
    mid=b.method_id;f=final[final.method_id.eq(mid)].iloc[0];p=price[price.method_id.eq(mid)].iloc[0];ov,on=overmax.get(mid,(0,''))
    # status is shadow-ready only if base/filtered robustness remains strong; price can revert safely.
    sample_ok=f.filtered_n>=70 and f.filtered_holdout_n>=15
    robust_ok=f.filtered_roi>=.10 and f.filtered_holdout_roi>=.10 and f.positive_season_ratio>=.70 and b.top_team_share<=.15
    duplicate=ov>=.70
    status='SHADOW_READY' if sample_ok and robust_ok and not duplicate else 'WATCH'
    reason=[]
    if not sample_ok:reason.append('SAMPLE')
    if not robust_ok:reason.append('ROBUSTNESS')
    if duplicate:reason.append('DUPLICATE')
    rows.append({'method_id':mid,'concept':b.concept,'base_record':f"{int(b.wins)}-{int(b.losses)}",'base_win_pct':b.win_pct,'base_roi':b.roi,'base_holdout_roi':b.holdout_roi,'final_record':f"{int(f.filtered_wins)}-{int(f.filtered_losses)}",'final_win_pct':f.filtered_win_pct,'final_roi':f.filtered_roi,'final_holdout_roi':f.filtered_holdout_roi,'positive_season_ratio':f.positive_season_ratio,'veto_count':int(f.veto_count),'vetoes':f.vetoes,'stack_status':f.stack_status,'final_odds':p.final_odds,'price_decision':p.decision,'max_jaccard_overlap':ov,'max_overlap_method':on,'status':status,'reason':'|'.join(reason)})
final_sieve=pd.DataFrame(rows).sort_values(['status','final_holdout_roi','final_roi'],ascending=[True,False,False]);final_sieve.to_csv(OUT/'final_sieve.csv',index=False)
summary={'input_methods':len(pref),'shadow_ready':int((final_sieve.status=='SHADOW_READY').sum()),'watch':int((final_sieve.status=='WATCH').sum()),'multi_veto_accepted':int(((final.veto_count>=2)&final.stack_status.eq('STACK_ACCEPTED')).sum()),'single_veto_accepted':int(((final.veto_count==1)&final.stack_status.eq('STACK_ACCEPTED')).sum()),'no_stack_or_rejected':int((final.veto_count==0).sum())};(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
lines=['NFL UNIQUE SITUATIONAL DEEP DISSECTION','',json.dumps(summary,indent=2),'','FINAL']
for _,r in final_sieve.iterrows():lines.append(f"{r.method_id} {r.concept} | base {r.base_record} win={100*r.base_win_pct:.1f}% ROI={100*r.base_roi:+.1f}% hold={100*r.base_holdout_roi:+.1f}% | final {r.final_record} win={100*r.final_win_pct:.1f}% ROI={100*r.final_roi:+.1f}% hold={100*r.final_holdout_roi:+.1f}% pos={100*r.positive_season_ratio:.0f}% | vetoes={r.veto_count} [{r.vetoes}] | odds={r.final_odds} {r.price_decision} | overlap={r.max_jaccard_overlap:.2f} {r.max_overlap_method} | {r.status}")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
