#!/usr/bin/env python3
import csv,gzip,json,re,math,statistics
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
def free_throw(r):
    d=(r.get("description") or "").lower()
    typ=(r.get("action_type") or "").lower()
    return "free throw" in d or "free throw" in typ
def coord(r):
    x=n(r.get("x"));y=n(r.get("y"))
    if x is None or y is None or x < -100 or y < -100:return None
    return x,y
def derived_distance(r):
    xy=coord(r)
    if not xy:return None
    x,y=xy
    # ESPN basketball half-court frame: x spans court width with hoop near x=25;
    # y is distance from the hoop toward half court, with historical fits around y=0..1.
    # Use y=1 and validate against explicit "N-foot" descriptions below.
    return math.hypot(x-25.0,y-1.0)
def text_distance(r):
    d=(r.get("description") or "").lower()
    m=re.search(r"\b(\d+(?:\.\d+)?)\s*[- ]?foot\b",d)
    return float(m.group(1)) if m else None

team={}
for p in DATA.glob("*_*/regular_season/team_boxscores.csv.gz"):
    for r in rgz(p):team[(r.get("season"),r.get("game_id"),r.get("team_id"))]=r

agg=defaultdict(lambda:{
    "shooting_plays":0,"free_throws":0,"fg_attempt_plays":0,"fg_coords":0,
    "pbp_3_text":0,"box_fga":0.0,"box_3pa":0.0,"distance_pairs":0,
    "distance_abs_error_sum":0.0,"distance_errors":[]
})
for p in DATA.glob("*_*/regular_season/playbyplay.csv.gz"):
    for r in rgz(p):
        if not truth(r.get("is_field_goal")):continue
        key=(r.get("season"),r.get("game_id"),r.get("team_id"))
        if key not in team:continue
        a=agg[key];a["shooting_plays"]+=1
        if free_throw(r):
            a["free_throws"]+=1
            continue
        a["fg_attempt_plays"]+=1
        if coord(r):a["fg_coords"]+=1
        desc=(r.get("description") or "").lower()
        if re.search(r"three[- ]point|3[- ]pt|3 pointer|3-point",desc):a["pbp_3_text"]+=1
        td=text_distance(r);dd=derived_distance(r)
        if td is not None and dd is not None:
            err=abs(td-dd);a["distance_pairs"]+=1;a["distance_abs_error_sum"]+=err
            if len(a["distance_errors"])<500:a["distance_errors"].append(err)

season=defaultdict(lambda:{
    "team_games":0,"shooting_plays":0,"free_throws":0,"fg_attempt_plays":0,"fg_coords":0,
    "pbp_3_text":0,"box_fga":0.0,"box_3pa":0.0,"distance_pairs":0,"distance_abs_error_sum":0.0,
    "distance_errors":[]
})
for key,a in agg.items():
    tr=team[key];s=key[0];x=season[s]
    x["team_games"]+=1
    for k in ("shooting_plays","free_throws","fg_attempt_plays","fg_coords","pbp_3_text","distance_pairs"):
        x[k]+=a[k]
    x["box_fga"]+=n(tr.get("field_goals_attempted")) or 0
    x["box_3pa"]+=n(tr.get("three_pointers_attempted")) or 0
    x["distance_abs_error_sum"]+=a["distance_abs_error_sum"]
    if len(x["distance_errors"])<5000:
        x["distance_errors"].extend(a["distance_errors"][:5000-len(x["distance_errors"])])

for s,x in season.items():
    x["raw_shooting_play_vs_box_fga_ratio"]=x["shooting_plays"]/x["box_fga"] if x["box_fga"] else None
    x["non_ft_fga_vs_box_ratio"]=x["fg_attempt_plays"]/x["box_fga"] if x["box_fga"] else None
    x["valid_coordinate_coverage_of_non_ft_fga"]=x["fg_coords"]/x["fg_attempt_plays"] if x["fg_attempt_plays"] else None
    x["text_3pa_vs_box_ratio"]=x["pbp_3_text"]/x["box_3pa"] if x["box_3pa"] else None
    x["derived_vs_text_distance_mae_ft"]=x["distance_abs_error_sum"]/x["distance_pairs"] if x["distance_pairs"] else None
    x["derived_vs_text_distance_median_abs_error_ft"]=statistics.median(x["distance_errors"]) if x["distance_errors"] else None
    x["fga_ratio_error_abs"]=abs(x["non_ft_fga_vs_box_ratio"]-1) if x["non_ft_fga_vs_box_ratio"] is not None else None
    x["coordinate_geometry_pass"]=bool(
        x["non_ft_fga_vs_box_ratio"] is not None and 0.97<=x["non_ft_fga_vs_box_ratio"]<=1.03 and
        x["valid_coordinate_coverage_of_non_ft_fga"] is not None and x["valid_coordinate_coverage_of_non_ft_fga"]>=0.95 and
        x["derived_vs_text_distance_mae_ft"] is not None and x["derived_vs_text_distance_mae_ft"]<=1.0
    )
    x.pop("distance_errors",None)

report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"by_season":dict(sorted(season.items())),
        "coordinate_model":{"hoop_x":25.0,"hoop_y":1.0,"distance_formula":"sqrt((x-25)^2 + (y-1)^2)",
                            "free_throw_filter":"exclude shootingPlay rows whose action/description identifies a free throw"},
        "policy":"Do not create coordinate-derived shot-location methods unless non-free-throw PBP attempt counts reconcile to box-score FGA, valid-coordinate coverage is high, and derived distance agrees with explicit shot-distance text."}
(OUT/"shot_profile_audit.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
