from pathlib import Path
import os,re,runpy,json,itertools
import numpy as np,pandas as pd

REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
ROOT=Path('/home/anestishkurti92/nfl-predictor-v1')
CTX=Path('/home/appwiza-runner/nfl-context-data')
OUT=CTX/'live_legacy_full_dissection';OUT.mkdir(parents=True,exist_ok=True)
PUB=Path('/srv/appwiza-sports/public/nfl/index.html')
CONTEXT=CTX/'derived/team_game_pregame_context_2006_2026.parquet'
PRE=CTX/'raw/preseason_team_2006_2026.csv'

# Reuse exact live-rule reconstruction so this audit cannot silently drift from the scanner.
ns=runpy.run_path(str(REPO/'scripts/nfl_audit_current_live_legacy_methods.py'))
bets=ns['bets'].copy(); rich=ns['d'].copy(); metrics0=ns['metrics']
num=lambda x:pd.to_numeric(x,errors='coerce')

def met(x):
    if not len(x):return dict(n=0,wins=0,losses=0,win_pct=np.nan,roi=np.nan,units=0.)
    return dict(n=int(len(x)),wins=int(x.win.sum()),losses=int(len(x)-x.win.sum()),win_pct=float(x.win.mean()),roi=float(x.profit_units.mean()),units=float(x.profit_units.sum()))

def season_stats(x):
    if not len(x):return dict(positive_seasons=0,active_seasons=0,positive_ratio=np.nan,max_share=np.nan)
    y=x.groupby('season').agg(n=('win','size'),u=('profit_units','sum'))
    return dict(positive_seasons=int((y.u>0).sum()),active_seasons=int(len(y)),positive_ratio=float((y.u>0).mean()),max_share=float(y.n.max()/len(x)))

def cmask(x,c,o,t):
    if c not in x:return pd.Series(False,index=x.index)
    v=num(x[c]);t=float(t)
    return {'>=':v>=t,'<=':v<=t}.get(o,pd.Series(False,index=x.index))

# Only current home-opener family here; secondary method is audited elsewhere.
bets=bets[bets.method.isin(['original','stricter'])].copy()
core={'method','game_id','season','week','team','opponent','moneyline','win','profit_units','record_gap','run_def_rank'}
rf=rich.sort_values(['season','week']).drop_duplicates(['game_id','team'])
featcols=[c for c in rf.columns if c not in core and c not in {'opponent','season','week','moneyline','win','profit_units'}]
joined=bets.merge(rf[['game_id','team']+featcols],on=['game_id','team'],how='left',suffixes=('','_rich'))

# Add preseason form for selected team and opponent.
if PRE.exists():
    p=pd.read_csv(PRE);p['season']=num(p.season)
    keep=['season','team','preseason_wins','preseason_games','preseason_win_pct','preseason_losing_record','preseason_winning_record','preseason_winless']
    p=p[[c for c in keep if c in p.columns]].copy()
    joined=joined.merge(p,on=['season','team'],how='left')
    op=p.rename(columns={c:'opp_'+c for c in p.columns if c not in {'season','team'}}).rename(columns={'team':'opponent'})
    joined=joined.merge(op,on=['season','opponent'],how='left')
    joined['preseason_win_edge']=num(joined.get('preseason_win_pct'))-num(joined.get('opp_preseason_win_pct'))

# Fixed split: selection only before opening the 2020-25 confirmation period.
SPLIT={'train':(2007,2013),'validation':(2014,2019),'holdout':(2020,2025)}
summary=[];price_rows=[];week_rows=[];loss_rows=[];veto_rows=[];stack_rows=[];final_rows=[]

# Pregame-safe enriched features. Weather deliberately excluded per prior decision.
prefixes=('coachq_','opp_coachq_','adv_','rank_','recent_','pre_','prior_','returning_','qb_','out_','travel_','road_','rest','fatigue','tz_','dome_game','grass_surface','base_vs_open_prob_move','preseason_','opp_preseason_')
exclude_frag=('score','result','profit','win','closing','final','temperature','wind','weather')
features=[]
for c in joined.columns:
    if c in core or c in {'market_prob','prior_games'}:continue
    if not any(c.startswith(p) for p in prefixes):continue
    if any(k in c.lower() for k in exclude_frag):continue
    if num(joined[c]).notna().sum()<25:continue
    features.append(c)
features=sorted(set(features),key=lambda c:num(joined[c]).notna().sum(),reverse=True)[:180]

for mid,x in joined.groupby('method'):
    x=x.copy();tr=x[x.season.between(*SPLIT['train'])];va=x[x.season.between(*SPLIT['validation'])];ho=x[x.season.between(*SPLIT['holdout'])]
    bm=met(x);tm=met(tr);vm=met(va);hm=met(ho);ss=season_stats(x)
    summary.append({'method':mid,**bm,'train_n':tm['n'],'train_win_pct':tm['win_pct'],'train_roi':tm['roi'],'validation_n':vm['n'],'validation_win_pct':vm['win_pct'],'validation_roi':vm['roi'],'holdout_n':hm['n'],'holdout_win_pct':hm['win_pct'],'holdout_roi':hm['roi'],**ss})
    for w,g in x.groupby('week'):week_rows.append({'method':mid,'week':int(w),**met(g)})
    for _,r in x[x.win.eq(0)].iterrows():loss_rows.append({'method':mid,'season':int(r.season),'week':int(r.week),'team':r.team,'opponent':r.opponent,'moneyline':r.moneyline,'record_gap':r.record_gap,'run_def_rank':r.run_def_rank,'preseason_wins':r.get('preseason_wins',np.nan),'preseason_win_pct':r.get('preseason_win_pct',np.nan)})

    # Price anatomy first. No post-hoc price gate is promoted merely because a bin looks good.
    bins=[(-10000,-400,'<= -400'),(-400,-300,'-399 to -300'),(-300,-200,'-299 to -200'),(-200,-150,'-199 to -150'),(-150,-110,'-149 to -110'),(-110,0,'-109 to -100'),(0,10000,'underdog')]
    for lo,hi,label in bins:
        z=x[(num(x.moneyline)>=lo)&(num(x.moneyline)<hi)]
        if len(z):price_rows.append({'method':mid,'bucket':label,**met(z),'recent_n':len(z[z.season>=2020]),'recent_roi':met(z[z.season>=2020])['roi']})

    # Train/validation loss forensics across the new context universe.
    cand=[]
    for c in features:
        tv=num(tr[c]).dropna()
        if len(tv)<12:continue
        for q in [.2,.35,.5,.65,.8]:
            t=float(tv.quantile(q))
            for op in ['<=','>=']:
                r1=cmask(tr,c,op,t);r2=cmask(va,c,op,t)
                risk1,risk2=tr[r1],va[r2];safe1,safe2=tr[~r1],va[~r2]
                if len(risk1)<4 or len(risk2)<3 or len(safe1)<max(10,int(.45*len(tr))) or len(safe2)<max(8,int(.45*len(va))):continue
                a,b,sa,sb=met(risk1),met(risk2),met(safe1),met(safe2)
                loss_lift1=(1-a['win_pct'])-(1-tm['win_pct']);loss_lift2=(1-b['win_pct'])-(1-vm['win_pct'])
                imp1=sa['roi']-tm['roi'];imp2=sb['roi']-vm['roi']
                if loss_lift1<.06 or loss_lift2<.06:continue
                if a['roi']>=tm['roi']-.05 or b['roi']>=vm['roi']-.05:continue
                if imp1<-.015 or imp2<-.015:continue
                score=(imp1+imp2)/2+.15*(loss_lift1+loss_lift2)/2
                cand.append({'feature':c,'op':op,'threshold':t,'score':score,'train_risk_n':a['n'],'train_risk_roi':a['roi'],'validation_risk_n':b['n'],'validation_risk_roi':b['roi'],'train_safe_roi':sa['roi'],'validation_safe_roi':sb['roi']})
    # Deduplicate nested thresholds on same feature/direction.
    cc=pd.DataFrame(cand)
    if len(cc):
        cc=cc.sort_values('score',ascending=False).drop_duplicates(['feature','op']).head(16)
        for _,r in cc.iterrows():veto_rows.append({'method':mid,**r.to_dict()})
        cands=cc.to_dict('records')
    else:cands=[]

    # Freeze one candidate per stack depth on train+validation, then open holdout.
    frozen=[];basepre=(tm['roi']+vm['roi'])/2
    for depth in [1,2,3]:
        best=None
        for combo in itertools.combinations(cands[:10],depth):
            if len({z['feature'] for z in combo})<depth:continue
            def keep(df):
                k=pd.Series(True,index=df.index)
                for z in combo:k &= ~cmask(df,z['feature'],z['op'],z['threshold'])
                return df[k]
            st,sv=keep(tr),keep(va);mt,mv=met(st),met(sv)
            if mt['n']<max(10,int(.52*len(tr))) or mv['n']<max(8,int(.52*len(va))):continue
            if mt['roi']<tm['roi']-.01 or mv['roi']<vm['roi']-.01:continue
            pre=(mt['roi']+mv['roi'])/2
            if pre<basepre+.035+.018*(depth-1):continue
            score=pre-.012*(depth-1)+.01*((len(st)+len(sv))/(len(tr)+len(va)))
            if best is None or score>best[0]:best=(score,combo,mt,mv,pre)
        if best:frozen.append((depth,best))
    accepted=[]
    for depth,best in frozen:
        _,combo,mt,mv,pre=best
        def keep(df):
            k=pd.Series(True,index=df.index)
            for z in combo:k &= ~cmask(df,z['feature'],z['op'],z['threshold'])
            return df[k]
        zh=keep(ho);mh=met(zh);zf=keep(x);mf=met(zf)
        confirmed=mh['n']>=max(8,int(.50*len(ho))) and mh['roi']>=hm['roi']+.015 and mf['n']>=max(35,int(.55*len(x)))
        txt=' | '.join(f"{z['feature']} {z['op']} {z['threshold']:.6g}" for z in combo)
        stack_rows.append({'method':mid,'depth':depth,'vetoes':txt,'pre_roi':pre,'holdout_n':mh['n'],'holdout_win_pct':mh['win_pct'],'holdout_roi':mh['roi'],'full_n':mf['n'],'full_win_pct':mf['win_pct'],'full_roi':mf['roi'],'confirmed':confirmed})
        if confirmed:accepted.append((mh['roi'],depth,txt,mf,mh))
    if accepted:
        accepted.sort(reverse=True,key=lambda z:(z[0],-z[1]));_,depth,txt,mf,mh=accepted[0]
        final_rows.append({'method':mid,'decision':'ENRICHED_VETO_CONFIRMED','veto_count':depth,'vetoes':txt,'final_n':mf['n'],'final_wins':mf['wins'],'final_losses':mf['losses'],'final_win_pct':mf['win_pct'],'final_roi':mf['roi'],'final_holdout_n':mh['n'],'final_holdout_win_pct':mh['win_pct'],'final_holdout_roi':mh['roi']})
    else:
        final_rows.append({'method':mid,'decision':'NO_NEW_ENRICHED_STACK_CONFIRMED','veto_count':0,'vetoes':'','final_n':bm['n'],'final_wins':bm['wins'],'final_losses':bm['losses'],'final_win_pct':bm['win_pct'],'final_roi':bm['roi'],'final_holdout_n':hm['n'],'final_holdout_win_pct':hm['win_pct'],'final_holdout_roi':hm['roi']})

pd.DataFrame(summary).to_csv(OUT/'method_summary.csv',index=False)
pd.DataFrame(price_rows).to_csv(OUT/'price_buckets.csv',index=False)
pd.DataFrame(week_rows).to_csv(OUT/'week_breakdown.csv',index=False)
pd.DataFrame(loss_rows).to_csv(OUT/'losses.csv',index=False)
pd.DataFrame(veto_rows).to_csv(OUT/'veto_candidates_preholdout.csv',index=False)
pd.DataFrame(stack_rows).to_csv(OUT/'frozen_stack_holdout.csv',index=False)
pd.DataFrame(final_rows).to_csv(OUT/'final_enriched_decision.csv',index=False)

# Current board: parse the public cards, then attach 2026 preseason and context facts.
htmltxt=PUB.read_text(errors='ignore') if PUB.exists() else ''
name_to_code={'Saints':'NO','Titans':'TEN','Ravens':'BAL','Chargers':'LAC','Eagles':'PHI','Buccaneers':'TB','Browns':'CLE','Raiders':'LV','Cardinals':'ARI','Commanders':'WAS','Vikings':'MIN','Packers':'GB'}
current=[]
# Restrict to the current-board region, before completed results.
front=htmltxt.split('Tracked results')[0]
for art in re.findall(r'<article class="card">(.*?)</article>',front,re.S):
    mm=re.search(r'<h2 class="pick">([^<]+) to win</h2>',art)
    if not mm:continue
    nm=re.sub('<.*?>','',mm.group(1)).strip();team=name_to_code.get(nm)
    pm=re.search(r'<div class="quote"><strong>([-+]?\d+(?:\.\d+)?)</strong>',art)
    wm=re.search(r'NFL · Week (\d+)',art)
    if not team:continue
    current.append({'team':team,'selection':nm,'moneyline':float(pm.group(1)) if pm else np.nan,'week':int(wm.group(1)) if wm else np.nan})
cur=pd.DataFrame(current).drop_duplicates('team') if current else pd.DataFrame(columns=['team','selection','moneyline','week'])
if len(cur):
    # 2026 preseason.
    if PRE.exists():
        p=pd.read_csv(PRE);p=p[num(p.season).eq(2026)][['team','preseason_wins','preseason_games','preseason_win_pct','preseason_record_equiv']]
        cur=cur.merge(p,on='team',how='left')
    # Map each to its 2026 first home game and context row.
    sch=pd.read_parquet(ROOT/'data/raw/schedules_2006_2026.parquet');gt='game_type' if 'game_type' in sch else 'season_type';sch=sch[(sch.season==2026)&sch[gt].eq('REG')].sort_values(['week','game_id'])
    openers=sch.drop_duplicates('home_team').rename(columns={'home_team':'team','away_team':'opponent'})[['game_id','team','opponent','week']]
    cur=cur.drop(columns=['week'],errors='ignore').merge(openers,on='team',how='left')
    if CONTEXT.exists():
        ctx=pd.read_parquet(CONTEXT);cx=ctx[(ctx.season==2026)].drop_duplicates(['game_id','team'])
        cols=['game_id','team']+[c for c in ['head_coach_changed','offensive_coordinator_changed','defensive_coordinator_changed','qb_changed_from_prior_season','returning_offense_snap_share','returning_defense_snap_share','returning_ol_snap_share','returning_skill_snap_share','travel_miles','rest','preseason_form_disadvantage'] if c in cx.columns]
        cur=cur.merge(cx[cols],on=['game_id','team'],how='left')
    # Base-method identity from known live board criteria, copied from page evidence when possible.
    for i,r in cur.iterrows():
        art=next((a for a in re.findall(r'<article class="card">(.*?)</article>',front,re.S) if f'>{r.selection} to win<' in a), '')
        rg=re.search(r'Winning-percentage advantage:\s*([\d.]+)',art)
        rr=re.search(r'run defense:\s*#([\d.]+)',art)
        cur.loc[i,'record_gap_pp']=float(rg.group(1)) if rg else np.nan
        cur.loc[i,'run_def_rank']=float(rr.group(1)) if rr else np.nan
        cur.loc[i,'method']='stricter' if pd.notna(cur.loc[i,'record_gap_pp']) and cur.loc[i,'record_gap_pp']>12.5 else 'original'
        cur.loc[i,'preseason_policy_pass']=bool(pd.notna(r.get('preseason_wins')) and float(r.get('preseason_wins'))>=2)
        # Historical bucket for exact current price.
        pb=pd.DataFrame(price_rows);sub=pb[pb.method.eq(cur.loc[i,'method'])]
        ml=float(r.moneyline)
        label='<= -400' if ml<-400 else '-399 to -300' if ml<-300 else '-299 to -200' if ml<-200 else '-199 to -150' if ml<-150 else '-149 to -110' if ml<-110 else '-109 to -100' if ml<0 else 'underdog'
        z=sub[sub.bucket.eq(label)]
        if len(z):
            cur.loc[i,'historical_price_bucket']=label;cur.loc[i,'price_bucket_n']=int(z.iloc[0].n);cur.loc[i,'price_bucket_win_pct']=float(z.iloc[0].win_pct);cur.loc[i,'price_bucket_roi']=float(z.iloc[0].roi);cur.loc[i,'price_bucket_recent_roi']=float(z.iloc[0].recent_roi)
    cur.to_csv(OUT/'current_board_deep_context.csv',index=False)

# Preseason-filter historical reference from already stress-tested policy.
pre_ref={}
prejson=REPO/'nfl/legacy_live_filter_recommendations.json'
if prejson.exists():pre_ref=json.loads(prejson.read_text())

report=['NFL LIVE HOME-OPENER FULL DISSECTION','',f"historical_bets={len(bets)} enriched_features_screened={len(features)}",'REG only. Weather excluded. Veto candidates selected on 2007-2019 train/validation; 2020-25 opened only after stack freeze.','']
for r in summary:
    report.append(f"BASE {r['method']}: {r['wins']}-{r['losses']} win={100*r['win_pct']:.1f}% ROI={100*r['roi']:+.1f}% | train {100*r['train_roi']:+.1f}% val {100*r['validation_roi']:+.1f}% hold {100*r['holdout_roi']:+.1f}% | pos seasons {r['positive_seasons']}/{r['active_seasons']}")
report+=['','ENRICHED FINAL DECISION']
for r in final_rows:
    report.append(f"{r['method']}: {r['decision']} | final {r['final_wins']}-{r['final_losses']} win={100*r['final_win_pct']:.1f}% ROI={100*r['final_roi']:+.1f}% hold={100*r['final_holdout_roi']:+.1f}% | {r['vetoes']}")
report+=['','PRICE BUCKETS']
for r in price_rows:report.append(f"{r['method']} {r['bucket']}: n={r['n']} {r['wins']}-{r['losses']} win={100*r['win_pct']:.1f}% ROI={100*r['roi']:+.1f}% recent={100*r['recent_roi']:+.1f}%")
if pre_ref:
    z=pre_ref.get('preferred_legacy_home_opener',{});report+=['','PRESEASON STRESS-TEST REFERENCE',f"stricter + preseason wins>=2: {z.get('record')} win={100*z.get('win_pct',0):.1f}% ROI={100*z.get('roi',0):+.1f}% | recent 2020-25 ROI={100*z.get('era_results',{}).get('2020_2025',{}).get('roi',0):+.1f}%"]
if len(cur):
    report+=['','CURRENT BOARD']
    for _,r in cur.iterrows():
        report.append(f"{r.team} ({r.selection}) W{int(r.week) if pd.notna(r.week) else '?'} ML={r.moneyline:+.0f} method={r.method} | preseason={r.get('preseason_record_equiv','?')} pass={r.get('preseason_policy_pass')} | price bucket {r.get('historical_price_bucket','?')} n={r.get('price_bucket_n',np.nan)} win={100*r.get('price_bucket_win_pct',np.nan):.1f}% ROI={100*r.get('price_bucket_roi',np.nan):+.1f}% recent={100*r.get('price_bucket_recent_roi',np.nan):+.1f}%")
(OUT/'report.txt').write_text('\n'.join(report)+'\n')
print('\n'.join(report))
