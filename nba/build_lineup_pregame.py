#!/usr/bin/env python3
import csv,gzip,json
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path

NBA=Path(__file__).resolve().parent
DATA=NBA/"data"
SRC=NBA/"lineups"/"historical_lineup_stints.csv.gz"
OUT=NBA/"features"
OUT.mkdir(parents=True,exist_ok=True)

def read_gz(path):
    if not path.exists(): return []
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        return list(csv.DictReader(f))

def write_gz(path,rows):
    rows=list(rows);fields=[]
    for r in rows:
        for k in r:
            if k not in fields:fields.append(k)
    with gzip.open(path,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields or ["_empty"]);w.writeheader();w.writerows(rows)

def num(v):
    try:return float(v)
    except:return 0.0

def dt(v):
    try:return datetime.fromisoformat(str(v).replace("Z","+00:00"))
    except:return None

def lineup_set(s): return set(x for x in (s or "").split("|") if x)

game_meta={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in read_gz(p):
        game_meta[g.get("game_id")]=g

stints=read_gz(SRC)
by_team_game=defaultdict(list)
for r in stints:
    by_team_game[(r.get("season"),r.get("team_id"),r.get("game_id"))].append(r)

summaries=[]
for (season,tid,gid),arr in by_team_game.items():
    g=game_meta.get(gid)
    if not g:continue
    arr.sort(key=lambda r:num(r.get("stint_start_action")))
    start=lineup_set(arr[0].get("lineup_player_ids"))
    close=lineup_set(arr[-1].get("lineup_player_ids"))
    dur=Counter()
    players=set()
    for r in arr:
        lid=r.get("lineup_player_ids") or ""
        d=max(0.0,num(r.get("stint_duration_sec")))
        dur[lid]+=d
        players.update(lineup_set(lid))
    summaries.append({
      "season":season,"team_id":tid,"game_id":gid,"game_date":g.get("game_date"),
      "start_ids":start,"close_ids":close,"lineup_seconds":dur,"rotation_players":players,
      "stints":len(arr)
    })

grouped=defaultdict(list)
for x in summaries:
    when=dt(x["game_date"])
    if when:grouped[(x["season"],x["team_id"])].append((when,x))

rows=[]
for (season,tid),arr in grouped.items():
    arr.sort(key=lambda x:x[0])
    hist=[]
    for when,x in arr:
        row={
          "season":season,"game_id":x["game_id"],"game_date":x["game_date"],"team_id":tid,
          "confirmed_starting_five":"|".join(sorted(x["start_ids"])),
          "requires_confirmed_starters":True,"pregame_only_feature":True
        }
        prev=hist[-1] if hist else None
        if prev:
            row["starter_overlap_prev_game"]=len(x["start_ids"] & prev["start_ids"])
            row["starter_overlap_prev_closing_lineup"]=len(x["start_ids"] & prev["close_ids"])
            row["prev_game_rotation_players"]=len(prev["rotation_players"])
            row["prev_game_lineup_stints"]=prev["stints"]
        for n in (3,5):
            prior=hist[-n:]
            durations=Counter(); rotation=set(); stint_count=0
            for h in prior:
                durations.update(h["lineup_seconds"]);rotation.update(h["rotation_players"]);stint_count+=h["stints"]
            total=sum(durations.values())
            top=max(durations.values()) if durations else 0
            row[f"prior{n}_distinct_lineups"]=len(durations)
            row[f"prior{n}_top_lineup_share"]=top/total if total>0 else None
            row[f"prior{n}_rotation_players"]=len(rotation) if prior else None
            row[f"prior{n}_avg_stints_per_game"]=stint_count/len(prior) if prior else None
            starters=set()
            for h in prior:starters.update(h["start_ids"])
            row[f"prior{n}_distinct_starters"]=len(starters) if prior else None
        rows.append(row)
        hist.append(x)

write_gz(OUT/"lineup_pregame.csv.gz",rows)
summary={
 "generated_at_utc":datetime.now(timezone.utc).isoformat(),
 "rows":len(rows),"team_seasons":len(grouped),
 "fields":["starter_overlap_prev_game","starter_overlap_prev_closing_lineup","prior3/5_distinct_lineups",
           "prior3/5_top_lineup_share","prior3/5_rotation_players","prior3/5_avg_stints_per_game","prior3/5_distinct_starters"],
 "policy":"All rolling lineup history uses prior games only. Starter-overlap fields require the current confirmed starting five."
}
(OUT/"lineup_pregame_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
