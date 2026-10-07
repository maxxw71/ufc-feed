#!/usr/bin/env python3
import csv,gzip,json
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

NBA=Path(__file__).resolve().parent
DATA=NBA/"data";OUT=NBA/"features";OUT.mkdir(parents=True,exist_ok=True)
WINDOWS=(3,5,10)

def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def wgz(p,rows):
    rows=list(rows);fs=[]
    for r in rows:
        for k in r:
            if k not in fs:fs.append(k)
    with gzip.open(p,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fs or ["_empty"]);w.writeheader();w.writerows(rows)
def n(v):
    try:return float(v)
    except:return None
def dt(v):
    try:return datetime.fromisoformat(str(v).replace("Z","+00:00"))
    except:return None

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):games[g.get("game_id")]=g

rows=[]
for p in DATA.glob("*_*/regular_season/team_boxscores.csv.gz"):
    rows.extend(rgz(p))
bygame=defaultdict(list)
for r in rows:bygame[r.get("game_id")].append(r)

team_games=defaultdict(list)
for gid,pair in bygame.items():
    if len(pair)!=2:continue
    g=games.get(gid);when=dt(g.get("game_date") if g else None)
    if not g or not when:continue
    for own,opp in ((pair[0],pair[1]),(pair[1],pair[0])):
        tid=own.get("team_id")
        pts=n(own.get("points"));opp_pts=n(opp.get("points"))
        paint=n(own.get("stat_pointsinpaint"));opp_paint=n(opp.get("stat_pointsinpaint"))
        fb=n(own.get("points_fast_break") or own.get("stat_fastbreakpoints"));opp_fb=n(opp.get("points_fast_break") or opp.get("stat_fastbreakpoints"))
        top=n(own.get("stat_turnoverpoints"));opp_top=n(opp.get("stat_turnoverpoints"))
        oreb=n(own.get("rebounds_offensive"));opp_dreb=n(opp.get("rebounds_defensive"))
        tov=n(own.get("turnovers"));opp_tov=n(opp.get("turnovers"))
        fga=n(own.get("field_goals_attempted"));fta=n(own.get("free_throws_attempted"))
        poss=(fga+0.44*fta-oreb+tov) if None not in (fga,fta,oreb,tov) else None
        x={
          "season":g.get("season"),"game_id":gid,"game_date":g.get("game_date"),"team_id":tid,
          "paint_points":paint,"paint_share":paint/pts if paint is not None and pts and pts>0 else None,
          "opp_paint_points":opp_paint,"opp_paint_share":opp_paint/opp_pts if opp_paint is not None and opp_pts and opp_pts>0 else None,
          "fastbreak_points":fb,"fastbreak_share":fb/pts if fb is not None and pts and pts>0 else None,
          "opp_fastbreak_points":opp_fb,
          "turnover_points":top,"turnover_points_share":top/pts if top is not None and pts and pts>0 else None,
          "opp_turnover_points":opp_top,
          "oreb_rate":oreb/(oreb+opp_dreb) if oreb is not None and opp_dreb is not None and (oreb+opp_dreb)>0 else None,
          "turnover_rate":tov/poss if tov is not None and poss and poss>0 else None,
          "forced_turnovers":opp_tov,
          "fouls_per100":100*n(own.get("fouls_personal"))/poss if n(own.get("fouls_personal")) is not None and poss and poss>0 else None,
          "technical_fouls":n(own.get("stat_technicalfouls")),
          "flagrant_fouls":n(own.get("stat_flagrantfouls")),
          "largest_lead":n(own.get("stat_largestlead")),
        }
        team_games[(g.get("season"),tid)].append((when,x))

metrics=["paint_points","paint_share","opp_paint_points","opp_paint_share","fastbreak_points","fastbreak_share","opp_fastbreak_points",
         "turnover_points","turnover_points_share","opp_turnover_points","oreb_rate","turnover_rate","forced_turnovers",
         "fouls_per100","technical_fouls","flagrant_fouls","largest_lead"]
out=[]
coverage={m:0 for m in metrics};total=0
for (season,tid),arr in team_games.items():
    arr.sort(key=lambda z:z[0]);hist=[]
    for when,x in arr:
        row={"season":season,"game_id":x["game_id"],"game_date":x["game_date"],"team_id":tid,"pregame_only_feature":True}
        for w in WINDOWS:
            prior=hist[-w:]
            for m in metrics:
                vals=[z.get(m) for z in prior if z.get(m) is not None]
                row[f"{m}_last{w}_avg"]=sum(vals)/len(vals) if vals else None
        out.append(row);hist.append(x)
        total+=1
        for m in metrics:
            if x.get(m) is not None:coverage[m]+=1
coverage={m:(coverage[m]/total if total else 0) for m in metrics}
wgz(OUT/"team_style_rolling.csv.gz",out)
summary={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(out),"team_seasons":len(team_games),
         "windows":list(WINDOWS),"metrics":metrics,"raw_game_metric_coverage":coverage,"point_in_time":True}
(OUT/"team_style_rolling_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
