#!/usr/bin/env python3
import csv,gzip,itertools,json,math
from collections import defaultdict
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1];F=NBA/"features";OUT=NBA/"research"/"cold_start";OUT.mkdir(parents=True,exist_ok=True)
A={"2019-20","2020-21","2021-22"};B={"2022-23"};V={"2023-24"};H={"2024-25","2025-26"};ALL=A|B|V|H

def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def n(v):
    if v in (None,"","None","nan","NaN"):return None
    try:return float(v)
    except:return None
def t(v):return str(v).lower() in ("true","1","yes")
def prev(s):
    a=int(s[:4]);return f"{a-1}-{str(a)[-2:]}"
def pf(a,w):
    a=n(a)
    if a is None or abs(a)<100:return None
    return (a/100 if a>0 else 100/abs(a)) if w else -1.0
def wilson(w,nn,z=1.96):
    if nn<=0:return None
    p=w/nn;den=1+z*z/nn
    return (p+z*z/(2*nn)-z*math.sqrt((p*(1-p)+z*z/(4*nn))/nn))/den
def met(rr):
    vals=[(r,pf(r["price"],r["won"])) for r in rr];vals=[x for x in vals if x[1] is not None]
    if not vals:return {"n":0,"wins":0,"losses":0,"hit":None,"roi":None,"wilson_low":None}
    w=sum(1 for r,_ in vals if r["won"]);pr=sum(p for _,p in vals)
    return {"n":len(vals),"wins":w,"losses":len(vals)-w,"hit":w/len(vals),"roi":pr/len(vals),"profit":pr,"wilson_low":wilson(w,len(vals))}
def d(a,b,k):
    x=n(a.get(k) if a else None);y=n(b.get(k) if b else None)
    return x-y if x is not None and y is not None else None
def q(v,p):
    v=sorted(x for x in v if x is not None)
    return v[min(len(v)-1,int(p*(len(v)-1)))] if v else None

roll=rgz(F/"team_rolling.csv.gz")
# Latest pregame rolling state from prior season for each team; still point-in-time and excludes that season's last target game.
last={}
for r in roll:
    key=(r.get("season"),r.get("team_id"))
    if not all(key):continue
    if key not in last or str(r.get("game_date") or "")>str(last[key].get("game_date") or ""):last[key]=r

cur={(r.get("game_id"),r.get("team_id")):r for r in roll}
cont={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"starter_prior_season_continuity.csv.gz")}
coach={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"coach_event_pregame.csv.gz")}
travel={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"travel_pregame.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}

games={}
for p in (NBA/"data").glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):
        if g.get("season") in ALL and t(g.get("completed")):games[g["game_id"]]=g

rows=[]
for gid,g in games.items():
    m=market.get(gid)
    if not m:continue
    hs=n(g.get("home_score"));aas=n(g.get("away_score"))
    if hs is None or aas is None:continue
    for home in (True,False):
        tid=g.get("home_team_id") if home else g.get("away_team_id");oid=g.get("away_team_id") if home else g.get("home_team_id")
        cr=cur.get((gid,tid),{});cor=cur.get((gid,oid),{})
        gp=n(cr.get("prior_games_available"));ogp=n(cor.get("prior_games_available"))
        if gp is None or ogp is None or max(gp,ogp)>10:continue
        pr=last.get((prev(g.get("season")),tid),{});por=last.get((prev(g.get("season")),oid),{})
        ct,co=cont.get((gid,tid),{}),cont.get((gid,oid),{})
        ch,cho=coach.get((gid,tid),{}),coach.get((gid,oid),{})
        tv,ov=travel.get((gid,tid),{}),travel.get((gid,oid),{})
        price=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
        prob=n(m.get("home_implied_probability_devig") if home else m.get("away_implied_probability_devig"))
        if price is None or prob is None:continue
        rows.append({
          "season":g.get("season"),"game_id":gid,"team_id":tid,"won":hs>aas if home else aas>hs,"price":price,
          "current_prior_games":gp,"market_prob":prob,"is_home":1.0 if home else 0.0,
          "prior_net10_gap":d(pr,por,"_net_rating_est_last10_avg"),"prior_ts10_gap":d(pr,por,"_true_shooting_est_last10_avg"),
          "prior_def10_adv":d(por,pr,"_def_rating_est_last10_avg"),"prior_efg10_gap":d(pr,por,"_e_fg_last10_avg"),
          "prior_threepa10_gap":d(pr,por,"_three_pa_rate_last10_avg"),
          "returning_starters_gap":d(ct,co,"returning_starters_from_prior_team"),
          "prior_minutes_share_gap":d(ct,co,"prior_team_starter_minutes_share"),
          "prior_points_share_gap":d(ct,co,"prior_team_starter_points_share"),
          "new_starters_adv":d(co,ct,"new_to_team_starters"),
          "return40_gap":d(ct,co,"returning_starters_40plus_games"),
          "coach_changes90_gap":d(ch,cho,"coach_events_prev_90d"),
          "travel7d_adv":d(ov,tv,"travel_miles_prev_7d_including_arrival")
        })

DOMAIN={
 "market_prob":"market","is_home":"context","current_prior_games":"current",
 "prior_net10_gap":"prior_strength","prior_ts10_gap":"prior_strength","prior_def10_adv":"prior_strength","prior_efg10_gap":"prior_strength","prior_threepa10_gap":"prior_strength",
 "returning_starters_gap":"continuity","prior_minutes_share_gap":"continuity","prior_points_share_gap":"continuity","new_starters_adv":"continuity","return40_gap":"continuity",
 "coach_changes90_gap":"coaching","travel7d_adv":"travel"
}
disc=[r for r in rows if r["season"] in A]
conds=[]
for f in DOMAIN:
    vals=[r.get(f) for r in disc if r.get(f) is not None]
    if len(vals)<150:continue
    if f=="is_home":
        conds += [(f,"==",0.0),(f,"==",1.0)];continue
    for qq in (.2,.35,.65,.8):
        cut=q(vals,qq)
        if cut is not None:conds.append((f,"<=" if qq<.5 else ">=",float(cut)))
def meet(r,cs):
    for f,o,v in cs:
        x=r.get(f)
        if x is None:return False
        if o=="==" and x!=v:return False
        if o==">=" and x<v:return False
        if o=="<=" and x>v:return False
    return True
def phase(cs,ss):return met([r for r in rows if r["season"] in ss and meet(r,cs)])

screen=[]
for c in conds:
    m=phase((c,),A)
    if m["n"]>=80 and m["hit"] is not None and m["roi"] is not None and (m["hit"]>=.63 or m["roi"]>=.025):screen.append((m["wilson_low"] or 0,m["roi"],c))
screen.sort(reverse=True,key=lambda x:(x[0],x[1]));pool=[x[2] for x in screen[:32]]
res=[];tested=0
for k in (2,3):
    src=pool if k==2 else pool[:22]
    for cs in itertools.combinations(src,k):
        fs={x[0] for x in cs}
        if len(fs)!=k:continue
        dom={DOMAIN.get(f) for f in fs}
        if "prior_strength" not in dom or len(dom)<2:continue
        tested+=1
        a=phase(cs,A);b=phase(cs,B);v=phase(cs,V);h=phase(cs,H)
        if a["n"]<55 or b["n"]<18 or v["n"]<18 or h["n"]<35:continue
        if any(x["hit"] is None or x["roi"] is None for x in (a,b,v,h)):continue
        if a["hit"]<.70 or a["roi"]<.035 or b["hit"]<.68 or b["roi"]<=0 or v["hit"]<.68 or v["roi"]<=0 or h["hit"]<.70 or h["roi"]<.04:continue
        res.append({"rule":" AND ".join(f"{f} {o} {vv:.4g}" for f,o,vv in cs),"domains":sorted(dom),"discA":a,"discB":b,"validation":v,"holdout":h})
res.sort(key=lambda x:(x["holdout"]["wilson_low"] or 0,x["holdout"]["roi"],x["holdout"]["n"]),reverse=True)
for i,r in enumerate(res,1):r["method_id"]=f"NBA_COLD_{i:03d}"
report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"rows":len(rows),"screened_conditions":len(screen),"rules_tested":tested,"candidates":len(res),"top_methods":res[:50],
 "policy":"Opening-10-game cold-start lane. Prior-season strength is used only as a separate historically validated feature family and must interact with another domain; existing live methods remain season-reset."}
(OUT/"cold_start_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
