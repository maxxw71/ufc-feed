#!/usr/bin/env python3
import csv,gzip,json,re
from collections import defaultdict
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parent;DATA=NBA/"data";OUT=NBA/"research"/"gap_audit";OUT.mkdir(parents=True,exist_ok=True)
def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def n(v):
    try:return float(v)
    except:return None
def truth(v):return str(v).lower() in ("true","1","yes","made")

team={}
for p in DATA.glob("*_*/regular_season/team_boxscores.csv.gz"):
    for r in rgz(p):team[(r.get("season"),r.get("game_id"),r.get("team_id"))]=r

agg=defaultdict(lambda:{"pbp_fga":0,"pbp_dist":0,"pbp_3_text":0,"box_fga":0.0,"box_3pa":0.0,"rows":0})
for p in DATA.glob("*_*/regular_season/playbyplay.csv.gz"):
    for r in rgz(p):
        if not truth(r.get("is_field_goal")):continue
        key=(r.get("season"),r.get("game_id"),r.get("team_id"))
        if key not in team:continue
        a=agg[key];a["pbp_fga"]+=1
        if n(r.get("shot_distance")) is not None:a["pbp_dist"]+=1
        desc=(r.get("description") or "").lower()
        if re.search(r"three[- ]point|3[- ]pt|3 pointer|3-point",desc):a["pbp_3_text"]+=1

season=defaultdict(lambda:{"team_games":0,"pbp_fga":0,"pbp_dist":0,"pbp_3_text":0,"box_fga":0.0,"box_3pa":0.0})
for key,a in agg.items():
    tr=team[key];s=key[0];x=season[s]
    x["team_games"]+=1;x["pbp_fga"]+=a["pbp_fga"];x["pbp_dist"]+=a["pbp_dist"];x["pbp_3_text"]+=a["pbp_3_text"]
    x["box_fga"]+=n(tr.get("field_goals_attempted")) or 0;x["box_3pa"]+=n(tr.get("three_pointers_attempted")) or 0
for s,x in season.items():
    x["pbp_fga_vs_box_ratio"]=x["pbp_fga"]/x["box_fga"] if x["box_fga"] else None
    x["distance_coverage_of_pbp_fga"]=x["pbp_dist"]/x["pbp_fga"] if x["pbp_fga"] else None
    x["text_3pa_vs_box_ratio"]=x["pbp_3_text"]/x["box_3pa"] if x["box_3pa"] else None
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"by_season":dict(sorted(season.items())),
        "policy":"Do not create shot-location methods unless PBP attempt and distance/text coverage is acceptably consistent against official team boxscores."}
(OUT/"shot_profile_audit.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
