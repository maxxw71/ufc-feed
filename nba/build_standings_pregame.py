#!/usr/bin/env python3
import csv,gzip,json
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

NBA=Path(__file__).resolve().parent;DATA=NBA/"data";OUT=NBA/"features";OUT.mkdir(parents=True,exist_ok=True)
EAST={1,2,4,5,11,14,15,17,18,19,20,27,28,30,23}  # SAC corrected below by explicit WEST
WEST={3,6,7,8,9,10,12,13,16,21,22,23,24,25,26,29}
# Atlanta,Boston,Chicago,Cleveland,Indiana,Miami,Milwaukee,Brooklyn,NYK,Orlando,Philadelphia,Washington,Toronto,Charlotte + Detroit(8)
EAST={1,2,4,5,8,11,14,15,17,18,19,20,27,28,30}

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
def dt(v):
    try:return datetime.fromisoformat(str(v).replace("Z","+00:00"))
    except:return None
def n(v):
    try:return float(v)
    except:return None

games=[]
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    games.extend(rgz(p))
games=[g for g in games if dt(g.get("game_date"))]
games.sort(key=lambda g:dt(g.get("game_date")))

state=defaultdict(lambda:{"w":0,"l":0,"streak":0,"last10":[]})
rows=[]
def conf(tid):return "E" if int(tid) in EAST else "W"
def pct(s):
    q=s["w"]+s["l"];return s["w"]/q if q else None

for g in games:
    season=g.get("season");hid=g.get("home_team_id");aid=g.get("away_team_id")
    if not hid or not aid:continue
    # Snapshot ranks before this game.
    ranks={}
    cuts={}
    for c in ("E","W"):
        tids={tid for (ss,tid) in state if ss==season and conf(tid)==c}
        # ensure all teams seen in target game can enter rank before first result
        tids|={x for x in (hid,aid) if conf(x)==c}
        vals=[]
        for tid in tids:
            s=state[(season,tid)];gp=s["w"]+s["l"];wp=pct(s)
            vals.append((tid,wp if wp is not None else -1,gp,s["w"]))
        vals.sort(key=lambda x:(x[1],x[3]),reverse=True)
        for i,(tid,wp,gp,w) in enumerate(vals,1):ranks[tid]=i if wp>=0 else None
        valid=[x for x in vals if x[1]>=0]
        cuts[c]={
          "seed6":valid[5][1] if len(valid)>=6 else None,
          "seed10":valid[9][1] if len(valid)>=10 else None
        }
    for home,tid in ((True,hid),(False,aid)):
        s=state[(season,tid)];gp=s["w"]+s["l"];wp=pct(s);c=conf(tid);rank=ranks.get(tid)
        c6=cuts[c]["seed6"];c10=cuts[c]["seed10"]
        last10=s["last10"][-10:]
        rows.append({
          "season":season,"game_id":g.get("game_id"),"game_date":g.get("game_date"),"team_id":tid,"conference":c,
          "prior_games":gp,"prior_win_pct":wp,"conference_rank":rank,
          "top6":bool(rank and rank<=6) if rank else None,
          "playin_7_10":bool(rank and 7<=rank<=10) if rank else None,
          "outside_playin":bool(rank and rank>10) if rank else None,
          "winpct_gap_to_seed6":wp-c6 if None not in (wp,c6) else None,
          "winpct_gap_to_seed10":wp-c10 if None not in (wp,c10) else None,
          "abs_gap_seed6":abs(wp-c6) if None not in (wp,c6) else None,
          "abs_gap_seed10":abs(wp-c10) if None not in (wp,c10) else None,
          "current_streak":s["streak"],
          "last10_win_pct":sum(last10)/len(last10) if last10 else None,
          "late_40plus":gp>=40,"late_55plus":gp>=55,"late_65plus":gp>=65,
          "pregame_only_feature":True
        })
    completed=str(g.get("completed")).lower() in ("true","1");hs=n(g.get("home_score"));aas=n(g.get("away_score"))
    if not completed or hs is None or aas is None:continue
    hw=hs>aas
    for tid,win in ((hid,hw),(aid,not hw)):
        s=state[(season,tid)]
        if win:s["w"]+=1
        else:s["l"]+=1
        s["last10"].append(1 if win else 0)
        if win:s["streak"]=s["streak"]+1 if s["streak"]>=0 else 1
        else:s["streak"]=s["streak"]-1 if s["streak"]<=0 else -1

wgz(OUT/"standings_pregame.csv.gz",rows)
summary={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(rows),
         "policy":"Conference rank and seed-cut gaps are recomputed only from results completed before each target game. No future standings are used."}
(OUT/"standings_pregame_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
