#!/usr/bin/env python3
import csv,gzip,json,statistics
from datetime import datetime,timezone
from pathlib import Path

NBA=Path(__file__).resolve().parents[1]
F=NBA/"features"; PLAYERS=NBA/"players"/"historical_player_profiles.csv.gz"
LINE=F/"lineup_pregame.csv.gz"

def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def n(v):
    try:return float(v)
    except:return None
def parse_dt(v):
    if not v:return None
    try:return datetime.fromisoformat(str(v).replace("Z","+00:00"))
    except:
        try:return datetime.strptime(str(v)[:10],"%Y-%m-%d").replace(tzinfo=timezone.utc)
        except:return None
def wgz(p,rows):
    rows=list(rows);fs=[]
    for r in rows:
        for k in r:
            if k not in fs:fs.append(k)
    with gzip.open(p,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fs or ["_empty"]);w.writeheader();w.writerows(rows)

profiles={r["person_id"]:r for r in rgz(PLAYERS) if r.get("person_id")}
rows=[]
for r in rgz(LINE):
    ids=[x for x in (r.get("confirmed_starting_five") or "").split("|") if x]
    if len(ids)!=5:continue
    gd=parse_dt(r.get("game_date"))
    ps=[profiles.get(pid,{}) for pid in ids]
    heights=[n(p.get("height_inches")) for p in ps];heights=[x for x in heights if x is not None]
    weights=[n(p.get("weight_lbs")) for p in ps];weights=[x for x in weights if x is not None]
    exps=[n(p.get("experience_years")) for p in ps];exps=[x for x in exps if x is not None]
    ages=[]
    if gd:
        for p in ps:
            dob=parse_dt(p.get("date_of_birth"))
            if dob:
                ages.append((gd-dob).days/365.2425)
    pos=[(p.get("position_abbr") or "").upper() for p in ps]
    def pos_avg(cats,key):
        vals=[]
        for p,po in zip(ps,pos):
            if po in cats:
                v=n(p.get(key))
                if v is not None: vals.append(v)
        return statistics.mean(vals) if vals else None
    out={
      "season":r.get("season"),"game_id":r.get("game_id"),"game_date":r.get("game_date"),"team_id":r.get("team_id"),
      "pregame_only_feature":True,"requires_confirmed_starters":True,
      "starter_profile_coverage":sum(1 for p in ps if p)/5,
      "starter_avg_height_inches":statistics.mean(heights) if heights else None,
      "starter_avg_weight_lbs":statistics.mean(weights) if weights else None,
      "starter_avg_age":statistics.mean(ages) if ages else None,
      "starter_age_std":statistics.pstdev(ages) if len(ages)>=2 else None,
      "starter_avg_experience":statistics.mean(exps) if exps else None,
      "guard_height":pos_avg({"PG","SG","G"},"height_inches"),
      "wing_height":pos_avg({"SF","F","GF","G-F","F-G"},"height_inches"),
      "big_height":pos_avg({"PF","C","FC","F-C","C-F"},"height_inches"),
      "guard_weight":pos_avg({"PG","SG","G"},"weight_lbs"),
      "big_weight":pos_avg({"PF","C","FC","F-C","C-F"},"weight_lbs")
    }
    rows.append(out)

wgz(F/"starter_physical.csv.gz",rows)
summary={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(rows),
         "coverage_height":sum(1 for r in rows if r["starter_avg_height_inches"] is not None)/len(rows) if rows else 0,
         "coverage_age":sum(1 for r in rows if r["starter_avg_age"] is not None)/len(rows) if rows else 0,
         "policy":"Uses confirmed target-game starters plus stable historical player profile attributes. Suitable only after starters are known pregame."}
(F/"starter_physical_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
