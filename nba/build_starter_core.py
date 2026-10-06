#!/usr/bin/env python3
import csv,gzip,json,statistics
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parent
F=NBA/"features"
LINE=F/"lineup_pregame.csv.gz"
PR=F/"player_rolling.csv.gz"

def rgz(p):
    if not p.exists(): return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:
        return list(csv.DictReader(f))

def n(v):
    try: return float(v)
    except: return None

def wgz(p,rows):
    rows=list(rows); fs=[]
    for r in rows:
        for k in r:
            if k not in fs: fs.append(k)
    p.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(p,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fs or ["_empty"])
        w.writeheader(); w.writerows(rows)

pr={(r.get("game_id"),r.get("person_id")):r for r in rgz(PR)}
rows=[]

for r in rgz(LINE):
    ids=[x for x in (r.get("confirmed_starting_five") or "").split("|") if x]
    if len(ids)!=5: continue
    ps=[pr.get((r.get("game_id"),pid),{}) for pid in ids]

    def vals(k):
        x=[n(p.get(k)) for p in ps]
        return [v for v in x if v is not None]

    p5=vals("points_last5_avg")
    a5=vals("assists_last5_avg")
    pm5=vals("plus_minus_last5_avg")
    m5=vals("minutes_last5_avg")
    p10=vals("points_last10_avg")
    a10=vals("assists_last10_avg")

    def top_share(v):
        s=sum(v)
        return max(v)/s if v and s>0 else None

    rows.append({
      "season":r.get("season"),
      "game_id":r.get("game_id"),
      "team_id":r.get("team_id"),
      "pregame_only_feature":True,
      "requires_confirmed_starters":True,
      "starter_points5_sum":sum(p5) if p5 else None,
      "starter_points5_top_share":top_share(p5),
      "starter_points5_std":statistics.pstdev(p5) if len(p5)>=2 else None,
      "starter_assists5_sum":sum(a5) if a5 else None,
      "starter_assists5_top_share":top_share(a5),
      "starter_assists5_std":statistics.pstdev(a5) if len(a5)>=2 else None,
      "starter_plusminus5_avg":statistics.mean(pm5) if pm5 else None,
      "starter_plusminus5_std":statistics.pstdev(pm5) if len(pm5)>=2 else None,
      "starter_minutes5_avg":statistics.mean(m5) if m5 else None,
      "starter_minutes5_std":statistics.pstdev(m5) if len(m5)>=2 else None,
      "starter_points10_sum":sum(p10) if p10 else None,
      "starter_points10_top_share":top_share(p10),
      "starter_assists10_sum":sum(a10) if a10 else None,
      "starter_assists10_top_share":top_share(a10)
    })

wgz(F/"starter_core_pregame.csv.gz",rows)
summary={
  "generated_at_utc":datetime.now(timezone.utc).isoformat(),
  "rows":len(rows),
  "policy":"Confirmed-starter, point-in-time player rolling features only; current game excluded from all player rolling inputs."
}
(F/"starter_core_pregame_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
