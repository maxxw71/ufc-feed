# Accepted-game odds alignment; provider extras never enter research truth.\n#!/usr/bin/env python3
import csv,gzip,json
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"
MARKET=ROOT/"market"/"historical"

def read(path):
    if not path.exists(): return []
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        return list(csv.DictReader(f))

def write(path,rows):
    rows=list(rows); fields=[]
    for r in rows:
        for k in r:
            if k not in fields: fields.append(k)
    if not fields: fields=["_empty"]
    with gzip.open(path,"wt",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)

def main():
    reports={}
    for season_dir in sorted(MARKET.glob("*")) if MARKET.exists() else []:
        if not season_dir.is_dir(): continue
        season=season_dir.name
        games_path=DATA/season/"regular_season"/"games.csv.gz"
        odds_path=season_dir/"odds.csv.gz"
        if not games_path.exists() or not odds_path.exists(): continue
        games=read(games_path); odds=read(odds_path)
        accepted=set(r.get("game_id") for r in games if r.get("game_id"))
        filtered=[r for r in odds if r.get("game_id") in accepted]
        write(season_dir/"odds_accepted.csv.gz",filtered)
        odds_games=set(r.get("game_id") for r in filtered if r.get("game_id"))
        reports[season]={
          "accepted_games":len(accepted),"accepted_games_with_odds":len(odds_games),
          "coverage":len(odds_games)/len(accepted) if accepted else 0,
          "raw_odds_rows":len(odds),"accepted_odds_rows":len(filtered),
          "provider_extra_game_ids":sorted(set(r.get("game_id") for r in odds if r.get("game_id"))-accepted),
          "generated_at_utc":datetime.now(timezone.utc).isoformat()
        }
        (season_dir/"accepted_summary.json").write_text(json.dumps(reports[season],indent=2)+"\n")
    print(json.dumps(reports,indent=2))

if __name__=="__main__": main()
