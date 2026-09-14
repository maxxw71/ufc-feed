from pathlib import Path
import os,runpy,json
import numpy as np
import pandas as pd

REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
CTX=Path('/home/appwiza-runner/nfl-context-data')
OUT=CTX/'top3_run_schedule_final_review';OUT.mkdir(parents=True,exist_ok=True)
# Re-run the canonical dissection so this review always uses the exact same reconstructed bet sets.
g=runpy.run_path(str(REPO/'scripts/nfl_top3_run_schedule_dissection.py'))
sets=g['sets'];METHODS=g['METHODS'];tracks=g['tracks'];met=g['met'];cmask=g['cmask'];season_ratio=g['season_ratio'];odds_label=g['odds_label'];summary=g['summary'];price_sel=g['price_sel'];all_veto=g['all_veto'];ov=g['ov']

# Price discipline: a narrower window can be selected pre-holdout, but if the holdout does not confirm it,
# revert to the original frozen discovery band instead of demoting an otherwise robust method.
price_final=[]
for mid,m in METHODS.items():
    p=price_sel[price_sel.method_id.eq(mid)].iloc[0]
    if str(p.decision).startswith('TIGHTEN') and not bool(p.holdout_confirmed):
        lo,hi=m['band'];price_final.append({'method_id':mid,'p_lo':lo,'p_hi':hi,'odds_range':odds_label(lo,hi),'decision':'REVERT_TO_ORIGINAL_AFTER_HOLDOUT_REJECT','narrow_candidate':p.odds_range,'narrow_holdout_roi':p.holdout_roi,'base_holdout_roi':p.base_holdout_roi})
    else:
        price_final.append({'method_id':mid,'p_lo':p.p_lo,'p_hi':p.p_hi,'odds_range':p.odds_range,'decision':p.decision,'narrow_candidate':p.odds_range,'narrow_holdout_roi':p.holdout_roi,'base_holdout_roi':p.base_holdout_roi})
pf=pd.DataFrame(price_final);pf.to_csv(OUT/'final_price_windows.csv',index=False)

# One-veto maximum. Candidate threshold was created from train; it must have also shown the loss profile in validation,
# and now must confirm in holdout. Require enough risk observations and preserve >=65% of holdout bets.
sv=[]
if len(all_veto):
    for mid in METHODS:
        q=all_veto[(all_veto.method_id.eq(mid))&(all_veto.confirmed_holdout.eq(True))].copy()
        if not len(q):continue
        q['pre_risk_n']=q.train_risk_n+q.validation_risk_n
        q=q[(q.pre_risk_n>=16)&(q.holdout_risk_n>=5)&(q.holdout_retained_share>=.65)]
        # do not choose the filter by the prettiest holdout; choose highest pre-holdout score among filters that only PASS holdout confirmation.
        if len(q):sv.append(q.sort_values('pre_score',ascending=False).iloc[0].to_dict())
sv=pd.DataFrame(sv);sv.to_csv(OUT/'final_single_veto.csv',index=False)

# Recompute each method AFTER the one allowed veto, using the broad/final price policy.
rows=[];weeks=[];seasons=[];losses=[]
for mid,m in METHODS.items():
    x=sets[mid].copy();base=met(x);v=sv[sv.method_id.eq(mid)] if len(sv) else pd.DataFrame()
    veto_desc='-'
    if len(v):
        r=v.iloc[0];x=x[~cmask(x,r.feature,r.op,float(r.threshold))].copy();veto_desc=f'{r.feature} {r.op} {r.threshold}'
    tr,va,ho=tracks[m['track']];mt=met(x[x.season.between(*tr)]);mv=met(x[x.season.between(*va)]);mh=met(x[x.season.between(*ho)]);mf=met(x);ratio,pos,active=season_ratio(x)
    tc=x.team.value_counts(normalize=True);top=float(tc.iloc[0]) if len(tc) else np.nan;topteam=tc.index[0] if len(tc) else None;maxov=float(ov[ov.method_id.eq(mid)].jaccard.max()) if len(ov[ov.method_id.eq(mid)]) else 0
    p=pf[pf.method_id.eq(mid)].iloc[0]
    # status is deliberately shadow-only; no public email/website activation here.
    robust=(base['n']>=80 and mf['n']>=60 and mh['n']>=15 and mf['roi']>=.15 and mt['roi']>=.08 and mv['roi']>=.08 and mh['roi']>=.15 and ratio>=.70 and top<=.12 and maxov<.75)
    status='SHADOW_READY' if robust else 'WATCH'
    rows.append({'method_id':mid,'source':m['source'],'name':m['name'],'base_n':base['n'],'base_wins':base['wins'],'base_win_pct':base['win_pct'],'base_roi':base['roi'],'filtered_n':mf['n'],'filtered_wins':mf['wins'],'filtered_losses':mf['losses'],'filtered_win_pct':mf['win_pct'],'filtered_roi':mf['roi'],'train_n':mt['n'],'train_roi':mt['roi'],'validation_n':mv['n'],'validation_roi':mv['roi'],'holdout_n':mh['n'],'holdout_win_pct':mh['win_pct'],'holdout_roi':mh['roi'],'positive_season_ratio':ratio,'positive_seasons':pos,'active_seasons':active,'top_team':topteam,'top_team_share':top,'max_overlap':maxov,'final_odds_range':p.odds_range,'price_decision':p.decision,'single_veto':veto_desc,'status':status})
    for w in range(4,19):
        z=x[pd.to_numeric(x.week,errors='coerce').eq(w)];mm=met(z);weeks.append({'method_id':mid,'week':w,**mm})
    for season,z in x.groupby('season'):
        mm=met(z);seasons.append({'method_id':mid,'season':int(season),**mm})
    for _,r0 in x[x.win.eq(0)].iterrows():losses.append({'method_id':mid,'season':int(r0.season),'week':int(r0.week),'game_id':r0.game_id,'team':r0.team,'opponent':r0.opponent,'moneyline':r0.moneyline})
fin=pd.DataFrame(rows);fin.to_csv(OUT/'final_methods.csv',index=False);pd.DataFrame(weeks).to_csv(OUT/'filtered_by_week.csv',index=False);pd.DataFrame(seasons).to_csv(OUT/'filtered_by_season.csv',index=False);pd.DataFrame(losses).to_csv(OUT/'remaining_losses.csv',index=False)

# Staging manifest only; fail closed and do not publish to email/site/bet tracker.
manifest={'mode':'SHADOW_ONLY','public_website_enabled':False,'email_enabled':False,'bet_tracker_enabled':False,'global_guards':['REG only','selected team prior_games >= 3','current actionable moneyline required','final price band required','all signal conditions required','at most one validated veto','fail closed on missing required feature'],'methods':[]}
for _,r in fin[fin.status.eq('SHADOW_READY')].iterrows():
    manifest['methods'].append({'method_id':r.method_id,'source':r.source,'name':r['name'],'odds_range':r.final_odds_range,'conditions':METHODS[r.method_id]['conds'],'single_veto':None if r.single_veto=='-' else r.single_veto,'historical_record':f"{int(r.filtered_wins)}-{int(r.filtered_losses)}",'historical_win_pct':float(r.filtered_win_pct),'historical_roi':float(r.filtered_roi),'holdout_roi':float(r.holdout_roi)})
(OUT/'shadow_manifest.json').write_text(json.dumps(manifest,indent=2))

lines=['TOP-3 RUN / SCHEDULE STRICT FINAL REVIEW','',json.dumps({'shadow_ready':int(fin.status.eq('SHADOW_READY').sum()),'watch':int(fin.status.eq('WATCH').sum()),'one_veto_count':int(len(sv)),'public_live':False},indent=2),'','FINAL METHODS']
for _,r in fin.iterrows():
    lines.append(f"{r.method_id} {r['name']} | BASE {int(r.base_wins)}-{int(r.base_n-r.base_wins)} win={100*r.base_win_pct:.1f}% ROI={100*r.base_roi:+.1f}% | FINAL {int(r.filtered_wins)}-{int(r.filtered_losses)} win={100*r.filtered_win_pct:.1f}% ROI={100*r.filtered_roi:+.1f}% | train={100*r.train_roi:+.1f}% val={100*r.validation_roi:+.1f}% hold={100*r.holdout_roi:+.1f}% pos={100*r.positive_season_ratio:.0f}% | odds {r.final_odds_range} ({r.price_decision}) | veto={r.single_veto} | {r.status}")
lines+=['','PRICE AUDIT']
for _,r in pf.iterrows():lines.append(f"{r.method_id}: {r.decision}; final {r.odds_range}; rejected candidate {r.narrow_candidate} hold={100*r.narrow_holdout_roi:+.1f}% vs base={100*r.base_holdout_roi:+.1f}%")
if len(sv):
    lines+=['','ONE-VETO MAX']
    for _,r in sv.iterrows():lines.append(f"{r.method_id}: {r.feature} {r.op} {r.threshold:.6g}; pre-risk n={int(r.train_risk_n+r.validation_risk_n)}; hold risk n={int(r.holdout_risk_n)} ROI={100*r.holdout_risk_roi:+.1f}% -> safe={100*r.holdout_safe_roi:+.1f}%; retain={100*r.holdout_retained_share:.0f}%")
(OUT/'report.txt').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
