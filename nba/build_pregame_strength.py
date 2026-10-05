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
    rows=list(rows)
    fields=[]
    for r in rows:
        for k in r:
            if k not in fields: fields.append(k)
    if not fields: fields=["_empty"]
    with gzip.open(path,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)

def n(v):
    try: return float(v)
    except: return None

def dt(v):
    try: return datetime.fromisoformat(str(v).replace("Z","+00:00"))
    except: return None

games=[]
for base in DATA.glob("*/*"):
    if base.is_dir():
        games.extend(read(base/"games.csv.gz"))

games=[g for g in games if dt(g.get("game_date"))]
games.sort(key=lambda g:dt(g.get("game_date")))

state=defaultdict(lambda:{
    "w":0,"l":0,"pf":0.0,"pa":0.0,"g":0,
    "home_w":0,"home_l":0,"road_w":0,"road_l":0
})
rows=[]

def snap(tid):
    s=state[tid]
    return {
      "prior_games":s["g"],"prior_wins":s["w"],"prior_losses":s["l"],
      "prior_win_pct":s["w"]/s["g"] if s["g"] else None,
      "prior_points_for_avg":s["pf"]/s["g"] if s["g"] else None,
      "prior_points_against_avg":s["pa"]/s["g"] if s["g"] else None,
      "prior_point_diff_avg":(s["pf"]-s["pa"])/s["g"] if s["g"] else None,
      "prior_home_win_pct":s["home_w"]/(s["home_w"]+s["home_l"]) if (s["home_w"]+s["home_l"]) else None,
      "prior_road_win_pct":s["road_w"]/(s["road_w"]+s["road_l"]) if (s["road_w"]+s["road_l"]) else None
    }

for g in games:
    hid=g.get("home_team_id"); aid=g.get("away_team_id")
    if not hid or not aid: continue
    season=g.get("season")\n    hs=snap(season,hid); as_=snap(season,aid)
    base={
      "season":g.get("season"),"season_type":g.get("season_type"),
      "game_id":g.get("game_id"),"game_date":g.get("game_date"),
      "home_team_id":hid,"home_team":g.get("home_team"),"away_team_id":aid,"away_team":g.get("away_team"),
      "pregame_only_feature":True
    }
    row=dict(base)
    for k,v in hs.items(): row["home_"+k]=v
    for k,v in as_.items(): row["away_"+k]=v
    row["pregame_win_pct_diff"]=(hs["prior_win_pct"]-as_["prior_win_pct"]) if hs["prior_win_pct"] is not None and as_["prior_win_pct"] is not None else None
    row["pregame_point_diff_form_gap"]=(hs["prior_point_diff_avg"]-as_["prior_point_diff_avg"]) if hs["prior_point_diff_avg"] is not None and as_["prior_point_diff_avg"] is not None else None
    rows.append(row)

    completed=str(g.get("completed")).lower() in ("true","1")
    hp=n(g.get("home_score")); ap=n(g.get("away_score"))
    if not completed or hp is None or ap is None: continue
    sh=state[(season,hid)]; sa=state[(season,aid)]
    sh["g"]+=1; sa["g"]+=1
    sh["pf"]+=hp; sh["pa"]+=ap; sa["pf"]+=ap; sa["pa"]+=hp
    if hp>ap:
        sh["w"]+=1; sa["l"]+=1; sh["home_w"]+=1; sa["road_l"]+=1
    else:
        sh["l"]+=1; sa["w"]+=1; sh["home_l"]+=1; sa["road_w"]+=1

write(OUT/"pregame_strength.csv.gz",rows)
summary={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(rows),"teams_seen":len(set(k[1] for k in state)),"point_in_time":True}
(OUT/"pregame_strength_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
