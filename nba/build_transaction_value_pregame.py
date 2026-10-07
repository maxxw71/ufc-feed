#!/usr/bin/env python3
import csv,gzip,json,re
from collections import defaultdict
from datetime import date,datetime
from pathlib import Path

NBA=Path(__file__).resolve().parent
DATA=NBA/"data";TX=NBA/"transactions"/"bref_player_transaction_edges.csv.gz";OUT=NBA/"features"
OUT.mkdir(parents=True,exist_ok=True)

BREF_TO_ESPN={
 "ATL":"1","BOS":"2","BRK":"17","BKN":"17","CHO":"30","CHA":"30","CHI":"4","CLE":"5","DAL":"6","DEN":"7","DET":"8","GSW":"9",
 "HOU":"10","IND":"11","LAC":"12","LAL":"13","MEM":"29","MIA":"14","MIL":"15","MIN":"16","NOP":"3","NYK":"18","OKC":"25",
 "ORL":"19","PHI":"20","PHO":"21","PHX":"21","POR":"22","SAC":"23","SAS":"24","TOR":"28","UTA":"26","WAS":"27"
}
ESPN_TO_BREF={v:k for k,v in BREF_TO_ESPN.items() if k not in ("BKN","CHA","PHX")}

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
def d(v):
    try:return date.fromisoformat(str(v)[:10])
    except:return None
def num(v):
    try:return float(v)
    except:return 0.0
def norm(s):
    s=(s or "").lower()
    s=re.sub(r"\b(jr|sr|ii|iii|iv)\.?\b","",s)
    return re.sub(r"[^a-z0-9]","",s)

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):
        if g.get("game_id"):games[g["game_id"]]=g

apps=defaultdict(list)
for p in DATA.glob("*_*/regular_season/player_boxscores.csv.gz"):
    for r in rgz(p):
        g=games.get(r.get("game_id"))
        day=d(g.get("game_date") if g else None)
        name=norm(r.get("player_name"))
        if not day or not name or str(r.get("played")).lower() in ("false","0","dnp","none",""):continue
        apps[name].append({
          "date":day,"team_id":str(r.get("team_id") or ""),
          "minutes":num(r.get("minutes")),"points":num(r.get("points")),"plus_minus":num(r.get("plus_minus")),
          "usage":num(r.get("field_goals_attempted"))+0.44*num(r.get("free_throws_attempted"))+num(r.get("turnovers")),
          "starter":1.0 if str(r.get("starter")).lower() in ("true","1","yes") else 0.0
        })
for arr in apps.values():arr.sort(key=lambda x:x["date"])

edges=[]
for r in rgz(TX):
    day=d(r.get("date"));name=norm(r.get("player_name"))
    if not day or not name:continue
    arr=[x for x in apps.get(name,[]) if x["date"]<day]
    from_id=BREF_TO_ESPN.get((r.get("from_team_bref") or "").upper())
    if from_id:
        team_arr=[x for x in arr if x["team_id"]==from_id]
        if team_arr:arr=team_arr
    last=arr[-10:]
    def avg(k):return sum(x[k] for x in last)/len(last) if last else None
    edges.append({
      **r,
      "prior_appearances_10":len(last),
      "prior_minutes10_avg":avg("minutes"),
      "prior_points10_avg":avg("points"),
      "prior_plusminus10_avg":avg("plus_minus"),
      "prior_usage10_avg":avg("usage"),
      "prior_start_rate10":avg("starter"),
      "high_rotation_player":bool(last and avg("minutes")>=24),
      "star_minutes_player":bool(last and avg("minutes")>=30),
      "value_history_found":bool(last)
    })

wgz(NBA/"transactions"/"bref_player_transaction_edges_enriched.csv.gz",edges)

team_events=defaultdict(list)
for e in edges:
    day=d(e.get("date"))
    frm=(e.get("from_team_bref") or "").upper();to=(e.get("to_team_bref") or "").upper()
    if frm:team_events[frm].append((day,-1,e))
    if to:team_events[to].append((day,1,e))
for arr in team_events.values():arr.sort(key=lambda x:x[0])

rows=[]
for g in games.values():
    gd=d(g.get("game_date"))
    if not gd:continue
    for home in (True,False):
        tid=str(g.get("home_team_id") if home else g.get("away_team_id"))
        tc=ESPN_TO_BREF.get(tid)
        if not tc:continue
        prior=[x for x in team_events.get(tc,[]) if x[0]<gd]
        row={"season":g.get("season"),"game_id":g.get("game_id"),"game_date":g.get("game_date"),"team_id":tid,
             "team_bref":tc,"pregame_only_feature":True}
        for days in (7,14,30,60):
            recent=[x for x in prior if (gd-x[0]).days<=days]
            incoming=[e for _,sgn,e in recent if sgn>0]
            outgoing=[e for _,sgn,e in recent if sgn<0]
            row[f"incoming_value_events_{days}d"]=len(incoming);row[f"outgoing_value_events_{days}d"]=len(outgoing)
            for field,short in (("prior_minutes10_avg","minutes"),("prior_points10_avg","points"),("prior_plusminus10_avg","plusminus"),("prior_usage10_avg","usage")):
                inv=[num(e.get(field)) for e in incoming if e.get(field) not in (None,"")]
                outv=[num(e.get(field)) for e in outgoing if e.get(field) not in (None,"")]
                row[f"incoming_{short}_value_{days}d"]=sum(inv)
                row[f"outgoing_{short}_value_{days}d"]=sum(outv)
                row[f"net_{short}_value_{days}d"]=sum(inv)-sum(outv)
            row[f"incoming_high_rotation_{days}d"]=sum(1 for e in incoming if str(e.get("high_rotation_player")).lower() in ("true","1"))
            row[f"outgoing_high_rotation_{days}d"]=sum(1 for e in outgoing if str(e.get("high_rotation_player")).lower() in ("true","1"))
            row[f"incoming_star_minutes_{days}d"]=sum(1 for e in incoming if str(e.get("star_minutes_player")).lower() in ("true","1"))
            row[f"outgoing_star_minutes_{days}d"]=sum(1 for e in outgoing if str(e.get("star_minutes_player")).lower() in ("true","1"))
        rows.append(row)

wgz(OUT/"transaction_value_pregame.csv.gz",rows)
summary={"generated_at_utc":datetime.utcnow().isoformat()+"Z","edge_rows":len(edges),
         "edges_with_value_history":sum(1 for e in edges if e.get("value_history_found")),
         "feature_rows":len(rows),
         "policy":"Each transaction is weighted only by the player's NBA appearances strictly before the transaction date. Target games use only transactions from prior calendar dates."}
(OUT/"transaction_value_pregame_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
