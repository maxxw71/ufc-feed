#!/usr/bin/env python3
import csv,gzip,json,math,statistics
from collections import defaultdict,Counter
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data";F=NBA/"features";OUT=NBA/"research"/"physical_coach"
OUT.mkdir(parents=True,exist_ok=True)
SEASONS=["2018-19","2019-20","2020-21","2021-22","2022-23","2023-24","2024-25","2025-26"]
A={"2018-19","2019-20","2020-21"}; B={"2021-22","2022-23"}; V={"2023-24"}; H={"2024-25","2025-26"}
GW=-10.0;BW=8.0;MP=0.5968

def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def n(v):
    if v in (None,"","None","nan","NaN"):return None
    try:return float(v)
    except:return None
def t(v):return str(v).lower() in ("true","1","yes")
def d(a,b,k,rev=False):
    x=n(a.get(k) if a else None);y=n(b.get(k) if b else None)
    if x is None or y is None:return None
    return (y-x) if rev else (x-y)
def pf(a,w):
    a=n(a)
    if a is None or abs(a)<100:return None
    return (a/100 if a>0 else 100/abs(a)) if w else -1.0
def met(rr):
    vals=[(r,pf(r.get("ml"),r["won"])) for r in rr];vals=[x for x in vals if x[1] is not None]
    if not vals:return {"n":0,"wins":0,"losses":0,"hit":None,"roi":None,"profit":0}
    w=sum(1 for r,_ in vals if r["won"]);pr=sum(p for _,p in vals)
    return {"n":len(vals),"wins":w,"losses":len(vals)-w,"hit":w/len(vals),"roi":pr/len(vals),"profit":pr}
def mean(vs):
    vs=[x for x in vs if x is not None]
    return statistics.mean(vs) if vs else None
def median(vs):
    vs=[x for x in vs if x is not None]
    return statistics.median(vs) if vs else None
def quant(vs,p):
    vs=sorted(x for x in vs if x is not None)
    if not vs:return None
    return vs[min(len(vs)-1,max(0,int(p*(len(vs)-1))))]

games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
  for g in rgz(p):
    if g.get("season") in SEASONS and t(g.get("completed")):games[g["game_id"]]=g
phys={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"starter_physical.csv.gz")}
roll={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"team_rolling.csv.gz")}
line={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"lineup_pregame.csv.gz")}
work={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"team_workload_pregame.csv.gz")}
flow={(r.get("game_id"),r.get("team_id")):r for r in rgz(F/"team_flow_rolling.csv.gz")}
ctx={(r.get("game_id"),r.get("team_id")):r for r in rgz(NBA/"team_game_context.csv.gz")}
coach={(r.get("season"),r.get("team_id")):r for r in rgz(F/"coach_continuity.csv.gz")}
market={r.get("game_id"):r for r in rgz(F/"historical_market_features.csv.gz")}

rows=[]
for gid,g in games.items():
    m=market.get(gid); hs=n(g.get("home_score"));as_=n(g.get("away_score"))
    if not m or hs is None or as_ is None:continue
    for home in (True,False):
      tid=g.get("home_team_id") if home else g.get("away_team_id"); oid=g.get("away_team_id") if home else g.get("home_team_id")
      tp,op=phys.get((gid,tid),{}),phys.get((gid,oid),{})
      gw=d(tp,op,"guard_weight");bw=d(tp,op,"big_weight")
      ml=n(m.get("home_moneyline_median") if home else m.get("away_moneyline_median"))
      prob=n(m.get("home_implied_probability_devig") if home else m.get("away_implied_probability_devig"))
      if None in (gw,bw,ml,prob) or not (gw<=GW and bw>=BW and prob>=MP):continue
      tr,orr=roll.get((gid,tid),{}),roll.get((gid,oid),{})
      tl,ol=line.get((gid,tid),{}),line.get((gid,oid),{})
      tw,ow=work.get((gid,tid),{}),work.get((gid,oid),{})
      tf,of=flow.get((gid,tid),{}),flow.get((gid,oid),{})
      tx,ox=ctx.get((gid,tid),{}),ctx.get((gid,oid),{})
      tc,oc=coach.get((g.get("season"),tid),{}),coach.get((g.get("season"),oid),{})
      rest=n(tx.get("days_since_prev_game"));orest=n(ox.get("days_since_prev_game"))
      row={"season":g.get("season"),"game_id":gid,"team":g.get("home_team") if home else g.get("away_team"),
           "opponent":g.get("away_team") if home else g.get("home_team"),"home":1.0 if home else 0.0,
           "ml":ml,"market_prob":prob,"won":hs>as_ if home else as_>hs,
           "guard_weight_gap":gw,"big_weight_gap":bw,
           "height_gap":d(tp,op,"starter_avg_height_inches"),"age_gap":d(tp,op,"starter_avg_age"),
           "experience_gap":d(tp,op,"starter_avg_experience"),
           "guard_height_gap":d(tp,op,"guard_height"),"big_height_gap":d(tp,op,"big_height"),
           "rest_diff":rest-orest if rest is not None and orest is not None else None,
           "net5_gap":d(tr,orr,"_net_rating_est_last5_avg"),"net10_gap":d(tr,orr,"_net_rating_est_last10_avg"),
           "off5_gap":d(tr,orr,"_off_rating_est_last5_avg"),"def5_adv":d(orr,tr,"_def_rating_est_last5_avg"),
           "ts5_gap":d(tr,orr,"_true_shooting_est_last5_avg"),"efg5_gap":d(tr,orr,"_effective_fg_est_last5_avg"),
           "threepa5_gap":d(tr,orr,"_three_point_attempt_rate_last5_avg"),
           "starter_overlap_gap":d(tl,ol,"starter_overlap_prev_game"),
           "starter_close_overlap_gap":d(tl,ol,"starter_overlap_prev_closing_lineup"),
           "distinct_starters_adv":d(ol,tl,"prior5_distinct_starters"),
           "top_lineup_share_gap":d(tl,ol,"prior5_top_lineup_share"),
           "rotation_size_adv":d(ol,tl,"prior5_rotation_players"),
           "top8_minutes_3d_adv":d(ow,tw,"top8_minutes_prev_3d"),
           "top8_minutes_7d_adv":d(ow,tw,"top8_minutes_prev_7d"),
           "heavy30_adv":d(ow,tw,"players_30plus_prior_game"),
           "ot_exposure_adv":d(ow,tw,"players_prev_game_ot_proxy"),
           "coach_tenure_gap":d(tc,oc,"consecutive_seasons_same_coach"),
           "secondhalf5_gap":d(tf,of,"second_half_plus_ot_margin_last5_avg"),
           "comeback5_gap":d(tf,of,"comeback_win_last5_avg"),
           "blownlead5_adv":d(of,tf,"blown_halftime_lead_loss_last5_avg"),
           "clutch5_gap":d(tf,of,"clutch_actions_last5_avg")}
      rows.append(row)

features=[k for k in rows[0] if k not in {"season","game_id","team","opponent","won","ml"}] if rows else []
s20=[r for r in rows if r["season"]=="2020-21"]
contrast=[]
for f in features:
    w=[n(r.get(f)) for r in s20 if r["won"]];l=[n(r.get(f)) for r in s20 if not r["won"]]
    w=[x for x in w if x is not None];l=[x for x in l if x is not None]
    if not w or not l:continue
    contrast.append({"feature":f,"wins_n":len(w),"losses_n":len(l),"win_mean":mean(w),"loss_mean":mean(l),
                     "loss_minus_win_mean":mean(l)-mean(w),"win_median":median(w),"loss_median":median(l)})
contrast.sort(key=lambda x:abs(x["loss_minus_win_mean"]),reverse=True)

# Team clustering in weak season.
team20=[]
for team in sorted(set(r["team"] for r in s20)):
    rr=[r for r in s20 if r["team"]==team]
    x=met(rr);x["team"]=team;team20.append(x)
team20.sort(key=lambda x:(x["losses"],x["n"]),reverse=True)

# Search simple one-feature filters. Cutoffs ONLY from parent signals in discovery A.
# Require improvement in A and positive performance in every later phase, with enough samples.
disc=[r for r in rows if r["season"] in A]
conds=[]
for f in features:
    vals=[n(r.get(f)) for r in disc];vals=[x for x in vals if x is not None]
    if len(vals)<120:continue
    for p in (0.20,0.35,0.50,0.65,0.80):
        c=quant(vals,p)
        if c is None:continue
        conds.append((f,"<=",c));conds.append((f,">=",c))
# exact home split also
conds += [("home","==",0.0),("home","==",1.0)]

def meet(r,c):
    f,o,v=c;x=n(r.get(f))
    if x is None:return False
    return x<=v if o=="<=" else x>=v if o==">=" else x==v
baseA=met([r for r in rows if r["season"] in A]);base20=met(s20)
filters=[]
seen=set()
for c in conds:
    key=(c[0],c[1],round(c[2],8))
    if key in seen:continue
    seen.add(key)
    sets={name:[r for r in rows if r["season"] in ss and meet(r,c)] for name,ss in [("discA",A),("discB",B),("validation",V),("holdout",H)]}
    m={k:met(v) for k,v in sets.items()};m20=met([r for r in s20 if meet(r,c)])
    if m["discA"]["n"]<70 or m["discB"]["n"]<40 or m["validation"]["n"]<25 or m["holdout"]["n"]<35:continue
    if m20["n"]<25:continue
    if (m20["hit"] or 0) <= (base20["hit"] or 0):continue
    if (m["discA"]["hit"] or 0) < (baseA["hit"] or 0):continue
    if any((m[k]["roi"] is None or m[k]["roi"]<=0) for k in ("discB","validation","holdout")):continue
    if any((m[k]["hit"] or 0)<0.75 for k in ("discB","validation","holdout")):continue
    filters.append({"feature":c[0],"op":c[1],"cut":c[2],"season2020":m20,**m})
filters.sort(key=lambda x:((x["holdout"]["hit"] or 0),(x["holdout"]["roi"] or 0),(x["season2020"]["hit"] or 0)),reverse=True)

report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"parent":met(rows),"season_2020_21":base20,
        "season_2020_21_team_clusters":team20,"season_2020_21_feature_contrasts":contrast,
        "generalizing_single_filters":filters[:50],
        "policy":"2020-21 is investigated as a weak season, but candidate filters use discovery-period cutpoints and must remain positive with >=75% hit in later confirmation, validation, and holdout. No 2020-only exclusions are accepted."}
(OUT/"physcoach_001_loss_forensics.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps({"parent":report["parent"],"2020_21":base20,"candidate_filters":len(filters),"top_filters":filters[:8]},indent=2))
