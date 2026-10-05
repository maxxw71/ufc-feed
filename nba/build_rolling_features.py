#!/usr/bin/env python3
import csv, gzip, json, math
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
DATA=ROOT/"data"
OUT=ROOT/"features"
OUT.mkdir(parents=True,exist_ok=True)

WINDOWS=(1,3,5,10)

def read_gz(path):
    if not path.exists(): return []
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        return list(csv.DictReader(f))

def write_gz(path,rows):
    rows=list(rows)
    fields=[]
    for r in rows:
        for k in r:
            if k not in fields: fields.append(k)
    if not fields: fields=["_empty"]
    with gzip.open(path,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        w.writeheader(); w.writerows(rows)

def num(v):
    if v in (None,"","None","nan"): return None
    try: return float(str(v).replace("%","").replace(",",""))
    except: return None

def dt(v):
    if not v: return None
    try: return datetime.fromisoformat(str(v).replace("Z","+00:00"))
    except: return None

games=[]
team=[]
players=[]
for base in DATA.glob("*/*"):
    if not base.is_dir(): continue
    games.extend(read_gz(base/"games.csv.gz"))
    team.extend(read_gz(base/"team_boxscores.csv.gz"))
    players.extend(read_gz(base/"player_boxscores.csv.gz"))

game_meta={}
for g in games:
    game_meta[g.get("game_id")]={"game_date":g.get("game_date"),"season":g.get("season"),"season_type":g.get("season_type"),"completed":str(g.get("completed")).lower() in ("true","1")}

TEAM_METRICS=[
 "points","field_goals_made","field_goals_attempted","field_goals_percentage",
 "three_pointers_made","three_pointers_attempted","three_pointers_percentage",
 "free_throws_made","free_throws_attempted","free_throws_percentage",
 "rebounds_offensive","rebounds_defensive","rebounds_total","assists","steals","blocks","turnovers","fouls_personal"
]
PLAYER_METRICS=[
 "minutes","points","assists","rebounds_total","rebounds_offensive","rebounds_defensive","steals","blocks","turnovers",
 "fouls_personal","field_goals_made","field_goals_attempted","three_pointers_made","three_pointers_attempted",
 "free_throws_made","free_throws_attempted","plus_minus"
]

def add_derived_team(row):
    fga=num(row.get("field_goals_attempted")); fgm=num(row.get("field_goals_made"))
    tpa=num(row.get("three_pointers_attempted")); tpm=num(row.get("three_pointers_made"))
    fta=num(row.get("free_throws_attempted")); pts=num(row.get("points"))
    oreb=num(row.get("rebounds_offensive")); tov=num(row.get("turnovers"))
    if fga is not None and fga>0:
        row["_e_fg"]=(fgm + 0.5*tpm)/fga if fgm is not None and tpm is not None else None
        row["_three_pa_rate"]=tpa/fga if tpa is not None else None
        row["_ft_rate"]=fta/fga if fta is not None else None
    poss=None
    if None not in (fga,fta,oreb,tov):
        poss=fga+0.44*fta-oreb+tov
    row["_possessions_est"]=poss
    if poss and pts is not None: row["_off_rating_est"]=100*pts/poss
    if pts is not None and fga is not None and fta is not None and (2*(fga+0.44*fta))>0:
        row["_true_shooting_est"]=pts/(2*(fga+0.44*fta))
    return row

for r in team: add_derived_team(r)

# Pair each team's completed-game row with its opponent so defensive/context form
# can be rolled point-in-time just like offense.
_by_game=defaultdict(list)
for r in team:
    _by_game[r.get("game_id")].append(r)
for gid,pair in _by_game.items():
    if len(pair)!=2:
        continue
    a,b=pair
    for own,opp in ((a,b),(b,a)):
        own["_opp_points"]=num(opp.get("points"))
        own["_opp_e_fg"]=num(opp.get("_e_fg"))
        own["_opp_three_pa_rate"]=num(opp.get("_three_pa_rate"))
        own["_opp_ft_rate"]=num(opp.get("_ft_rate"))
        own["_opp_true_shooting_est"]=num(opp.get("_true_shooting_est"))
        own["_opp_possessions_est"]=num(opp.get("_possessions_est"))
        own["_def_rating_est"]=(100*own["_opp_points"]/own["_opp_possessions_est"]) if own.get("_opp_possessions_est") and own.get("_opp_points") is not None else None
        own["_net_rating_est"]=(own.get("_off_rating_est")-own.get("_def_rating_est")) if own.get("_off_rating_est") is not None and own.get("_def_rating_est") is not None else None

TEAM_DERIVED=[
 "_e_fg","_three_pa_rate","_ft_rate","_possessions_est","_off_rating_est","_true_shooting_est",
 "_opp_points","_opp_e_fg","_opp_three_pa_rate","_opp_ft_rate","_opp_true_shooting_est",
 "_opp_possessions_est","_def_rating_est","_net_rating_est"
]

def pregame_roll(rows,id_field,metrics,played_field=None):
    grouped=defaultdict(list)
    for r in rows:
        ident=r.get(id_field)
        gm=game_meta.get(r.get("game_id")) or {}
        when=dt(gm.get("game_date"))
        if ident and when:
            grouped[ident].append((when,r,gm))
    out=[]
    for ident,arr in grouped.items():
        arr.sort(key=lambda x:x[0])
        hist=[]
        for when,r,gm in arr:
            feat={
                "game_id":r.get("game_id"),id_field:ident,"game_date":gm.get("game_date"),
                "season":gm.get("season") or r.get("season"),"season_type":gm.get("season_type") or r.get("season_type"),
                "prior_games_available":len(hist),"pregame_only_feature":True
            }
            if id_field=="team_id":
                feat["team_tricode"]=r.get("team_tricode")
            else:
                feat["player_name"]=r.get("player_name"); feat["team_id"]=r.get("team_id"); feat["team_tricode"]=r.get("team_tricode")
            for m in metrics:
                for w in WINDOWS:
                    vals=[num(h.get(m)) for h in hist[-w:]]
                    vals=[v for v in vals if v is not None]
                    feat[f"{m}_last{w}_avg"]=sum(vals)/len(vals) if vals else None
            out.append(feat)
            include=bool(gm.get("completed"))
            if played_field:
                played=str(r.get(played_field)).lower()
                include=include and played not in ("false","0","dnp","none","")
            if include:
                hist.append(r)
    return out

team_features=pregame_roll(team,"team_id",TEAM_METRICS+TEAM_DERIVED)
player_features=pregame_roll(players,"person_id",PLAYER_METRICS,played_field="played")

write_gz(OUT/"team_rolling.csv.gz",team_features)
write_gz(OUT/"player_rolling.csv.gz",player_features)

summary={
 "generated_at_utc":datetime.now(timezone.utc).isoformat(),
 "games":len(games),"team_boxscore_rows":len(team),"player_boxscore_rows":len(players),
 "team_feature_rows":len(team_features),"player_feature_rows":len(player_features),
 "windows":list(WINDOWS),"point_in_time_excludes_current_game":True,
 "team_metrics":TEAM_METRICS+TEAM_DERIVED,"player_metrics":PLAYER_METRICS
}
(OUT/"rolling_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
