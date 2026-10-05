# Unified NBA dataset coverage manifest.\n#!/usr/bin/env python3
import csv,gzip,json
from pathlib import Path
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parent

def rows(path):
    if not path.exists(): return []
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        return list(csv.DictReader(f))

manifest={
 "generated_at_utc":datetime.now(timezone.utc).isoformat(),
 "seasons":{},"layers":{}
}
for base in sorted((ROOT/"data").glob("*/*")) if (ROOT/"data").exists() else []:
    if not base.is_dir(): continue
    season=base.parent.name.replace("_","-")
    stype=base.name.replace("_"," ")
    manifest["seasons"].setdefault(season,{})[stype]={
      "games":len(rows(base/"games.csv.gz")),
      "team_boxscores":len(rows(base/"team_boxscores.csv.gz")),
      "player_boxscores":len(rows(base/"player_boxscores.csv.gz")),
      "playbyplay":len(rows(base/"playbyplay.csv.gz"))
    }

layer_paths={
 "roster_profiles":ROOT/"rosters"/"current_roster_profiles.csv.gz",
 "availability_snapshots":ROOT/"availability"/"availability_snapshots.csv.gz",
 "market_snapshots_espn":ROOT/"market"/"market_snapshots.csv.gz",
 "market_snapshots_external":ROOT/"market"/"external_bookmaker_snapshots.csv.gz",
 "team_rolling_features":ROOT/"features"/"team_rolling.csv.gz",
 "player_rolling_features":ROOT/"features"/"player_rolling.csv.gz",
 "pregame_strength":ROOT/"features"/"pregame_strength.csv.gz",
 "availability_impact_players":ROOT/"features"/"availability_impact_players.csv.gz",
 "team_game_context":ROOT/"team_game_context.csv.gz"
}
for name,path in layer_paths.items():
    manifest["layers"][name]={"present":path.exists(),"rows":len(rows(path)) if path.exists() else 0}

for meta in ["integrity_report.json","completeness_report.json","official_crosscheck_report.json","context_summary.json"]:
    p=ROOT/meta
    if p.exists():
        try: manifest["layers"][meta]=json.loads(p.read_text())
        except: manifest["layers"][meta]={"present":True,"parse_error":True}
    else:
        manifest["layers"][meta]={"present":False}

(ROOT/"dataset_manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
print(json.dumps(manifest,indent=2))
