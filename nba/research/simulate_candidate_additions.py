#!/usr/bin/env python3
import contextlib,io,json,runpy,csv
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

NBA=Path(__file__).resolve().parents[1]
OUT=NBA/"research"/"bankroll";OUT.mkdir(parents=True,exist_ok=True)
START=50000.0;BASE=0.01
SEASONS=["2018-19","2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26"]

buf=io.StringIO()
with contextlib.redirect_stdout(buf):
    live_ns=runpy.run_path(str(NBA/"research"/"audit_live_arsenal_losses.py"))
with contextlib.redirect_stdout(buf):
    h3_ns=runpy.run_path(str(NBA/"research"/"audit_hunt_v3_over.py"))
methods=live_ns["methods"];games=live_ns["games"]
h3_rows=h3_ns["qual"]("NBA_H3_OVER_002")

def dt(v):
    try:return datetime.fromisoformat(str(v).replace("Z","+00:00")).astimezone(timezone.utc)
    except:return datetime.min.replace(tzinfo=timezone.utc)
def num(v):
    try:return float(v)
    except:return None
def up(price,outcome):
    p=num(price)
    if p is None or abs(p)<100:raise ValueError(f"bad price {price}")
    if outcome=="push":return 0.0
    if outcome=="loss":return -1.0
    return p/100 if p>0 else 100/abs(p)

base_signals=[]
for mid,rr in methods.items():
    total=(mid=="NBA_STYLE_TOT_001")
    for r in rr:
        gid=r["game_id"];g=games.get(gid,{})
        season=r.get("season") or g.get("season")
        if season not in SEASONS:continue
        if total:
            res=r.get("result");outcome="win" if res==1 else ("push" if res==0.5 else "loss")
            key=(gid,"total_over","OVER")
            sel="OVER";price=float(r["price"]);line=num(r.get("line") or r.get("total_line"))
        else:
            outcome="win" if bool(r.get("won")) else "loss"
            tid=str(r.get("team_id") or "");sel=r.get("team") or tid
            key=(gid,"moneyline",tid or sel);price=float(r["price"]);line=None
        base_signals.append({"season":season,"game_id":gid,"when":dt(g.get("game_date")),"game_date":g.get("game_date"),
          "method_id":mid,"market":key[1],"selection":sel,"price":price,"line":line,"outcome":outcome,"bet_key":key})

h3_signals=[]
for r in h3_rows:
    gid=r["game_id"];g=games.get(gid,{})
    season=r.get("season") or g.get("season")
    if season not in SEASONS:continue
    out=r.get("out");outcome="win" if out==1 else ("push" if out==0.5 else "loss")
    h3_signals.append({"season":season,"game_id":gid,"when":dt(g.get("game_date")),"game_date":g.get("game_date"),
      "method_id":"NBA_H3_OVER_002","market":"total_over","selection":"OVER","price":float(r["median"]),
      "line":num(r.get("total_line")),"outcome":outcome,"bet_key":(gid,"total_over","OVER")})

# STAND descendant uses the already-frozen STAND parent and one discovery-only
# starter-form gate found in the early-period loss-forensics pass.
stand_child=[]
for r in methods["NBA_STAND_002"]:
    x=num(r.get("starter_pm5_gap"))
    if x is None or x<0.92:continue
    gid=r["game_id"];g=games.get(gid,{})
    season=r.get("season") or g.get("season")
    if season not in SEASONS:continue
    tid=str(r.get("team_id") or "");sel=r.get("team") or tid
    stand_child.append({"season":season,"game_id":gid,"when":dt(g.get("game_date")),"game_date":g.get("game_date"),
      "method_id":"NBA_STAND_002_STARTPM","market":"moneyline","selection":sel,"price":float(r["price"]),
      "line":None,"outcome":"win" if bool(r.get("won")) else "loss","bet_key":(gid,"moneyline",tid or sel)})

def simulate(signals):
    grouped=defaultdict(list)
    for s in signals:grouped[s["bet_key"]].append(s)
    bets=[]
    for key,arr in grouped.items():
        arr=sorted(arr,key=lambda z:z["method_id"]);b=arr[0]
        prices={round(x["price"],8) for x in arr};outs={x["outcome"] for x in arr}
        if len(prices)!=1 or len(outs)!=1:raise RuntimeError(f"inconsistent overlap {key}: {prices} {outs}")
        bets.append({**b,"methods":[x["method_id"] for x in arr],"method_count":len(arr),"stake_fraction":BASE*len(arr)})
    bets.sort(key=lambda x:(x["when"],x["game_id"],x["market"],x["selection"]))
    bank=START;peak=START;maxdd=0.0;maxdd_dollars=0.0;total_stake=0.0
    season_start={};season_end={};overlaps=defaultdict(int);method=defaultdict(lambda:{"signals":0,"wins":0,"losses":0,"pushes":0,"pnl":0.0})
    bytime=defaultdict(list)
    for b in bets:bytime[b["when"]].append(b)
    for when in sorted(bytime):
        batch=bytime[when];basebank=bank;pnl_batch=0.0;staged=[]
        for b in batch:
            season_start.setdefault(b["season"],basebank)
            stake=basebank*b["stake_fraction"];u=up(b["price"],b["outcome"]);pnl=stake*u
            total_stake+=stake;pnl_batch+=pnl;overlaps[b["method_count"]]+=1;staged.append((b,pnl))
        bank=basebank+pnl_batch;peak=max(peak,bank)
        dd=(peak-bank)/peak if peak else 0
        if dd>maxdd:maxdd=dd;maxdd_dollars=peak-bank
        for b,pnl in staged:
            season_end[b["season"]]=bank
            each=pnl/b["method_count"]
            for mid in b["methods"]:
                x=method[mid];x["signals"]+=1;x["pnl"]+=each
                if b["outcome"]=="win":x["wins"]+=1
                elif b["outcome"]=="loss":x["losses"]+=1
                else:x["pushes"]+=1
    wins=sum(1 for b in bets if b["outcome"]=="win");losses=sum(1 for b in bets if b["outcome"]=="loss");pushes=sum(1 for b in bets if b["outcome"]=="push")
    seasons=[]
    prev=START
    for s in SEASONS:
        st=season_start.get(s,prev);en=season_end.get(s,st);prev=en
        seasons.append({"season":s,"starting_bankroll":st,"ending_bankroll":en,"profit":en-st,"return":en/st-1 if st else None})
    return {"ending_bankroll":bank,"net_profit":bank-START,"total_return":bank/START-1,
      "cagr_8yr":(bank/START)**(1/8)-1 if bank>0 else None,
      "max_drawdown_fraction":maxdd,"max_drawdown_dollars":maxdd_dollars,
      "method_signals":len(signals),"unique_wagers":len(bets),"wins":wins,"losses":losses,"pushes":pushes,
      "unique_wager_hit_rate":wins/(wins+losses) if wins+losses else None,
      "overlap_distribution":{str(k):v for k,v in sorted(overlaps.items())},
      "total_dollars_staked":total_stake,"season_results":seasons,"method_attribution":dict(method)}

scenarios={
 "current_8_live":base_signals,
 "current_plus_H3_OVER_002":base_signals+h3_signals,
 "current_plus_STAND_002_STARTPM":base_signals+stand_child,
 "current_plus_both_candidates":base_signals+h3_signals+stand_child
}
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),
 "starting_bankroll":START,"sizing":"1% bankroll per method signal; identical game/market/selection signals stack; simultaneous tips use same pre-batch bankroll.",
 "candidate_signal_counts":{"NBA_H3_OVER_002":len(h3_signals),"NBA_STAND_002_STARTPM":len(stand_child)},
 "scenarios":{k:simulate(v) for k,v in scenarios.items()},
 "policy":"Decision support only. Candidate methods remain shadow unless separately promoted; no candidate result is used to change its historical threshold."}
(OUT/"candidate_addition_50k_scenarios.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
