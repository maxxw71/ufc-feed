from pathlib import Path
import os, json, time
import numpy as np
import pandas as pd
import requests

REPO=Path(os.environ.get('GITHUB_WORKSPACE','.'))
CTX=Path('/home/appwiza-runner/nfl-context-data')
OUT=CTX/'legacy_preseason_staff_filter_audit'; OUT.mkdir(parents=True,exist_ok=True)
ENR=CTX/'coaching_everything'/'coaching_enriched_team_sides_2006_2025.parquet'
ALIASES={'SD':'LAC','OAK':'LV','STL':'LA','LAR':'LA','WSH':'WAS'}

def num(x):return pd.to_numeric(x,errors='coerce')
def met(x):
    if not len(x):return {'n':0,'wins':0,'losses':0,'win_pct':np.nan,'roi':np.nan}
    return {'n':len(x),'wins':int(x.win.sum()),'losses':int(len(x)-x.win.sum()),'win_pct':float(x.win.mean()),'roi':float(x.profit_units.mean())}
def scoreval(x):
    if isinstance(x,dict):x=x.get('value',x.get('displayValue'))
    try:return float(x)
    except:return np.nan

cache=OUT/'espn_preseason_team_seasons.csv'
if cache.exists():
    pre=pd.read_csv(cache)
else:
    games=[]; seen=set()
    for season in range(2007,2027):
        for week in range(1,6):
            url='https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard'
            try:
                r=requests.get(url,params={'dates':season,'seasontype':1,'week':week,'limit':100},timeout=(5,20));r.raise_for_status();obj=r.json()
            except Exception:
                continue
            for ev in obj.get('events',[]):
                eid=str(ev.get('id'))
                if eid in seen:continue
                comps=ev.get('competitions') or []
                if not comps:continue
                c=comps[0];status=((c.get('status') or {}).get('type') or {})
                if not status.get('completed'):continue
                sides={}
                for cp in c.get('competitors',[]):
                    ha=cp.get('homeAway');abbr=((cp.get('team') or {}).get('abbreviation') or '').upper();abbr=ALIASES.get(abbr,abbr)
                    sides[ha]=(abbr,scoreval(cp.get('score')))
                if 'home' not in sides or 'away' not in sides:continue
                ht,hs=sides['home'];at,aas=sides['away']
                if not ht or not at or pd.isna(hs) or pd.isna(aas):continue
                seen.add(eid);games.append({'season':season,'week':week,'event_id':eid,'home_team':ht,'away_team':at,'home_score':hs,'away_score':aas})
            time.sleep(.03)
    gg=pd.DataFrame(games);sides=[]
    for _,g in gg.iterrows():
        sides += [
          {'season':g.season,'team':g.home_team,'win':int(g.home_score>g.away_score),'loss':int(g.home_score<g.away_score),'pf':g.home_score,'pa':g.away_score},
          {'season':g.season,'team':g.away_team,'win':int(g.away_score>g.home_score),'loss':int(g.away_score<g.home_score),'pf':g.away_score,'pa':g.home_score},]
    z=pd.DataFrame(sides)
    pre=z.groupby(['season','team']).agg(pre_games=('win','size'),pre_wins=('win','sum'),pre_losses=('loss','sum'),pre_pf=('pf','sum'),pre_pa=('pa','sum')).reset_index()
    pre['pre_win_pct']=pre.pre_wins/pre.pre_games;pre['pre_margin_pg']=(pre.pre_pf-pre.pre_pa)/pre.pre_games
    pre.to_csv(cache,index=False)

def load_method(path,name):
    d=pd.read_csv(path,low_memory=False);z=pd.DataFrame({'method':name,'season':num(d.season).astype(int),'week':num(d.week).astype(int),'game_id':d.game_id.astype(str),'team':d.home_team.replace(ALIASES),'opponent':d.away_team.replace(ALIASES),'moneyline':num(d.home_moneyline),'win':num(d.win),'profit_units':num(d.profit),'prior_win_pct':num(d.prior_win_pct),'opponent_prior_win_pct':num(d.opponent_prior_win_pct)})
    return z
bets=pd.concat([
 load_method(REPO/'nfl/legacy_live_home_opener_exact/original_bets.csv','original'),
 load_method(REPO/'nfl/legacy_live_home_opener_exact/stricter_bets.csv','stricter')],ignore_index=True)

bets=bets.merge(pre.rename(columns={c:'team_'+c for c in pre.columns if c not in ['season','team']}),on=['season','team'],how='left')
bets=bets.merge(pre.rename(columns={'team':'opponent',**{c:'opp_'+c for c in pre.columns if c not in ['season','team']}}),on=['season','opponent'],how='left')

d=pd.read_parquet(ENR);d=d[d.season.between(2007,2025)].copy();d.team=d.team.replace(ALIASES)
want=[c for c in ['hc_changed_season','oc_changed_season','dc_changed_season','both_coords_changed','staff_change_count','full_staff_overhaul','new_oc_low_off_continuity','new_dc_low_def_continuity','returning_offense_share','returning_defense_share','returning_ol_share','returning_skill_share'] if c in d.columns]
team=d[['game_id','team']+want].drop_duplicates(['game_id','team']).rename(columns={c:'team_'+c for c in want})
opp=d[['game_id','team']+want].drop_duplicates(['game_id','team']).rename(columns={'team':'opponent',**{c:'opp_'+c for c in want}})
bets=bets.merge(team,on=['game_id','team'],how='left').merge(opp,on=['game_id','opponent'],how='left')

def B(c):return num(bets[c]) if c in bets else pd.Series(np.nan,index=bets.index)
flags={}
def add(n,s):flags[n]=s.fillna(False).astype(bool)
add('team_preseason_losing',B('team_pre_win_pct')<.5)
add('team_preseason_1_2_exact',(B('team_pre_games')==3)&(B('team_pre_wins')==1)&(B('team_pre_losses')==2))
add('team_preseason_negative_margin',B('team_pre_margin_pg')<0)
add('team_preseason_record_worse_than_opponent',B('team_pre_win_pct')<B('opp_pre_win_pct'))
add('team_preseason_1win_or_less',B('team_pre_wins')<=1)
for p in ['team','opp']:
    for c in ['hc_changed_season','oc_changed_season','dc_changed_season','both_coords_changed','full_staff_overhaul','new_oc_low_off_continuity','new_dc_low_def_continuity']:
        if f'{p}_{c}' in bets:add(f'{p}_{c}',B(f'{p}_{c}')>=1)
    if f'{p}_staff_change_count' in bets:add(f'{p}_staff_ge2',B(f'{p}_staff_change_count')>=2)
for c in ['returning_offense_share','returning_defense_share','returning_ol_share','returning_skill_share']:
    if 'team_'+c in bets:
        for th in [.50,.60,.70]:add(f'team_{c}_le_{th:.2f}',B('team_'+c)<=th)
def F(n):return flags.get(n,pd.Series(False,index=bets.index))
add('team_both_coords_plus_losing_preseason',F('team_both_coords_changed')&F('team_preseason_losing'))
add('team_oc_dc_plus_1_2_preseason',F('team_oc_changed_season')&F('team_dc_changed_season')&F('team_preseason_1_2_exact'))
add('team_any_coord_plus_losing_preseason',(F('team_oc_changed_season')|F('team_dc_changed_season'))&F('team_preseason_losing'))
add('team_staff_ge2_plus_losing_preseason',F('team_staff_ge2')&F('team_preseason_losing'))
add('team_new_coords_opponent_new_hc',F('team_both_coords_changed')&F('opp_hc_changed_season'))
add('scheme_reset_both_sides',(F('team_oc_changed_season')|F('team_dc_changed_season'))&(F('opp_hc_changed_season')|F('opp_oc_changed_season')|F('opp_dc_changed_season')))
add('opp_new_hc_after_bad_prior_year',F('opp_hc_changed_season')&(B('opponent_prior_win_pct')<=.35))
add('opp_new_staff_after_bad_prior_year',(F('opp_hc_changed_season')|F('opp_oc_changed_season')|F('opp_dc_changed_season'))&(B('opponent_prior_win_pct')<=.35))
add('chargers_like_combo',F('team_oc_changed_season')&F('team_dc_changed_season')&F('team_preseason_losing')&F('opp_hc_changed_season'))

rows=[]
for method,x in bets.groupby('method'):
    idx=x.index;old=x.season<=2019;recent=x.season>=2020;base=met(x);bo=met(x[old]);br=met(x[recent])
    for name,f in flags.items():
        r=f.loc[idx];risk=x[r];safe=x[~r];ro=met(x[old&r]);rr=met(x[recent&r]);so=met(x[old&~r]);sr=met(x[recent&~r])
        if ro['n']<3 or rr['n']<2:continue
        ol=(1-ro['win_pct'])-(1-bo['win_pct']);rl=(1-rr['win_pct'])-(1-br['win_pct'])
        confirm=ol>=.08 and rl>=.08 and so['roi']>=bo['roi']-.02 and sr['roi']>=br['roi']-.02
        rows.append({'method':method,'flag':name,'base_n':base['n'],'base_win_pct':base['win_pct'],'base_roi':base['roi'],'risk_n':risk.shape[0],'risk_win_pct':risk.win.mean(),'risk_roi':risk.profit_units.mean(),'safe_n':safe.shape[0],'safe_win_pct':safe.win.mean(),'safe_roi':safe.profit_units.mean(),'old_risk_n':ro['n'],'old_loss_lift':ol,'old_safe_roi':so['roi'],'recent_risk_n':rr['n'],'recent_loss_lift':rl,'recent_safe_roi':sr['roi'],'confirmed':confirm})
res=pd.DataFrame(rows)
if len(res):
    res['score']=res.confirmed.astype(int)*10+res.old_loss_lift+res.recent_loss_lift+(res.safe_roi-res.base_roi)
    res=res.sort_values(['confirmed','score','risk_n'],ascending=[False,False,False]);res.to_csv(OUT/'filter_results.csv',index=False);res[res.confirmed].to_csv(OUT/'confirmed_filters.csv',index=False)

charg={
 'game':'ARI at LAC, 2026 Week 1','result':'LAC lost 26-14',
 'selected_team':{'head_coach':'Jim Harbaugh','offensive_coordinator':'Mike McDaniel (new in 2026)','defensive_coordinator':'Chris OLeary (new DC in 2026; regular-season playcalling debut)','preseason':'1-2'},
 'opponent':{'head_coach':'Mike LaFleur (new in 2026)','offensive_coordinator':'Nathaniel Hackett (new in 2026)','defensive_coordinator':'Nick Rallis (retained)','prior_record':'3-14'},
 'structural_flags':['team_both_coords_changed','team_preseason_losing','team_preseason_1_2_exact','opp_hc_changed_season','opp_oc_changed_season','scheme_reset_both_sides','opp_new_hc_after_bad_prior_year','chargers_like_combo'],
 'note':'Current staff facts are from official Chargers/Cardinals sources; historical coaching flags are evaluated from the audited 2007-2025 enrichment.'}
q=pre[(pre.season==2026)&(pre.team.isin(['LAC','ARI']))]
charg['preseason_from_espn']=q.to_dict('records')
(OUT/'chargers_2026_profile.json').write_text(json.dumps(charg,indent=2))
summary={'preseason_team_seasons':int(len(pre)),'bets':int(len(bets)),'flags':len(flags),'tested':int(len(res)) if len(rows) else 0,'confirmed':int(res.confirmed.sum()) if len(rows) else 0}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
lines=['NFL LEGACY HOME-OPENER PRESEASON + STAFF FILTER AUDIT','',json.dumps(summary,indent=2),'','CONFIRMED FILTERS']
if len(res):
    for _,r in res[res.confirmed].head(30).iterrows():lines.append(f"{r.method} | {r.flag} | risk n={int(r.risk_n)} win={100*r.risk_win_pct:.1f}% ROI={100*r.risk_roi:+.1f}% | safe win={100*r.safe_win_pct:.1f}% ROI={100*r.safe_roi:+.1f}% | loss lift old={100*r.old_loss_lift:+.1f}pp recent={100*r.recent_loss_lift:+.1f}pp")
lines+=['','CHARGERS PROFILE',json.dumps(charg,indent=2)]
(OUT/'report.txt').write_text('\n'.join(lines)+'\n');print('\n'.join(lines[:80]))
# trigger after workflow creation
