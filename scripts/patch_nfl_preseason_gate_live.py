from pathlib import Path
import sys

scanner=Path(sys.argv[1])
design=Path(sys.argv[2])
s=scanner.read_text()
d=design.read_text()

if 'LEGACY_PRESEASON_GATE_V1' not in s:
    import_anchor="import nfl_email_design\n"
    if import_anchor not in s:
        raise SystemExit('scanner import anchor missing')
    s=s.replace(import_anchor,import_anchor+"import legacy_preseason_veto_policy as preseason_policy\n",1)

    version_old="VERSION='nfl-v3-late-road-protection-rest'"
    if version_old in s:
        s=s.replace(version_old,"VERSION='nfl-v4-home-opener-preseason-gate'",1)

    root_anchor="R=Path(__file__).resolve().parent;S=R/'home_opener_email_state';NY=ZoneInfo('America/New_York')"
    if root_anchor not in s:
        raise SystemExit('scanner root anchor missing')
    s=s.replace(root_anchor,root_anchor+"\nPRESEASON_DATA=R/'data'/'preseason_team_2006_2026.csv'\nPRESEASON_NO_LEAGUE={2020}",1)

    main_anchor="def settlement_result(home,away,side='home'):"
    if main_anchor not in s:
        raise SystemExit('scanner function anchor missing')
    helper=r'''
# LEGACY_PRESEASON_GATE_V1
# Validated refinement: original/stricter home-opener methods require >=2
# preseason wins whenever that NFL season had a preseason. A league-wide
# no-preseason season (2020) is neutral. Missing team data fails closed.
def _preseason_profiles(season):
    season=int(season)
    if season in PRESEASON_NO_LEAGUE:
        return False,{}
    if not PRESEASON_DATA.exists():
        raise RuntimeError(f'Missing audited preseason source: {PRESEASON_DATA}')
    z=pd.read_csv(PRESEASON_DATA)
    if not {'season','team','preseason_wins','preseason_games'}.issubset(z.columns):
        raise RuntimeError('Audited preseason source missing required columns')
    z=z[pd.to_numeric(z.season,errors='coerce').eq(season)].copy()
    if len(z)!=32 or z.team.astype(str).nunique()!=32:
        raise RuntimeError(f'Incomplete preseason coverage for {season}: {len(z)} rows / {z.team.astype(str).nunique()} teams')
    out={}
    for _,x in z.iterrows():
        team=str(x.team)
        wins=pd.to_numeric(pd.Series([x.preseason_wins]),errors='coerce').iloc[0]
        games=pd.to_numeric(pd.Series([x.preseason_games]),errors='coerce').iloc[0]
        if pd.isna(wins) or pd.isna(games):
            raise RuntimeError(f'Incomplete preseason record for {team}')
        record=x.get('preseason_record_equiv')
        out[team]={'wins':float(wins),'games':float(games),'record':None if pd.isna(record) else str(record),
                   'source':None if pd.isna(x.get('preseason_source')) else str(x.get('preseason_source'))}
    return True,out

def _preseason_fail_closed(records,reason):
    kept=[];rejections=[]
    gated_set=set(preseason_policy.LEGACY_PRESEASON_GATED_METHODS)
    for rec in records:
        gated=[r for r in rec.get('rules',[]) if r in gated_set]
        if not gated:
            kept.append(rec);continue
        remaining=[r for r in rec.get('rules',[]) if r not in gated_set]
        r=dict(rec);r['rules']=remaining
        r['preseason_profile']={'available':False,'pass':False,'error':reason}
        rejections.append({'game_id':r.get('game_id'),'selected_team':r.get('selected_team'),
                           'removed_rules':gated,'remaining_rules':remaining,
                           'reasons':['preseason_gate_fail_closed:'+reason]})
        if remaining:kept.append(r)
    return kept,rejections

def apply_preseason_gate(records,season):
    try:
        league_had,profiles=_preseason_profiles(season)
    except Exception as exc:
        return _preseason_fail_closed(records,type(exc).__name__+':'+str(exc))
    gated_set=set(preseason_policy.LEGACY_PRESEASON_GATED_METHODS)
    kept=[];rejections=[]
    for rec in records:
        gated=[rule for rule in rec.get('rules',[]) if rule in gated_set]
        if not gated:
            kept.append(rec);continue
        side=rec.get('selection_side','home')
        selected=rec.get('selected_team') or rec.get(side)
        prof=profiles.get(str(selected)) if league_had else None
        wins=None if prof is None else prof.get('wins')
        passed=all(preseason_policy.passes_preseason_gate(rule,wins,league_had) for rule in gated)
        r=dict(rec)
        r['preseason_profile']={
            'available':bool((not league_had) or prof),
            'league_had_preseason':league_had,
            'wins':wins,
            'games':None if prof is None else prof.get('games'),
            'record':None if prof is None else prof.get('record'),
            'source':None if prof is None else prof.get('source'),
            'pass':bool(passed),
            'minimum_wins':preseason_policy.MIN_PRESEASON_WINS,
        }
        if passed:
            kept.append(r);continue
        remaining=[rule for rule in r.get('rules',[]) if rule not in gated_set]
        r['rules']=remaining
        rejections.append({'game_id':r.get('game_id'),'selected_team':selected,
                           'removed_rules':gated,'remaining_rules':remaining,
                           'preseason_profile':r['preseason_profile'],
                           'reasons':[f"preseason_gate_failed:{wins if wins is not None else 'missing'} wins < {preseason_policy.MIN_PRESEASON_WINS}"]})
        if remaining:kept.append(r)
    return kept,rejections

'''
    s=s.replace(main_anchor,helper+main_anchor,1)

    gate_anchor=" production_rejections=[{'game_id':None,'reasons':['match_guard_exception:'+type(e).__name__]}]\n"
    if gate_anchor not in s:
        raise SystemExit('production guard anchor missing')
    gate_code=""" production_rejections=[{'game_id':None,'reasons':['match_guard_exception:'+type(e).__name__]}]\n\n records,preseason_rejections=apply_preseason_gate(records,season)\n production_rejections.extend(preseason_rejections)\n"""
    s=s.replace(gate_anchor,gate_code,1)

# Email/site method definitions and evidence must reflect the production rule.
old_original="'original':('M1','Home opener · Run defense','79.6%','78 / 98','+10.7%','Archived opening prices','Previous-season run defense in the top 10, plus a better regular-season record than the opponent.'),"
new_original="'original':('M1','Home opener · Run defense + preseason gate','92.2%','59 / 64','+25.2%','Archived opening prices · validated preseason refinement','Previous-season run defense in the top 10, a better regular-season record than the opponent, and at least 2 preseason wins when the league held a preseason.'),"
if old_original in d:
    d=d.replace(old_original,new_original,1)

old_strict="'stricter':('M2','Home opener · Larger record advantage','82.4%','56 / 68','+11.1%','Archived opening prices','All M1 conditions, plus a winning-percentage advantage greater than 12.5 percentage points.'),"
new_strict="'stricter':('M2','Home opener · Larger record edge + preseason gate','93.8%','45 / 48','+24.3%','Archived opening prices · validated preseason refinement','All M1 conditions, plus a winning-percentage advantage greater than 12.5 percentage points.'),"
if old_strict in d:
    d=d.replace(old_strict,new_strict,1)

ev_old='''  rows=[f"First non-neutral regular-season home game · Week {r['week']}",f"Previous-season run defense: #{r['rank']:g} of 32 (required: top 10)",f"Previous-season records: {name(r['home'])} {h['wins']}–{h['losses']}–{h['ties']} vs {name(r['away'])} {a['wins']}–{a['losses']}–{a['ties']}",f"Winning-percentage advantage: {r['record_gap_pp']:.2f} points (required: {'>12.5' if rule=='stricter' else '>0'})"]'''
ev_new='''  p=r.get('preseason_profile') or {}
  pre_record=p.get('record') or ('no league preseason' if p.get('league_had_preseason') is False else 'unavailable')
  pre_check=f"Preseason: {pre_record} · wins {p.get('wins') if p.get('wins') is not None else 'N/A'} (required: ≥2 when preseason exists)"
  rows=[f"First non-neutral regular-season home game · Week {r['week']}",f"Previous-season run defense: #{r['rank']:g} of 32 (required: top 10)",f"Previous-season records: {name(r['home'])} {h['wins']}–{h['losses']}–{h['ties']} vs {name(r['away'])} {a['wins']}–{a['losses']}–{a['ties']}",f"Winning-percentage advantage: {r['record_gap_pp']:.2f} points (required: {'>12.5' if rule=='stricter' else '>0'})",pre_check]'''
if ev_old in d:
    d=d.replace(ev_old,ev_new,1)
elif 'pre_check=f"Preseason:' not in d:
    raise SystemExit('email evidence anchor missing')

scanner.write_text(s)
design.write_text(d)
print('NFL validated preseason gate installed upstream')
