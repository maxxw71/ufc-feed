#!/usr/bin/env python3
import csv,gzip,json
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

NBA=Path(__file__).resolve().parent
DATA=NBA/"data";SRC=NBA/"officials"/"historical_game_officials.csv.gz";OUT=NBA/"features";OUT.mkdir(parents=True,exist_ok=True)
WINDOWS=(10,25,50)

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
teamrows=defaultdict(list)
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):games[g.get("game_id")]=g
for p in DATA.glob("*_*/regular_season/team_boxscores.csv.gz"):
    for r in rgz(p):teamrows[r.get("game_id")].append(r)
officials=defaultdict(list)
for r in rgz(SRC):
    if r.get("game_id") and r.get("official_name"):officials[r["game_id"]].append(r["official_name"])

game_stats={}
for gid,g in games.items():
    pair=teamrows.get(gid,[])
    if len(pair)!=2:continue
    home=next((r for r in pair if str(r.get("is_home")).lower() in ("true","1")),None)
    away=next((r for r in pair if r is not home),None)
    if not home or not away:continue
    hp=n(home.get("points"));ap=n(away.get("points"))
    hf=n(home.get("fouls_personal"));af=n(away.get("fouls_personal"))
    hfta=n(home.get("free_throws_attempted"));afta=n(away.get("free_throws_attempted"))
    if hp is None or ap is None:continue
    game_stats[gid]={
      "date":dt(g.get("game_date")),"season":g.get("season"),
      "total_points":hp+ap,"total_fouls":(hf+af) if hf is not None and af is not None else None,
      "total_fta":(hfta+afta) if hfta is not None and afta is not None else None,
      "home_margin":hp-ap,"home_win":1.0 if hp>ap else 0.0,
      "home_fta_diff":(hfta-afta) if hfta is not None and afta is not None else None
    }

events=[]
for gid,names in officials.items():
    gs=game_stats.get(gid)
    if gs and gs["date"]:events.append((gs["date"],gid,names,gs))
events.sort(key=lambda x:x[0])

hist=defaultdict(list);out=[]
metrics=["total_points","total_fouls","total_fta","home_margin","home_win","home_fta_diff"]
for when,gid,names,gs in events:
    row={"season":gs["season"],"game_id":gid,"game_date":when.isoformat(),"official_count":len(names),
         "officials":"|".join(names),"pregame_only_feature":True}
    for w in WINDOWS:
        per=[]
        for name in names:
            hh=hist[name][-w:]
            if len(hh)>=max(5,min(w,10)):
                vals={}
                for m in metrics:
                    xx=[z[m] for z in hh if z.get(m) is not None]
                    vals[m]=sum(xx)/len(xx) if xx else None
                per.append(vals)
        row[f"crew_officials_with_{w}_history"]=len(per)
        for m in metrics:
            vals=[x[m] for x in per if x.get(m) is not None]
            row[f"crew_{m}_last{w}_avg"]=sum(vals)/len(vals) if vals else None
            row[f"crew_{m}_last{w}_min"]=min(vals) if vals else None
            row[f"crew_{m}_last{w}_max"]=max(vals) if vals else None
    out.append(row)
    for name in names:hist[name].append(gs)

wgz(OUT/"official_crew_pregame.csv.gz",out)
summary={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(out),"unique_officials":len(hist),
         "windows":list(WINDOWS),"metrics":metrics,
         "policy":"Every official tendency uses only that official's games strictly before the target game."}
(OUT/"official_crew_pregame_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
