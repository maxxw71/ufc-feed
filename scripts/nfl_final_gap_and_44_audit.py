from pathlib import Path
import json, re
import pandas as pd
import numpy as np

ROOT=Path('/home/anestishkurti92/nfl-predictor-v1')
R=ROOT/'research_v2'
OUT=Path('/home/appwiza-runner/nfl-context-data/final_method_expansion')
OUT.mkdir(parents=True,exist_ok=True)
lines=[]

def say(x=''):
    lines.append(str(x)); print(x,flush=True)

say('NFL FINAL GAP + 44-7 AUDIT')
say('')
# Core enriched table
p=Path('/home/appwiza-runner/nfl-context-data/injury_travel_mining/complete_pregame_team_sides.parquet')
if p.exists():
    d=pd.read_parquet(p)
    say(f'complete_pregame_rows={len(d)} cols={len(d.columns)} seasons={int(pd.to_numeric(d.season,errors="coerce").min())}-{int(pd.to_numeric(d.season,errors="coerce").max())}')
    checks={
      'injury_data_available':['injury_data_available','unavailable_equiv','adv_unavailable_equiv','qb_unavail_equiv','ol_unavail_equiv','star_outs'],
      'travel':['travel_miles','abs_tz_shift_hours','altitude_change_ft','consecutive_road_games','international_game','high_altitude_game'],
      'continuity':['returning_offense_snap_share','returning_defense_snap_share','returning_ol_snap_share','returning_skill_snap_share'],
      'qb_coach':['qb_prior_starts','qb_changed_from_prior_season','head_coach_changed','offensive_coordinator_changed','defensive_coordinator_changed'],
      'weather_venue':['wind','roof','surface'],
      'rest_elo':['rest_edge','elo_edge'],
    }
    for fam,cols in checks.items():
        vals=[]
        for c in cols:
            if c in d.columns: vals.append(f'{c}:{100*d[c].notna().mean():.1f}%')
            else: vals.append(f'{c}:MISSING')
        say(f'{fam} -> '+', '.join(vals))
else:
    say('complete enriched table MISSING')

# Historical open/close quotes structure
od=R/'historical_odds'/'all_open_close_quotes.csv'
if od.exists():
    q=pd.read_csv(od,low_memory=False)
    say('')
    say(f'odds_quotes rows={len(q)} cols={len(q.columns)} seasons={q.season.min()}-{q.season.max()}')
    say('odds_columns='+','.join(q.columns))
    for c in ['market','phase','selection','provider']:
        if c in q.columns:
            vc=q[c].astype(str).value_counts(dropna=False).head(20)
            say(f'{c}_top='+json.dumps({str(k):int(v) for k,v in vc.items()}))
    say('odds_sample:')
    say(q.head(12).to_csv(index=False))
else:
    say('historical open/close quotes MISSING')

# Schedule columns relevant to richer travel/time/day context
sp=ROOT/'data'/'raw'/'schedules_2006_2026.parquet'
if sp.exists():
    s=pd.read_parquet(sp)
    say('')
    say('schedule_context_columns='+','.join([c for c in s.columns if any(k in c.lower() for k in ['time','stad','location','rest','roof','surface','temp','wind','div','weekday','gameday'])]))

# Locate rows/files that appear to encode 44 wins / 7 losses or 51 total with 44 wins.
say('')
say('44-7 SEARCH')
matches=[]
for f in R.rglob('*.csv'):
    try:
        if f.stat().st_size>60_000_000: continue
        x=pd.read_csv(f,low_memory=False)
    except Exception:
        continue
    low={c.lower():c for c in x.columns}
    w=next((low[k] for k in ['wins','full_wins','later_wins','train_wins'] if k in low),None)
    l=next((low[k] for k in ['losses','full_losses','later_losses','train_losses'] if k in low),None)
    n=next((low[k] for k in ['bets','n','full_n','later_bets','train_bets'] if k in low),None)
    masks=[]
    if w and l:
        masks.append((pd.to_numeric(x[w],errors='coerce')==44)&(pd.to_numeric(x[l],errors='coerce')==7))
    if w and n:
        masks.append((pd.to_numeric(x[w],errors='coerce')==44)&(pd.to_numeric(x[n],errors='coerce')==51))
    if not masks: continue
    m=masks[0]
    for z in masks[1:]: m=m|z
    if m.any():
        for idx,row in x[m].head(20).iterrows():
            keep=[c for c in x.columns if c.lower() in {'family','description','rule','rule_id','base_rule_id','context','price_band','venue','parameters','bets','wins','losses','win_rate','roi','full_n','full_wins','full_losses','full_roi','later_bets','later_wins','later_losses','later_roi'}]
            rec={c:(None if pd.isna(row[c]) else str(row[c])) for c in keep}
            matches.append({'file':str(f),'row':int(idx),**rec})
            say(json.dumps(matches[-1],sort_keys=True))

# Also grep text reports for literal patterns.
text_hits=[]
pat=re.compile(r'44\s*[-–/]\s*7|44\s+wins?.{0,25}7\s+loss|\b44\D+7\b',re.I)
for f in list(R.rglob('*.txt'))+list(R.rglob('*.md')):
    try:
        if f.stat().st_size>15_000_000: continue
        txt=f.read_text(errors='ignore')
    except Exception: continue
    if pat.search(txt):
        mm=pat.search(txt); a=max(0,mm.start()-250); b=min(len(txt),mm.end()+350)
        hit={'file':str(f),'snippet':txt[a:b].replace('\n',' ')[:700]}
        text_hits.append(hit); say(json.dumps(hit))

summary={'fortyfour_csv_matches':len(matches),'fortyfour_text_hits':len(text_hits),'odds_file_exists':od.exists(),'complete_table_exists':p.exists()}
(OUT/'gap_44_audit.json').write_text(json.dumps(summary,indent=2))
(OUT/'gap_44_audit.txt').write_text('\n'.join(lines)+'\n')
pd.DataFrame(matches).to_csv(OUT/'fortyfour_matches.csv',index=False)
print(json.dumps(summary,indent=2))
