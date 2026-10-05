#!/usr/bin/env python3
import csv,gzip,json,statistics
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
HIST=ROOT/"market"/"historical"
OUT=ROOT/"features"
OUT.mkdir(parents=True,exist_ok=True)

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

def num(v):
    try:return float(v)
    except:return None

def median(vals):
    x=[num(v) for v in vals]; x=[v for v in x if v is not None]
    return statistics.median(x) if x else None

def minv(vals):
    x=[num(v) for v in vals]; x=[v for v in x if v is not None]
    return min(x) if x else None

def maxv(vals):
    x=[num(v) for v in vals]; x=[v for v in x if v is not None]
    return max(x) if x else None

NON_EXECUTABLE_PROVIDER_TOKENS=("live odds","accuscore","consensus","numberfire","teamrankings","betegy","betradar","opening")

def provider_is_executable(name):
    s=(name or "").lower()
    return bool(s) and not any(tok in s for tok in NON_EXECUTABLE_PROVIDER_TOKENS)

def american_price(v):
    a=num(v)
    if a is None or abs(a)<100:return None
    return a

def implied(american):
    a=american_price(american)
    if a is None:return None
    return (-a)/((-a)+100) if a<0 else 100/(a+100)

rows_out=[]
season_summary={}
for season_dir in sorted(HIST.glob("*")) if HIST.exists() else []:
    if not season_dir.is_dir():continue
    src=season_dir/"odds_accepted.csv.gz"
    if not src.exists():continue
    rows=read(src)
    by_game=defaultdict(list)
    for r in rows:
        if r.get("game_id"):by_game[r["game_id"]].append(r)
    built=0
    for gid,grp in by_game.items():
        pre=[r for r in grp if provider_is_executable(r.get("provider"))]
        if not pre:continue
        providers=sorted(set(r.get("provider") for r in pre if r.get("provider")))
        first=pre[0]
        hml=[american_price(r.get("home_moneyline")) for r in pre]; aml=[american_price(r.get("away_moneyline")) for r in pre]
        spreads=[r.get("spread") for r in pre]; totals=[r.get("over_under") for r in pre]
        home_spread_prices=[american_price(r.get("home_spread_odds")) for r in pre]; away_spread_prices=[american_price(r.get("away_spread_odds")) for r in pre]
        over_prices=[american_price(r.get("over_odds")) for r in pre]; under_prices=[american_price(r.get("under_odds")) for r in pre]
        open_totals=[r.get("open_total") for r in pre]
        open_hs=[r.get("open_home_spread") for r in pre]; open_as=[r.get("open_away_spread") for r in pre]
        row={
          "season":first.get("season") or season_dir.name.replace("_","-"),
          "game_id":gid,"game_date":first.get("game_date"),
          "home_team":first.get("home_team"),"home_tricode":first.get("home_tricode"),
          "away_team":first.get("away_team"),"away_tricode":first.get("away_tricode"),
          "pregame_provider_count":len(providers),"pregame_providers":"|".join(providers),
          "closing_spread_median":median(spreads),"closing_spread_min":minv(spreads),"closing_spread_max":maxv(spreads),
          "closing_total_median":median(totals),"closing_total_min":minv(totals),"closing_total_max":maxv(totals),
          "home_moneyline_median":median(hml),"home_moneyline_best":maxv(hml),"home_moneyline_worst":minv(hml),
          "away_moneyline_median":median(aml),"away_moneyline_best":maxv(aml),"away_moneyline_worst":minv(aml),
          "home_spread_price_median":median(home_spread_prices),"away_spread_price_median":median(away_spread_prices),
          "home_spread_price_best":maxv(home_spread_prices),"home_spread_price_worst":minv(home_spread_prices),
          "away_spread_price_best":maxv(away_spread_prices),"away_spread_price_worst":minv(away_spread_prices),
          "over_price_median":median(over_prices),"over_price_best":maxv(over_prices),"over_price_worst":minv(over_prices),
          "under_price_median":median(under_prices),"under_price_best":maxv(under_prices),"under_price_worst":minv(under_prices),
          "open_total_median":median(open_totals),"open_home_spread_median":median(open_hs),
          "open_away_spread_median":median(open_as),
          "contains_live_rows_excluded":len(grp)>len(pre),
          "source":"espn_core_historical_odds_nonlive"
        }
        if row["home_moneyline_median"] is not None:
            row["home_implied_probability_raw"]=implied(row["home_moneyline_median"])
        if row["away_moneyline_median"] is not None:
            row["away_implied_probability_raw"]=implied(row["away_moneyline_median"])
        hp=row.get("home_implied_probability_raw"); ap=row.get("away_implied_probability_raw")
        if hp is not None and ap is not None and hp+ap>0:
            row["home_implied_probability_devig"]=hp/(hp+ap)
            row["away_implied_probability_devig"]=ap/(hp+ap)
            row["moneyline_hold"]=hp+ap-1
        ot=row.get("open_total_median"); ct=row.get("closing_total_median")
        if ot is not None and ct is not None:row["total_move"]=ct-ot
        oh=row.get("open_home_spread_median"); cs=row.get("closing_spread_median")
        if oh is not None and cs is not None:row["spread_move"]=cs-oh
        rows_out.append(row);built+=1
    season_summary[season_dir.name.replace("_","-")]={
      "accepted_odds_rows":len(rows),"games_with_raw_accepted_odds":len(by_game),
      "games_with_nonlive_pregame_features":built
    }

write(OUT/"historical_market_features.csv.gz",rows_out)
summary={
 "generated_at_utc":datetime.now(timezone.utc).isoformat(),
 "games":len(rows_out),"seasons":season_summary,
 "policy":"Executable sportsbook providers only for price/ROI features. Live odds, consensus/model feeds, data-provider feeds and Opening pseudo-provider rows are excluded; American prices require absolute value >=100."
}
(OUT/"historical_market_features_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
