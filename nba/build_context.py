#!/usr/bin/env python3
import csv, gzip, json
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parent
DATA=ROOT/"data"

def parse_dt(s):
    if not s: return None
    try:
        return datetime.fromisoformat(str(s).replace("Z","+00:00")).astimezone(timezone.utc)
    except Exception:
        return None

def read_rows(path):
    if not path.exists(): return []
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        return list(csv.DictReader(f))

def write_rows(path,rows):
    rows=list(rows)
    fields=[]
    for r in rows:
        for k in r:
            if k not in fields: fields.append(k)
    if not fields: fields=["_empty"]
    with gzip.open(path,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        w.writeheader()
        for r in rows: w.writerow(r)

def max_periods():
    out={}
    for pbp in DATA.glob("*/*/playbyplay.csv.gz"):
        for r in read_rows(pbp):
            gid=r.get("game_id")
            try: p=int(float(r.get("period") or 0))
            except: p=0
            if gid: out[gid]=max(out.get(gid,0),p)
    return out

games=[]
for p in DATA.glob("*/*/games.csv.gz"):
    games.extend(read_rows(p))

ot=max_periods()
team_games=defaultdict(list)
for g in games:
    dt=parse_dt(g.get("game_date"))
    if not dt: continue
    for side in ("home","away"):
        team_id=g.get(f"{side}_team_id")
        opp="away" if side=="home" else "home"
        if not team_id: continue
        team_games[team_id].append({
            "season":g.get("season"),"season_type":g.get("season_type"),"game_id":g.get("game_id"),
            "tipoff_utc":dt,"team_id":team_id,"team":g.get(f"{side}_team"),
            "team_tricode":g.get(f"{side}_tricode"),"opponent_id":g.get(f"{opp}_team_id"),
            "opponent":g.get(f"{opp}_team"),"opponent_tricode":g.get(f"{opp}_tricode"),
            "is_home":side=="home","arena_city":g.get("arena_city"),"arena_state":g.get("arena_state"),
            "completed":str(g.get("completed")).lower() in ("true","1")
        })

rows=[]
for team_id,arr in team_games.items():
    arr.sort(key=lambda x:x["tipoff_utc"])
    history=[]
    home_run=0; road_run=0
    for x in arr:
        dt=x["tipoff_utc"]
        prev=history[-1] if history else None
        days=None
        if prev:
            delta=dt.date()-prev["tipoff_utc"].date()
            days=delta.days
        def count_window(days_back):
            cutoff=dt-timedelta(days=days_back)
            return sum(1 for h in history if h["tipoff_utc"]>=cutoff and h["tipoff_utc"]<dt)
        if x["is_home"]:
            home_run=home_run+1
            road_run=0
        else:
            road_run=road_run+1
            home_run=0
        r={k:(v.isoformat() if k=="tipoff_utc" else v) for k,v in x.items()}
        r.update({
            "days_since_prev_game":days,
            "back_to_back":bool(days is not None and days<=1),
            "games_prev_2_days":count_window(2),
            "games_prev_3_days":count_window(3),
            "games_prev_4_days":count_window(4),
            "games_prev_6_days":count_window(6),
            "games_prev_8_days":count_window(8),
            "three_in_four":count_window(3)>=2,
            "four_in_six":count_window(5)>=3,
            "five_in_eight":count_window(7)>=4,
            "home_stand_game_number":home_run if x["is_home"] else 0,
            "road_trip_game_number":road_run if not x["is_home"] else 0,
            "prev_game_id":prev["game_id"] if prev else None,
            "prev_game_went_ot":bool(prev and ot.get(prev["game_id"],0)>4),
            "pregame_only_feature":True,
            "generated_at_utc":datetime.now(timezone.utc).isoformat()
        })
        rows.append(r)
        if x["completed"]:
            history.append(x)

out=ROOT/"team_game_context.csv.gz"
write_rows(out,rows)
summary={
    "team_game_context_rows":len(rows),
    "teams":len(team_games),
    "games_seen":len(games),
    "overtime_games_detected":sum(1 for p in ot.values() if p>4),
    "generated_at_utc":datetime.now(timezone.utc).isoformat()
}
(ROOT/"context_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
