#!/usr/bin/env python3
import contextlib,io,json,runpy,math,csv
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

NBA=Path(__file__).resolve().parents[1]
OUT=NBA/"research"/"bankroll";OUT.mkdir(parents=True,exist_ok=True)
START=50000.0
BASE_FRACTION=0.01
SEASONS=["2018-19","2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26"]

# Reuse the exact frozen live-method reconstruction so this simulation cannot drift
# from the rules/odds used by the current live arsenal.
buf=io.StringIO()
with contextlib.redirect_stdout(buf):
    ns=runpy.run_path(str(NBA/"research"/"audit_live_arsenal_losses.py"))
methods=ns["methods"]
games=ns["games"]

def dt(v):
    try:return datetime.fromisoformat(str(v).replace("Z","+00:00")).astimezone(timezone.utc)
    except:return datetime.min.replace(tzinfo=timezone.utc)
def num(v):
    try:return float(v)
    except:return None
def unit_profit(price,outcome):
    p=num(price)
    if p is None or abs(p)<100:raise ValueError(f"invalid American price {price}")
    if outcome=="push":return 0.0
    if outcome=="loss":return -1.0
    return p/100.0 if p>0 else 100.0/abs(p)

signals=[]
for mid,rr in methods.items():
    total=(mid=="NBA_STYLE_TOT_001")
    for r in rr:
        gid=r["game_id"];g=games.get(gid,{})
        when=dt(g.get("game_date"))
        season=r.get("season") or g.get("season")
        if season not in SEASONS:continue
        if total:
            res=r.get("result")
            outcome="win" if res==1 else ("push" if res==0.5 else "loss")
            key=(gid,"total_over","OVER")
            selection="OVER"
            team_id=""
            line=num(r.get("line") or r.get("total_line"))
        else:
            outcome="win" if bool(r.get("won")) else "loss"
            team_id=str(r.get("team_id") or "")
            selection=r.get("team") or team_id
            key=(gid,"moneyline",team_id or selection)
            line=None
        signals.append({
          "season":season,"game_id":gid,"game_date":g.get("game_date"),"when":when,
          "method_id":mid,"market":"total_over" if total else "moneyline",
          "selection":selection,"team_id":team_id,"price":float(r["price"] if "price" in r else r["ml"]),
          "line":line,"outcome":outcome,"bet_key":key
        })

# Every method contributes 1 percentage point of bankroll to the same underlying wager.
# Same game/market/selection therefore merges to 2%, 3%, ...; different sides/markets remain separate wagers.
grouped=defaultdict(list)
for s in signals:grouped[s["bet_key"]].append(s)
bets=[]
for key,arr in grouped.items():
    arr=sorted(arr,key=lambda x:x["method_id"])
    base=arr[0]
    # sanity: same underlying wager must have same outcome and price in our frozen reconstruction
    prices={round(x["price"],8) for x in arr};outs={x["outcome"] for x in arr}
    if len(prices)!=1 or len(outs)!=1:
        raise RuntimeError(f"inconsistent overlap {key}: prices={prices} outcomes={outs}")
    bets.append({
      "season":base["season"],"game_id":base["game_id"],"game_date":base["game_date"],"when":base["when"],
      "market":base["market"],"selection":base["selection"],"team_id":base["team_id"],
      "price":base["price"],"line":base["line"],"outcome":base["outcome"],
      "method_count":len(arr),"methods":[x["method_id"] for x in arr],
      "stake_fraction":BASE_FRACTION*len(arr)
    })
bets.sort(key=lambda x:(x["when"],x["game_id"],x["market"],x["selection"]))

bank=START
peak=START
max_dd=0.0
max_dd_dollars=0.0
ledger=[]
season_start={}
season_end={}
method_attrib=defaultdict(lambda:{"signals":0,"wins":0,"losses":0,"pushes":0,"profit_dollars":0.0})
overlap_count=defaultdict(int)

for b in bets:
    season=b["season"]
    season_start.setdefault(season,bank)
    stake=bank*b["stake_fraction"]
    u=unit_profit(b["price"],b["outcome"])
    pnl=stake*u
    before=bank
    bank+=pnl
    peak=max(peak,bank)
    dd=(peak-bank)/peak if peak>0 else 0
    dd_dollars=peak-bank
    if dd>max_dd:
        max_dd=dd;max_dd_dollars=dd_dollars
    season_end[season]=bank
    overlap_count[b["method_count"]]+=1
    # Attribute P&L equally per 1% method sleeve within an overlap.
    per_method_pnl=pnl/b["method_count"]
    for m in b["methods"]:
        a=method_attrib[m];a["signals"]+=1;a["profit_dollars"]+=per_method_pnl
        if b["outcome"]=="win":a["wins"]+=1
        elif b["outcome"]=="loss":a["losses"]+=1
        else:a["pushes"]+=1
    ledger.append({
      "season":season,"game_date":b["game_date"],"game_id":b["game_id"],"market":b["market"],"selection":b["selection"],
      "methods":"|".join(b["methods"]),"method_count":b["method_count"],"stake_fraction":b["stake_fraction"],
      "bankroll_before":before,"stake_dollars":stake,"american_odds":b["price"],"line":b["line"],"outcome":b["outcome"],
      "unit_profit_multiple":u,"pnl_dollars":pnl,"bankroll_after":bank
    })

season_rows=[]
for s in SEASONS:
    st=season_start.get(s)
    en=season_end.get(s,st)
    season_bets=[x for x in ledger if x["season"]==s]
    season_rows.append({
      "season":s,"starting_bankroll":st,"ending_bankroll":en,
      "profit":(en-st) if st is not None and en is not None else None,
      "return":((en/st)-1) if st and en is not None else None,
      "unique_wagers":len(season_bets),
      "method_signals":sum(x["method_count"] for x in season_bets),
      "total_stake_dollars":sum(x["stake_dollars"] for x in season_bets)
    })

wins=sum(1 for b in bets if b["outcome"]=="win")
losses=sum(1 for b in bets if b["outcome"]=="loss")
pushes=sum(1 for b in bets if b["outcome"]=="push")
total_method_signals=len(signals)
total_unique_wagers=len(bets)
total_stake=sum(x["stake_dollars"] for x in ledger)
profit=bank-START
years=8.0
cagr=(bank/START)**(1/years)-1 if bank>0 else None

summary={
 "generated_at_utc":datetime.now(timezone.utc).isoformat(),
 "window":{"start_season":SEASONS[0],"end_season":SEASONS[-1],"seasons":SEASONS,"note":"Validated live-method research window; 2017-18 is not included in all eight live methods."},
 "sizing_policy":{
   "starting_bankroll":START,
   "base_stake_fraction_per_method_signal":BASE_FRACTION,
   "compounding":"Each unique wager is staked as method_count × 1% of bankroll immediately before that wager in chronological game-tip order.",
   "overlap":"Same game + same market + same selection merges into one wager and adds another 1% stake for each additional qualifying live method.",
   "different_markets_or_opposite_sides":"Remain separate wagers.",
   "odds":"Archived median executable historical American price used by the frozen method backtests: moneyline median for ML methods and over-price median for STYLE_TOT_001.",
   "fees":"No additional fees/slippage beyond the archived sportsbook price."
 },
 "method_ids":sorted(methods),
 "total_method_signals":total_method_signals,
 "unique_wagers_after_overlap_merge":total_unique_wagers,
 "wins":wins,"losses":losses,"pushes":pushes,
 "unique_wager_hit_rate":wins/(wins+losses) if wins+losses else None,
 "overlap_distribution":{str(k):v for k,v in sorted(overlap_count.items())},
 "starting_bankroll":START,
 "ending_bankroll":bank,
 "net_profit":profit,
 "total_return":bank/START-1,
 "cagr_8yr":cagr,
 "max_drawdown_fraction":max_dd,
 "max_drawdown_dollars_at_peak_basis":max_dd_dollars,
 "total_dollars_staked":total_stake,
 "season_results":season_rows,
 "method_attribution":dict(sorted(method_attrib.items()))
}
(OUT/"live_arsenal_50k_1pct_compound.json").write_text(json.dumps(summary,indent=2)+"\n")

# Reproducible ledger.
fields=list(ledger[0].keys()) if ledger else []
with open(OUT/"live_arsenal_50k_1pct_ledger.csv","w",encoding="utf-8",newline="") as f:
    w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(ledger)

print(json.dumps(summary,indent=2))
