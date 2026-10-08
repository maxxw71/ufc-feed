#!/usr/bin/env python3
"""Inspect 2023-24 extracted report shape before player-level feature production."""
import csv,gzip,json,re
from pathlib import Path
from datetime import datetime,timezone
NBA=Path(__file__).resolve().parent
ROOT=NBA/"availability"/"historical_by_season"
def rgz(p):
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
out={}
for season in ("2021_22","2022_23","2023_24"):
    folder=ROOT/season
    p=folder/"report_texts.csv.gz"
    c=folder/"coverage_games.csv.gz"
    if not p.exists() or not c.exists():continue
    report=rgz(p);games=rgz(c)
    game_by_url={}
    for g in games:game_by_url.setdefault(g["report_url"],g)
    selected=[]
    for r in report[:5]:
        text=r["report_text"];g=game_by_url.get(r["report_url"],{})
        ls=[s.strip() for s in text.splitlines() if s.strip()]
        selected.append({
          "url":r["report_url"],
          "home":g.get("home_team"),"away":g.get("away_team"),
          "both_teams_literal":bool(g.get("home_team") and g.get("away_team") and g["home_team"].lower() in text.lower() and g["away_team"].lower() in text.lower()),
          "first_25_lines":ls[:25],
          "terms":{"Injury Report":len(re.findall("Injury Report",text,re.I)),
                   "status_terms":len(re.findall(r"\\b(?:Out|Available|Questionable|Probable|Doubtful)\\b",text,re.I))}
        })
    out[season]={"reports":len(report),"games":len(games),"samples":selected}
out["generated_at_utc"]=datetime.now(timezone.utc).isoformat()
p=ROOT/"report_format_diagnostic.json";p.write_text(json.dumps(out,indent=2)+"\n")
print(json.dumps({s:{"reports":v["reports"],"samples":[{"home":x["home"],"away":x["away"],"lines":x["first_25_lines"][:6]} for x in v["samples"][:2]]} for s,v in out.items() if isinstance(v,dict)},indent=2))
