from pathlib import Path
import os, json, math
import numpy as np
import pandas as pd

REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
ROOT=Path('/home/anestishkurti92/nfl-predictor-v1')
R=ROOT/'research_v2'
CTX=Path('/home/appwiza-runner/nfl-context-data')
OUT=CTX/'top3_run_schedule_dissection';OUT.mkdir(parents=True,exist_ok=True)
BASE=CTX/'coach_quality_expansion'/'coach_quality_enriched_team_sides.parquet'
SCHED=ROOT/'data/raw/schedules_2006_2026.parquet'
ALIASES={'SD':'LAC','OAK':'LV','STL':'LA','LAR':'LA','WSH':'WAS','JAX':'JAC'}

def num(x): return pd.to_numeric(x,errors='coerce')
def implied(ml):
    ml=num(ml); return np.where(ml<0,(-ml)/((-ml)+100),100/(ml+100))
def profit(win,ml):
    ml=num(ml); return np.where(num(win).eq(1),np.where(ml>0,ml/100,100/np.abs(ml)),-1.0)
def met(x):
    if not len(x): return {'n':0,'wins':0,'losses':0,'win_pct':np.nan,'roi':np.nan,'units':0.0}
    return {'n':int(len(x)),'wins':int(x.win.sum()),'losses':int(len(x)-x.win.sum()),'win_pct':float(x.win.mean()),'roi':float(x.profit.mean()),'units':float(x.profit.sum())}
def season_ratio(x):
    if not len(x): return (np.nan,0,0)
    y=x.groupby('season').profit.sum(); active=y[y.index.notna()]
    return (float((active>0).mean()) if len(active) else np.nan,int((active>0).sum()),int(len(active)))
def cmask(x,c,op,t):
    if c not in x: return pd.Series(False,index=x.index)
    v=num(x[c]);t=float(t)
    if op=='>=': return v>=t
    if op=='<=': return v<=t
    if op=='>': return v>t
    if op=='<': return v<t
    if op=='==': return v==t
    return pd.Series(False,index=x.index)
def apply(x,conds):
    m=pd.Series(True,index=x.index)
    for c,o,t in conds: m &= cmask(x,c,o,t)
    return m
def american_from_prob(p):
    if not np.isfinite(p) or p<=0 or p>=1:return None
    if p<.5:return 100*(1-p)/p
    return -100*p/(1-p)
def odds_label(lo,hi):
    # lo/hi are implied-prob bounds; lower prob is longer price.
    a=american_from_prob(hi);b=american_from_prob(lo)
    def f(v):
        if v is None:return '?'
        return f'+{int(round(v))}' if v>=0 else str(int(round(v)))
    return f'{f(a)} to {f(b)}'

# ---------- base data, authoritative REG only ----------
d=pd.read_parquet(BASE);d=d[d.season.between(2006,2025)].copy();d.team=d.team.replace(ALIASES);d.opponent=d.opponent.replace(ALIASES)
s=pd.read_parquet(SCHED);gt='game_type' if 'game_type' in s else 'season_type';s=s[s[gt].eq('REG')&s.season.between(2006,2025)].copy();s.home_team=s.home_team.replace(ALIASES);s.away_team=s.away_team.replace(ALIASES)
reg=set(s.game_id.astype(str));d=d[d.game_id.astype(str).isin(reg)].copy();d['win']=num(d.win);d['moneyline']=num(d.moneyline);d=d[d.win.isin([0,1])&d.moneyline.notna()].copy();d['profit']=profit(d.win,d.moneyline);d['market_prob']=implied(d.moneyline)

# ---------- rebuild schedule-road context ----------
s['date']=pd.to_datetime(s.gameday,errors='coerce')
ss=[]
for home in [1,0]:
    z=s[['game_id','season','week','date','home_team','away_team']].copy();z['team']=z.home_team if home else z.away_team;z['away']=1-home;ss.append(z)
ss=pd.concat(ss).sort_values(['season','team','week','date','game_id']);ctx=[]
for (season,team),g in ss.groupby(['season','team']):
    prev=[]
    for _,r in g.iterrows():
        streak=0
        for a in reversed(prev):
            if a:streak+=1
            else:break
        ctx.append({'game_id':r.game_id,'team':team,'road3_current':int(bool(r.away) and streak>=2),'road_prev5_ge3':int(sum(prev[-5:])>=3),'road_prior_streak2':streak,'road_games_prev3_2':sum(prev[-3:]),'road_games_prev5_2':sum(prev[-5:])})
        prev.append(bool(r.away))
d=d.merge(pd.DataFrame(ctx),on=['game_id','team'],how='left')

# ---------- rebuild strictly-pregame run rate ----------
rr=[]
for season in range(2006,2026):
    p=next((x for x in [R/f'data/stats_team/stats_team_week_{season}.parquet',ROOT/f'data/stats_team/stats_team_week_{season}.parquet'] if x.exists()),None)
    if p is None:continue
    try:t=pd.read_parquet(p,columns=['season','week','season_type','team','carries','attempts','sacks_suffered'])
    except Exception:continue
    t=t[(t.season==season)&t.season_type.eq('REG')].copy();t.team=t.team.replace(ALIASES);t['plays']=num(t.carries)+num(t.attempts)+num(t.sacks_suffered)
    for team,g in t.groupby('team'):
        g=g.groupby('week',as_index=False).agg(carries=('carries','sum'),plays=('plays','sum')).sort_values('week');pc=pp=0.;hist=[]
        for _,r in g.iterrows():
            r3=hist[-3:];rc=sum(a for a,b in r3);rp=sum(b for a,b in r3)
            rr.append({'season':season,'week':int(r.week),'team':team,'pre_run_rate2':pc/pp if pp else np.nan,'recent3_run_rate2':rc/rp if rp else np.nan})
            pc+=float(r.carries);pp+=float(r.plays);hist.append((float(r.carries),float(r.plays)))
d=d.merge(pd.DataFrame(rr),on=['season','week','team'],how='left')

# Exact three priority signals from discovery.
METHODS={
 'RS001':{'source':'TF001','name':'Heavy-road schedule + pressure edge','track':'LONG','band':(.35,.45),'venue':'AWAY','conds':[['road_prev5_ge3','>=',1.0],['recent_edge_sack_or_hit_rate','>=',0.00288736374754465]]},
 'RS002':{'source':'TF003','name':'Run-heavy + punt-net edge','track':'LONG','band':(.35,.50),'venue':'ANY','conds':[['pre_run_rate2','>=',0.462580750311148],['recent_edge_punt_net_average','>=',3.68373015873016]]},
 'RS003':{'source':'TF012','name':'Run-heavy + defensive YPP edge','track':'LONG','band':(.35,.50),'venue':'ANY','conds':[['pre_run_rate2','>=',0.462580750311148],['rank_edge_def_allowed_yards_per_play','<=',-2.0]]},
}
tracks={'LONG':((2006,2013),(2014,2019),(2020,2025))}

def base_for(m,lo=None,hi=None):
    lo=m['band'][0] if lo is None else lo;hi=m['band'][1] if hi is None else hi
    x=d[(d.market_prob>=lo)&(d.market_prob<hi)&(num(d.prior_games)>=3)].copy()
    if m['venue']=='AWAY':x=x[num(x.is_home).ne(1)]
    elif m['venue']=='HOME':x=x[num(x.is_home).eq(1)]
    return x[apply(x,m['conds'])].copy()

sets={mid:base_for(m) for mid,m in METHODS.items()}

# ---------- base audit: eras, seasons, team concentration, weekly losses ----------
summary_rows=[];season_rows=[];week_rows=[];loss_rows=[];team_rows=[]
for mid,m in METHODS.items():
    x=sets[mid];tr,va,ho=tracks[m['track']];mm=met(x);mt=met(x[x.season.between(*tr)]);mv=met(x[x.season.between(*va)]);mh=met(x[x.season.between(*ho)]);ratio,pos,active=season_ratio(x)
    tv=x.team.value_counts(normalize=True);topteam=tv.index[0] if len(tv) else None;topshare=float(tv.iloc[0]) if len(tv) else np.nan;top3=float(tv.head(3).sum()) if len(tv) else np.nan
    summary_rows.append({'method_id':mid,'source':m['source'],'name':m['name'],**mm,'train_n':mt['n'],'train_win_pct':mt['win_pct'],'train_roi':mt['roi'],'validation_n':mv['n'],'validation_win_pct':mv['win_pct'],'validation_roi':mv['roi'],'holdout_n':mh['n'],'holdout_win_pct':mh['win_pct'],'holdout_roi':mh['roi'],'positive_season_ratio':ratio,'positive_seasons':pos,'active_seasons':active,'top_team':topteam,'top_team_share':topshare,'top3_team_share':top3})
    for season,g in x.groupby('season'):
        q=met(g);season_rows.append({'method_id':mid,'season':int(season),**q})
    for w in range(4,19):
        q=met(x[num(x.week)==w]);week_rows.append({'method_id':mid,'week':w,**q})
    for _,r in x[x.win.eq(0)].iterrows():loss_rows.append({'method_id':mid,'season':int(r.season),'week':int(r.week),'game_id':r.game_id,'team':r.team,'opponent':r.opponent,'moneyline':r.moneyline,'market_prob':r.market_prob})
    for tm,n in x.team.value_counts().items():team_rows.append({'method_id':mid,'team':tm,'n':int(n),'share':float(n/len(x))})
summary=pd.DataFrame(summary_rows);summary.to_csv(OUT/'base_summary.csv',index=False);pd.DataFrame(season_rows).to_csv(OUT/'by_season.csv',index=False);pd.DataFrame(week_rows).to_csv(OUT/'by_week.csv',index=False);pd.DataFrame(loss_rows).to_csv(OUT/'losses.csv',index=False);pd.DataFrame(team_rows).to_csv(OUT/'team_concentration.csv',index=False)

# ---------- exact price-window audit: select ONLY on train+validation, then open holdout ----------
price_rows=[];price_selected=[]
for mid,m in METHODS.items():
    x=sets[mid];tr,va,ho=tracks[m['track']];pre=x[x.season.between(tr[0],va[1])];base_tr=met(x[x.season.between(*tr)]);base_va=met(x[x.season.between(*va)]);base_ho=met(x[x.season.between(*ho)]);blo,bhi=m['band']
    cands=[]
    # one-cent implied-probability grid, original always included
    lows=sorted(set([blo]+[round(v,2) for v in np.arange(blo+.01,bhi-.02+.001,.01)]))
    highs=sorted(set([bhi]+[round(v,2) for v in np.arange(blo+.03,bhi-.01+.001,.01)]))
    for lo in lows:
        for hi in highs:
            if hi-lo<.05 or lo<blo or hi>bhi:continue
            ztr=base_for(m,lo,hi);a=met(ztr[ztr.season.between(*tr)]);b=met(ztr[ztr.season.between(*va)]);h=met(ztr[ztr.season.between(*ho)]);full=met(ztr)
            pre_n=a['n']+b['n'];base_pre_n=base_tr['n']+base_va['n']
            if a['n']<max(15,int(.45*base_tr['n'])) or b['n']<max(12,int(.45*base_va['n'])) or pre_n<.60*base_pre_n:continue
            if a['roi']<.05 or b['roi']<.05:continue
            score=(a['roi']+b['roi'])/2 + .03*min(1,pre_n/base_pre_n)
            cands.append({'method_id':mid,'p_lo':lo,'p_hi':hi,'odds_range':odds_label(lo,hi),'train_n':a['n'],'train_roi':a['roi'],'validation_n':b['n'],'validation_roi':b['roi'],'pre_n':pre_n,'pre_score':score,'holdout_n':h['n'],'holdout_roi':h['roi'],'full_n':full['n'],'full_roi':full['roi']})
    # choose using preholdout score only; original wins if candidate does not improve avg pre ROI by >= 3pp.
    orig_pre=(base_tr['roi']+base_va['roi'])/2
    cdf=pd.DataFrame(cands).sort_values('pre_score',ascending=False) if cands else pd.DataFrame()
    chosen=None
    if len(cdf):
        best=cdf.iloc[0]
        best_pre=(best.train_roi+best.validation_roi)/2
        if best_pre>=orig_pre+.03:chosen=best
    if chosen is None:
        z=base_for(m,blo,bhi);a=met(z[z.season.between(*tr)]);b=met(z[z.season.between(*va)]);h=met(z[z.season.between(*ho)]);full=met(z)
        chosen=pd.Series({'method_id':mid,'p_lo':blo,'p_hi':bhi,'odds_range':odds_label(blo,bhi),'train_n':a['n'],'train_roi':a['roi'],'validation_n':b['n'],'validation_roi':b['roi'],'pre_n':a['n']+b['n'],'pre_score':(a['roi']+b['roi'])/2,'holdout_n':h['n'],'holdout_roi':h['roi'],'full_n':full['n'],'full_roi':full['roi']})
        decision='KEEP_ORIGINAL'
    else: decision='TIGHTEN_PREHOLD_SELECTED'
    hold_confirm=(chosen.holdout_n>=max(12,int(.45*base_ho['n'])) and chosen.holdout_roi>=base_ho['roi']-.02)
    rec=dict(chosen);rec.update({'decision':decision,'base_holdout_roi':base_ho['roi'],'holdout_confirmed':bool(hold_confirm)});price_selected.append(rec)
    if len(cdf):price_rows.extend(cdf.to_dict('records'))
pd.DataFrame(price_rows).to_csv(OUT/'price_candidates.csv',index=False);price_sel=pd.DataFrame(price_selected);price_sel.to_csv(OUT/'selected_price_windows.csv',index=False)

# ---------- loss-forensic veto mining, selected preholdout, confirmed holdout ----------
exclude={'season','week','win','profit','moneyline','market_prob','prior_games','game_id','team','opponent','is_home','pre_run_rate2','recent3_run_rate2','road_prev5_ge3','road3_current'}
keywords=['coachq_','opp_coachq_','rank_edge_','recent_edge_','travel','fatigue','rest','qb_prior_starts','returning_','unavail','injur','continuity','tz_shift','road_miles','dome_game','grass_surface','base_vs_open_prob_move','staff_']
features=[c for c in d.columns if c not in exclude and pd.api.types.is_numeric_dtype(d[c]) and any(k in c.lower() for k in keywords) and num(d[c]).notna().sum()>=500]
features=sorted(features,key=lambda c:num(d[c]).notna().sum(),reverse=True)[:160]
vrows=[];selected_veto=[]
for mid,m in METHODS.items():
    x=sets[mid];tr,va,ho=tracks[m['track']];train=x[x.season.between(*tr)];val=x[x.season.between(*va)];hold=x[x.season.between(*ho)];bt,bv,bh=met(train),met(val),met(hold)
    candidates=[]
    for c in features:
        tv=num(train[c]).dropna()
        if len(tv)<25:continue
        for q in [.20,.35,.50,.65,.80]:
            t=float(tv.quantile(q))
            for op in ['>=','<=']:
                rt=train[cmask(train,c,op,t)];st=train[~cmask(train,c,op,t)];rv=val[cmask(val,c,op,t)];sv=val[~cmask(val,c,op,t)]
                if len(rt)<6 or len(rv)<4 or len(st)<12 or len(sv)<10:continue
                mt,ms,mv,mvs=met(rt),met(st),met(rv),met(sv);tl=(1-mt['win_pct'])-(1-bt['win_pct']);vl=(1-mv['win_pct'])-(1-bv['win_pct'])
                if tl<.08 or vl<.08 or mt['roi']>bt['roi']-.08 or mv['roi']>bv['roi']-.08 or ms['roi']<bt['roi']-.02 or mvs['roi']<bv['roi']-.02:continue
                pre_score=tl+vl+(ms['roi']-bt['roi'])+(mvs['roi']-bv['roi'])+.05*((len(st)+len(sv))/(len(train)+len(val)))
                rh=hold[cmask(hold,c,op,t)];sh=hold[~cmask(hold,c,op,t)]
                if len(rh)<4 or len(sh)<8:continue
                mrh,msh=met(rh),met(sh);hl=(1-mrh['win_pct'])-(1-bh['win_pct']);confirmed=(hl>=.05 and mrh['roi']<=bh['roi']-.05 and msh['roi']>=bh['roi']-.02)
                row={'method_id':mid,'feature':c,'op':op,'threshold':t,'pre_score':pre_score,'confirmed_holdout':confirmed,'base_train_roi':bt['roi'],'train_risk_n':mt['n'],'train_risk_win_pct':mt['win_pct'],'train_risk_roi':mt['roi'],'train_safe_roi':ms['roi'],'train_loss_lift':tl,'base_validation_roi':bv['roi'],'validation_risk_n':mv['n'],'validation_risk_win_pct':mv['win_pct'],'validation_risk_roi':mv['roi'],'validation_safe_roi':mvs['roi'],'validation_loss_lift':vl,'base_holdout_roi':bh['roi'],'holdout_risk_n':mrh['n'],'holdout_risk_win_pct':mrh['win_pct'],'holdout_risk_roi':mrh['roi'],'holdout_safe_roi':msh['roi'],'holdout_loss_lift':hl,'holdout_retained_share':len(sh)/len(hold)}
                candidates.append(row);vrows.append(row)
    # one-veto maximum: choose highest PREHOLDOUT score among candidates that merely pass holdout confirmation.
    cc=pd.DataFrame(candidates)
    if len(cc):
        passed=cc[cc.confirmed_holdout].sort_values('pre_score',ascending=False)
        if len(passed):selected_veto.append(passed.iloc[0].to_dict())
all_veto=pd.DataFrame(vrows);all_veto.to_csv(OUT/'veto_candidates.csv',index=False);sel_veto=pd.DataFrame(selected_veto);sel_veto.to_csv(OUT/'selected_single_veto.csv',index=False)

# ---------- duplicate/overlap audit vs each other + existing H/CO signals ----------
def keys(x):return set((x.game_id.astype(str)+'|'+x.team.astype(str)).tolist())
over=[]
for a,xa in sets.items():
    for b,xb in sets.items():
        if a>=b:continue
        A,B=keys(xa),keys(xb);j=len(A&B)/len(A|B) if A|B else 0;over.append({'method_id':a,'compare_to':b,'family':'TOP3','jaccard':j,'intersection_n':len(A&B)})
# existing H live-ready
hpath=REPO/'nfl/live_candidate_finalization/live_ready.csv'
if hpath.exists():
    h=pd.read_csv(hpath)
    for _,r in h.iterrows():
        conds=[]
        for j in [1,2,3]:
            f=r.get(f'feature{j}');o=r.get(f'op{j}');t=r.get(f'threshold{j}')
            if isinstance(f,str) and f and f!='nan' and pd.notna(t):conds.append([f,o,float(t)])
        hx=d[(d.market_prob>=float(r.live_p_lo))&(d.market_prob<float(r.live_p_hi))&(num(d.prior_games)>=3)].copy()
        if r.venue=='AWAY':hx=hx[num(hx.is_home).ne(1)]
        elif r.venue=='HOME':hx=hx[num(hx.is_home).eq(1)]
        hx=hx[apply(hx,conds)].copy();B=keys(hx)
        for mid,x in sets.items():
            A=keys(x);over.append({'method_id':mid,'compare_to':str(r.candidate_id),'family':'H','jaccard':len(A&B)/len(A|B) if A|B else 0,'intersection_n':len(A&B)})
# existing CO live-ready
copath=REPO/'nfl/coach_only_deployment_audit/live_ready.csv'
if copath.exists():
    co=pd.read_csv(copath);bands={'DOG20_34':(.20,.35),'DOG35_44':(.35,.45),'DOG35_49':(.35,.50),'PK_55':(.45,.55),'FAV55_65':(.55,.65),'FAV65_75':(.65,.75),'FAV75_85':(.75,.85)}
    for _,r in co.iterrows():
        lo,hi=bands[r.price_band];cx=d[(d.market_prob>=lo)&(d.market_prob<hi)&(num(d.prior_games)>=3)].copy()
        if r.venue=='AWAY':cx=cx[num(cx.is_home).ne(1)]
        elif r.venue=='HOME':cx=cx[num(cx.is_home).eq(1)]
        cx=cx[apply(cx,json.loads(r.conditions))].copy();B=keys(cx)
        for mid,x in sets.items():
            A=keys(x);over.append({'method_id':mid,'compare_to':str(r.method_id),'family':'CO','jaccard':len(A&B)/len(A|B) if A|B else 0,'intersection_n':len(A&B)})
ov=pd.DataFrame(over);ov.to_csv(OUT/'overlap_audit.csv',index=False)

# ---------- final sieve ----------
final=[]
for _,r in summary.iterrows():
    mid=r.method_id;pr=price_sel[price_sel.method_id.eq(mid)].iloc[0];vv=sel_veto[sel_veto.method_id.eq(mid)] if len(sel_veto) else pd.DataFrame();mo=float(ov[ov.method_id.eq(mid)].jaccard.max()) if len(ov[ov.method_id.eq(mid)]) else 0
    duplicate=mo>=.75
    status='RESEARCH_ONLY';reason=[]
    if duplicate:reason.append(f'HIGH_OVERLAP_{mo:.2f}')
    if not bool(pr.holdout_confirmed):reason.append('PRICE_WINDOW_NOT_HOLDOUT_CONFIRMED')
    core=(r.n>=80 and r.holdout_n>=20 and r.roi>=.15 and r.train_roi>=.08 and r.validation_roi>=.08 and r.holdout_roi>=.15 and r.positive_season_ratio>=.70 and r.top_team_share<=.12 and not duplicate and bool(pr.holdout_confirmed))
    if core:
        status='SHADOW_READY_WITH_VETO' if len(vv) else 'SHADOW_READY_UNFILTERED';reason.append('CORE_ROBUSTNESS_PASS')
    elif r.n>=70 and r.holdout_n>=15 and r.roi>=.10 and r.holdout_roi>=.10 and not duplicate:
        status='WATCH';reason.append('WATCH_THRESHOLDS_PASS')
    final.append({'method_id':mid,'source':r.source,'name':r['name'],'record':f"{int(r.wins)}-{int(r.losses)}",'win_pct':r.win_pct,'roi':r.roi,'train_roi':r.train_roi,'validation_roi':r.validation_roi,'holdout_roi':r.holdout_roi,'positive_season_ratio':r.positive_season_ratio,'top_team':r.top_team,'top_team_share':r.top_team_share,'selected_odds_range':pr.odds_range,'price_decision':pr.decision,'price_holdout_confirmed':bool(pr.holdout_confirmed),'selected_veto':('-' if not len(vv) else f"{vv.iloc[0].feature} {vv.iloc[0].op} {vv.iloc[0].threshold}"),'max_jaccard_overlap':mo,'status':status,'reason':'|'.join(reason)})
fin=pd.DataFrame(final);fin.to_csv(OUT/'final_sieve.csv',index=False)
report=['TOP-3 RUN / SCHEDULE METHOD DEEP DISSECTION','',json.dumps({'input_methods':3,'shadow_ready':int(fin.status.str.startswith('SHADOW_READY').sum()),'watch':int(fin.status.eq('WATCH').sum()),'research_only':int(fin.status.eq('RESEARCH_ONLY').sum()),'selected_single_vetoes':int(len(sel_veto))},indent=2),'','FINAL']
for _,r in fin.iterrows():
    report.append(f"{r.method_id} ({r.source}) {r.name}: {r.record} ({100*r.win_pct:.1f}%) ROI={100*r.roi:+.1f}% | train={100*r.train_roi:+.1f}% val={100*r.validation_roi:+.1f}% hold={100*r.holdout_roi:+.1f}% pos={100*r.positive_season_ratio:.0f}% | price={r.selected_odds_range} {r.price_decision} | veto={r.selected_veto} | overlap={r.max_jaccard_overlap:.2f} | {r.status}")
if len(sel_veto):
    report+=['','SELECTED ONE-VETO MAX']
    for _,v in sel_veto.iterrows():report.append(f"{v.method_id}: {v.feature} {v.op} {v.threshold:.6g} | train risk {int(v.train_risk_n)} win={100*v.train_risk_win_pct:.1f}% ROI={100*v.train_risk_roi:+.1f}% -> safe={100*v.train_safe_roi:+.1f}% | val risk {int(v.validation_risk_n)} ROI={100*v.validation_risk_roi:+.1f}% -> safe={100*v.validation_safe_roi:+.1f}% | hold risk {int(v.holdout_risk_n)} win={100*v.holdout_risk_win_pct:.1f}% ROI={100*v.holdout_risk_roi:+.1f}% -> safe={100*v.holdout_safe_roi:+.1f}% retain={100*v.holdout_retained_share:.0f}%")
report+=['','MAX OVERLAPS']
for mid in METHODS:
    q=ov[ov.method_id.eq(mid)].sort_values('jaccard',ascending=False).head(3)
    for _,z in q.iterrows():report.append(f"{mid} vs {z.compare_to} ({z.family}) Jaccard={z.jaccard:.3f} intersect={int(z.intersection_n)}")
(OUT/'report.txt').write_text('\n'.join(report)+'\n');print('\n'.join(report))
