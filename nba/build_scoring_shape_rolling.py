#!/usr/bin/env python3
import csv,gzip,json,re
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

NBA=Path(__file__).resolve().parent
DATA=NBA/"data";OUT=NBA/"features"
OUT.mkdir(parents=True,exist_ok=True)
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
def ni(v):
    try:return int(float(v))
    except:return None
def dt(v):
    try:return datetime.fromisoformat(str(v).replace("Z","+00:00"))
    except:return None
def truth(v):return str(v).lower() in ("true","1","yes","made")
def clocksec(s):
    if not s:return None
    s=str(s)
    if s.startswith("PT"):
        m=re.search(r"(\d+)M",s);q=re.search(r"([\d.]+)S",s)
        return 60*(int(m.group(1)) if m else 0)+(float(q.group(1)) if q else 0)
    try:
        a,b=s.split(":");return int(a)*60+float(b)
    except:return None

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):games[g.get("game_id")]=g
plays=defaultdict(list)
for p in DATA.glob("*_*/regular_season/playbyplay.csv.gz"):
    for r in rgz(p):
        if r.get("game_id"):plays[r["game_id"]].append(r)

team_games=defaultdict(list)
for gid,g in games.items():
    when=dt(g.get("game_date"));pp=plays.get(gid)
    if not when or not pp:continue
    pp.sort(key=lambda r:ni(r.get("action_number")) or 0)
    qend={};prev_h=prev_a=0
    clutch={"home_pts":0.0,"away_pts":0.0,"home_fga":0.0,"away_fga":0.0,"home_fgm":0.0,"away_fgm":0.0,"home_tov":0.0,"away_tov":0.0,"poss_actions":0.0}
    hid=str(g.get("home_team_id"));aid=str(g.get("away_team_id"))
    for r in pp:
        hs=n(r.get("score_home"));aw=n(r.get("score_away"));per=ni(r.get("period"))
        if hs is None or aw is None or per is None:continue
        before_diff=prev_h-prev_a
        sec=clocksec(r.get("clock"))
        if per==4 and sec is not None and sec<=300 and abs(before_diff)<=5:
            dh=max(0,hs-prev_h);da=max(0,aw-prev_a)
            clutch["home_pts"]+=dh;clutch["away_pts"]+=da;clutch["poss_actions"]+=1
            if truth(r.get("is_field_goal")):
                tid=str(r.get("team_id"))
                if tid==hid:
                    clutch["home_fga"]+=1
                    if dh>0 or truth(r.get("shot_result")):clutch["home_fgm"]+=1
                elif tid==aid:
                    clutch["away_fga"]+=1
                    if da>0 or truth(r.get("shot_result")):clutch["away_fgm"]+=1
            if "turnover" in str(r.get("action_type") or "").lower():
                tid=str(r.get("team_id"))
                if tid==hid:clutch["home_tov"]+=1
                elif tid==aid:clutch["away_tov"]+=1
        qend[per]=(hs,aw);prev_h,prev_a=hs,aw

    def qpts(side,q):
        cur=qend.get(q)
        if not cur:return None
        idx=0 if side=="home" else 1
        prev=qend.get(q-1,(0,0))[idx] if q>1 else 0
        return cur[idx]-prev
    for home in (True,False):
        tid=hid if home else aid
        if not tid:continue
        own="home" if home else "away";opp="away" if home else "home"
        row={"season":g.get("season"),"game_id":gid,"game_date":g.get("game_date"),"team_id":tid}
        for q in (1,2,3,4):
            op=qpts(own,q);ap=qpts(opp,q)
            row[f"q{q}_points"]=op
            row[f"q{q}_margin"]=(op-ap) if op is not None and ap is not None else None
        h1=sum(v for v in (row.get("q1_margin"),row.get("q2_margin")) if v is not None)
        h2=sum(v for v in (row.get("q3_margin"),row.get("q4_margin")) if v is not None)
        row["first_half_margin"]=h1;row["second_half_margin"]=h2
        row["q3_response_margin"]=row.get("q3_margin")
        row["late_game_margin"]=row.get("q4_margin")
        cp=clutch[f"{own}_pts"];ca=clutch[f"{opp}_pts"];fga=clutch[f"{own}_fga"];fgm=clutch[f"{own}_fgm"]
        row["clutch_points"]=cp;row["clutch_points_allowed"]=ca;row["clutch_margin"]=cp-ca
        row["clutch_fga"]=fga;row["clutch_fgm"]=fgm;row["clutch_fg_pct"]=fgm/fga if fga>0 else None
        row["clutch_turnovers"]=clutch[f"{own}_tov"];row["clutch_actions"]=clutch["poss_actions"]
        team_games[(g.get("season"),tid)].append((when,row))

metrics=["q1_points","q1_margin","q2_points","q2_margin","q3_points","q3_margin","q4_points","q4_margin",
         "first_half_margin","second_half_margin","q3_response_margin","late_game_margin",
         "clutch_points","clutch_points_allowed","clutch_margin","clutch_fga","clutch_fgm","clutch_fg_pct","clutch_turnovers","clutch_actions"]
out=[]
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

wgz(OUT/"scoring_shape_rolling.csv.gz",out)
summary={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(out),"team_seasons":len(team_games),
         "windows":list(WINDOWS),"metrics":metrics,"point_in_time":True,
         "clutch_definition":"Q4 final 5:00 with score margin <=5 before action; score deltas and shot/turnover events accumulated."}
(OUT/"scoring_shape_rolling_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
