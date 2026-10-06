#!/usr/bin/env python3
import csv,gzip,json
from collections import defaultdict
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parent
DATA=NBA/"data"; F=NBA/"features"
SRC=F/"game_flow.csv.gz"; OUT=F
OUT.mkdir(parents=True,exist_ok=True)
WINDOWS=(3,5,10)

def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def n(v):
    try:return float(v)
    except:return None
def dt(v):
    try:return datetime.fromisoformat(str(v).replace("Z","+00:00"))
    except:return None
def wgz(p,rows):
    rows=list(rows);fs=[]
    for r in rows:
        for k in r:
            if k not in fs:fs.append(k)
    with gzip.open(p,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fs or ["_empty"]);w.writeheader();w.writerows(rows)

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):games[g.get("game_id")]=g
flow={r.get("game_id"):r for r in rgz(SRC)}

team_games=defaultdict(list)
for gid,g in games.items():
    f=flow.get(gid)
    when=dt(g.get("game_date"))
    if not f or not when:continue
    hm=n(f.get("halftime_margin"));fm=n(f.get("final_margin"))
    hlead=n(f.get("largest_home_lead"));alead=n(f.get("largest_away_lead"))
    for home in (True,False):
        tid=g.get("home_team_id") if home else g.get("away_team_id")
        if not tid:continue
        half=hm if home else (-hm if hm is not None else None)
        final=fm if home else (-fm if fm is not None else None)
        maxlead=hlead if home else alead
        maxdef=alead if home else hlead
        row={
          "season":g.get("season"),"game_id":gid,"game_date":g.get("game_date"),"team_id":tid,
          "halftime_margin_team":half,"final_margin_team":final,
          "second_half_plus_ot_margin":(final-half) if final is not None and half is not None else None,
          "largest_lead":maxlead,"largest_deficit":maxdef,
          "lead_changes":n(f.get("lead_changes_est")),"ties":n(f.get("ties_est")),
          "clutch_actions":n(f.get("clutch_actions_est")),"overtime_periods":n(f.get("overtime_periods")),
          "comeback_win":1.0 if half is not None and final is not None and half<0 and final>0 else 0.0,
          "blown_halftime_lead_loss":1.0 if half is not None and final is not None and half>0 and final<0 else 0.0,
          "won":1.0 if final is not None and final>0 else 0.0,
        }
        team_games[(g.get("season"),tid)].append((when,row))

metrics=["halftime_margin_team","final_margin_team","second_half_plus_ot_margin","largest_lead","largest_deficit",
         "lead_changes","ties","clutch_actions","overtime_periods","comeback_win","blown_halftime_lead_loss","won"]
out=[]
for (season,tid),arr in team_games.items():
    arr.sort(key=lambda x:x[0]);hist=[]
    for when,row in arr:
        feat={"season":season,"game_id":row["game_id"],"game_date":row["game_date"],"team_id":tid,"pregame_only_feature":True}
        for w in WINDOWS:
            prior=hist[-w:]
            for m in metrics:
                vals=[n(x.get(m)) for x in prior];vals=[x for x in vals if x is not None]
                feat[f"{m}_last{w}_avg"]=sum(vals)/len(vals) if vals else None
        out.append(feat);hist.append(row)

wgz(OUT/"team_flow_rolling.csv.gz",out)
summary={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(out),"team_seasons":len(team_games),
         "windows":list(WINDOWS),"metrics":metrics,"point_in_time":True}
(OUT/"team_flow_rolling_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
