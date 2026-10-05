#!/usr/bin/env python3
import csv,gzip,json
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
DATA=ROOT/"data"
OUT=ROOT/"features"
OUT.mkdir(parents=True,exist_ok=True)

def read(path):
    if not path.exists(): return []
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        return list(csv.DictReader(f))

def write(path,rows):
    rows=list(rows); fields=[]
    for r in rows:
        for k in r:
            if k not in fields: fields.append(k)
    if not fields: fields=["_empty"]
    with gzip.open(path,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)

def n(v):
    try:return int(float(v))
    except:return None

def clock_seconds(v):
    if not v:return None
    s=str(v)
    if s.startswith("PT"):
        import re
        m=re.search(r"(\d+)M",s); sec=re.search(r"([\d.]+)S",s)
        return (int(m.group(1)) if m else 0)+(float(sec.group(1)) if sec else 0)
    try:
        mm,ss=s.split(":")
        return int(mm)*60+float(ss)
    except:return None

games={}
for p in DATA.glob("*/*/games.csv.gz"):
    for g in read(p):
        games[g.get("game_id")]=g

plays=defaultdict(list)
for p in DATA.glob("*/*/playbyplay.csv.gz"):
    for r in read(p):
        gid=r.get("game_id")
        if gid: plays[gid].append(r)

rows=[]
for gid,g in games.items():
    pp=plays.get(gid) or []
    if not pp: continue
    pp.sort(key=lambda r:n(r.get("action_number")) or 0)
    q_end={}
    max_home_lead=0; max_away_lead=0
    lead_changes=0; ties=0
    prev_sign=None
    clutch_actions=0
    first_half_home=first_half_away=None
    for r in pp:
        hs=n(r.get("score_home")); aw=n(r.get("score_away"))
        per=n(r.get("period")) or 0
        if hs is None or aw is None: continue
        diff=hs-aw
        max_home_lead=max(max_home_lead,diff)
        max_away_lead=max(max_away_lead,-diff)
        sign=1 if diff>0 else (-1 if diff<0 else 0)
        if sign==0 and prev_sign not in (None,0): ties+=1
        if sign not in (0,None) and prev_sign not in (None,0) and sign!=prev_sign:
            lead_changes+=1
        if sign!=0: prev_sign=sign
        q_end[per]=(hs,aw)
        secs=clock_seconds(r.get("clock"))
        if per==4 and secs is not None and secs<=300 and abs(diff)<=5:
            clutch_actions+=1
    if 2 in q_end:
        first_half_home,first_half_away=q_end[2]
    final_h=n(g.get("home_score")); final_a=n(g.get("away_score"))
    if final_h is None or final_a is None:
        last=q_end.get(max(q_end) if q_end else 0)
        if last: final_h,final_a=last
    row={
      "season":g.get("season"),"season_type":g.get("season_type"),"game_id":gid,
      "game_date":g.get("game_date"),"home_team_id":g.get("home_team_id"),"away_team_id":g.get("away_team_id"),
      "halftime_home":first_half_home,"halftime_away":first_half_away,
      "halftime_margin":(first_half_home-first_half_away) if first_half_home is not None and first_half_away is not None else None,
      "final_margin":(final_h-final_a) if final_h is not None and final_a is not None else None,
      "largest_home_lead":max_home_lead,"largest_away_lead":max_away_lead,
      "lead_changes_est":lead_changes,"ties_est":ties,"clutch_actions_est":clutch_actions,
      "overtime_periods":max(0,(max(q_end) if q_end else 4)-4),
      "home_q1":q_end.get(1,(None,None))[0],"away_q1":q_end.get(1,(None,None))[1],
      "home_q2_cum":q_end.get(2,(None,None))[0],"away_q2_cum":q_end.get(2,(None,None))[1],
      "home_q3_cum":q_end.get(3,(None,None))[0],"away_q3_cum":q_end.get(3,(None,None))[1],
      "home_q4_cum":q_end.get(4,(None,None))[0],"away_q4_cum":q_end.get(4,(None,None))[1],
      "generated_at_utc":datetime.now(timezone.utc).isoformat()
    }
    if first_half_home is not None and final_h is not None:
        row["home_second_half_plus_ot"]=final_h-first_half_home
        row["away_second_half_plus_ot"]=final_a-first_half_away
    rows.append(row)

write(OUT/"game_flow.csv.gz",rows)
summary={
 "generated_at_utc":datetime.now(timezone.utc).isoformat(),
 "games_with_playbyplay":len(rows),
 "fields":["halftime_margin","largest_home_lead","largest_away_lead","lead_changes_est","ties_est","clutch_actions_est","overtime_periods","quarter_cumulative_scores"]
}
(OUT/"game_flow_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
