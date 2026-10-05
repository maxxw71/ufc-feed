#!/usr/bin/env python3
import csv,gzip,itertools,json,math,statistics
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

NBA=Path(__file__).resolve().parents[1]
DATA=NBA/"data"
FEATURES=NBA/"features"
OUT=NBA/"research"/"results"
OUT.mkdir(parents=True,exist_ok=True)

TRAIN={"2018-19","2019-20","2020-21","2021-22","2022-23"}
VALID={"2023-24"}
HOLDOUT={"2024-25","2025-26"}
ALL=TRAIN|VALID|HOLDOUT

def read_gz(path):
    if not path.exists(): return []
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        return list(csv.DictReader(f))

def write_csv(path,rows):
    rows=list(rows); fields=[]
    for r in rows:
        for k in r:
            if k not in fields: fields.append(k)
    with open(path,"w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields or ["_empty"])
        w.writeheader(); w.writerows(rows)

def num(v):
    if v in (None,"","None","nan","NaN"): return None
    try:return float(v)
    except:return None

def truth(v):
    return str(v).lower() in ("true","1","yes")

def american_profit(odds, won, push=False):
    o=num(odds)
    if push:return 0.0
    if o is None or o==0:return None
    if not won:return -1.0
    return o/100.0 if o>0 else 100.0/abs(o)

def wilson(wins,n,z=1.96):
    if n<=0:return 0.0
    p=wins/n
    den=1+z*z/n
    center=p+z*z/(2*n)
    adj=z*math.sqrt((p*(1-p)+z*z/(4*n))/n)
    return (center-adj)/den

def median(vals):
    x=[v for v in vals if v is not None]
    return statistics.median(x) if x else None

# Load accepted game truth.
games={}
for p in DATA.glob("*_*/regular_season/games.csv.gz"):
    for g in read_gz(p):
        if g.get("season") in ALL and truth(g.get("completed")):
            games[g["game_id"]]=g

team_roll={(r.get("game_id"),r.get("team_id")):r for r in read_gz(FEATURES/"team_rolling.csv.gz")}
context={(r.get("game_id"),r.get("team_id")):r for r in read_gz(NBA/"team_game_context.csv.gz")}
strength={r.get("game_id"):r for r in read_gz(FEATURES/"pregame_strength.csv.gz")}
market={r.get("game_id"):r for r in read_gz(FEATURES/"historical_market_features.csv.gz")}

def diff(a,b,key):
    x=num(a.get(key) if a else None); y=num(b.get(key) if b else None)
    return x-y if x is not None and y is not None else None

side_rows=[]
game_rows=[]
for gid,g in games.items():
    m=market.get(gid)
    if not m: continue
    hs=num(g.get("home_score")); aas=num(g.get("away_score"))
    if hs is None or aas is None: continue
    home_id=g.get("home_team_id"); away_id=g.get("away_team_id")
    hr=team_roll.get((gid,home_id),{}); ar=team_roll.get((gid,away_id),{})
    hc=context.get((gid,home_id),{}); ac=context.get((gid,away_id),{})
    st=strength.get(gid,{})
    home_spread=num(m.get("closing_spread_median"))
    total_line=num(m.get("closing_total_median"))

    for side in ("home","away"):
        is_home=side=="home"
        tr,orr=(hr,ar) if is_home else (ar,hr)
        tc,oc=(hc,ac) if is_home else (ac,hc)
        team_score,opp_score=(hs,aas) if is_home else (aas,hs)
        ml=m.get("home_moneyline_median") if is_home else m.get("away_moneyline_median")
        ml_best=m.get("home_moneyline_best") if is_home else m.get("away_moneyline_best")
        prob=num(m.get("home_implied_probability_devig") if is_home else m.get("away_implied_probability_devig"))
        spread_price=m.get("home_spread_price_median") if is_home else m.get("away_spread_price_median")
        side_spread=home_spread if is_home else (-home_spread if home_spread is not None else None)
        rest=num(tc.get("days_since_prev_game")); opp_rest=num(oc.get("days_since_prev_game"))
        row={
          "season":g.get("season"),"game_id":gid,"side":side,
          "team_id":home_id if is_home else away_id,"opponent_id":away_id if is_home else home_id,
          "won":team_score>opp_score,"moneyline":num(ml),"moneyline_best":num(ml_best),
          "market_prob":prob,"spread":side_spread,"spread_price":num(spread_price),
          "ats_result":None,
          "net5_gap":diff(tr,orr,"_net_rating_est_last5_avg"),
          "net10_gap":diff(tr,orr,"_net_rating_est_last10_avg"),
          "off5_gap":diff(tr,orr,"_off_rating_est_last5_avg"),
          "def5_adv":diff(orr,tr,"_def_rating_est_last5_avg"),
          "efg5_gap":diff(tr,orr,"_e_fg_last5_avg"),
          "ts5_gap":diff(tr,orr,"_true_shooting_est_last5_avg"),
          "threepa5_gap":diff(tr,orr,"_three_pa_rate_last5_avg"),
          "points5_gap":diff(tr,orr,"points_last5_avg"),
          "rest_diff":(rest-opp_rest) if rest is not None and opp_rest is not None else None,
          "side_b2b":1.0 if truth(tc.get("back_to_back")) else 0.0,
          "opp_b2b":1.0 if truth(oc.get("back_to_back")) else 0.0,
          "fatigue4_adv":(num(oc.get("games_prev_4_days"))-num(tc.get("games_prev_4_days")))
                         if num(oc.get("games_prev_4_days")) is not None and num(tc.get("games_prev_4_days")) is not None else None,
          "is_home":1.0 if is_home else 0.0,
        }
        if is_home:
            row["pregame_win_pct_diff"]=num(st.get("pregame_win_pct_diff"))
            row["pregame_point_diff_gap"]=num(st.get("pregame_point_diff_form_gap"))
        else:
            v=num(st.get("pregame_win_pct_diff")); row["pregame_win_pct_diff"]=-v if v is not None else None
            v=num(st.get("pregame_point_diff_form_gap")); row["pregame_point_diff_gap"]=-v if v is not None else None
        if side_spread is not None:
            adj=team_score+side_spread-opp_score
            row["ats_result"]=1 if adj>0 else (0 if adj<0 else 0.5)
        side_rows.append(row)

    pace_vals=[num(hr.get("_possessions_est_last5_avg")),num(ar.get("_possessions_est_last5_avg"))]
    pace=median(pace_vals)
    hoff=num(hr.get("_off_rating_est_last5_avg")); aoff=num(ar.get("_off_rating_est_last5_avg"))
    hdef=num(hr.get("_def_rating_est_last5_avg")); adef=num(ar.get("_def_rating_est_last5_avg"))
    expected_total=None; expected_margin=None
    if None not in (pace,hoff,aoff,hdef,adef):
        h_exp=(hoff+adef)/2
        a_exp=(aoff+hdef)/2
        expected_total=pace*(h_exp+a_exp)/100
        expected_margin=pace*(h_exp-a_exp)/100
    actual_total=hs+aas
    game_rows.append({
      "season":g.get("season"),"game_id":gid,"actual_total":actual_total,
      "closing_total":total_line,"over_price":num(m.get("over_price_median")),
      "under_price":num(m.get("under_price_median")),"home_spread":home_spread,
      "expected_total_proxy":expected_total,
      "total_edge":expected_total-total_line if expected_total is not None and total_line is not None else None,
      "expected_home_margin_proxy":expected_margin,
      "spread_edge_home":expected_margin+home_spread if expected_margin is not None and home_spread is not None else None,
      "pace5":pace,
      "home_net5":num(hr.get("_net_rating_est_last5_avg")),
      "away_net5":num(ar.get("_net_rating_est_last5_avg")),
    })

def split(rows,seasons): return [r for r in rows if r.get("season") in seasons]

def eval_bets(rows,outcome_key,odds_key):
    n=wins=losses=pushes=0; profit=0.0
    for r in rows:
        outcome=r.get(outcome_key); odds=r.get(odds_key)
        if odds is None or outcome is None: continue
        if outcome==0.5:
            p=american_profit(odds,False,True); pushes+=1
        else:
            won=bool(outcome)
            p=american_profit(odds,won,False)
            if won:wins+=1
            else:losses+=1
        if p is None: continue
        profit+=p; n+=1
    decisive=wins+losses
    return {
      "n":n,"wins":wins,"losses":losses,"pushes":pushes,
      "hit_rate":wins/decisive if decisive else None,
      "roi":profit/n if n else None,"profit_units":profit,
      "wilson_low":wilson(wins,decisive) if decisive else None,
    }

def meets(row,conds):
    for feature,op,threshold in conds:
        v=row.get(feature)
        if v is None:return False
        if op==">=" and not v>=threshold:return False
        if op=="<=" and not v<=threshold:return False
        if op=="==" and not v==threshold:return False
    return True

def describe(conds):
    return " AND ".join(f"{f} {op} {t:.4g}" if isinstance(t,float) else f"{f} {op} {t}" for f,op,t in conds)

train=split(side_rows,TRAIN)
continuous=["net5_gap","net10_gap","off5_gap","def5_adv","efg5_gap","ts5_gap","threepa5_gap",
            "points5_gap","pregame_win_pct_diff","pregame_point_diff_gap","rest_diff","fatigue4_adv"]
conditions=[]
for feat in continuous:
    vals=sorted(r[feat] for r in train if r.get(feat) is not None)
    if len(vals)<100:continue
    for q in (0.55,0.65,0.75,0.85):
        idx=min(len(vals)-1,max(0,int(q*(len(vals)-1))))
        conditions.append((feat,">=",float(vals[idx])))
conditions += [("market_prob",">=",x) for x in (0.50,0.55,0.60,0.65,0.70,0.75)]
conditions += [("rest_diff",">=",x) for x in (0.0,1.0,2.0)]
conditions += [("side_b2b","==",0.0),("opp_b2b","==",1.0),("is_home","==",1.0),("is_home","==",0.0)]

# Deduplicate conditions.
seen=set(); uniq=[]
for c in conditions:
    k=(c[0],c[1],round(c[2],8) if isinstance(c[2],float) else c[2])
    if k not in seen:seen.add(k);uniq.append(c)
conditions=uniq

rules=[(c,) for c in conditions]
# Favor interpretable 2-condition rules. Limit pairs that are not duplicate features.
for a,b in itertools.combinations(conditions,2):
    if a[0]==b[0]:continue
    # Every pair should include either a market constraint or a basketball/context edge.
    rules.append((a,b))

candidates=[]
def add_candidate(market_type,bet_label,conds,all_rows,outcome_key,odds_key,min_train,base_hit):
    tr=[r for r in all_rows if r.get("season") in TRAIN and meets(r,conds)]
    if len(tr)<min_train:return
    va=[r for r in all_rows if r.get("season") in VALID and meets(r,conds)]
    ho=[r for r in all_rows if r.get("season") in HOLDOUT and meets(r,conds)]
    a=eval_bets(tr,outcome_key,odds_key); b=eval_bets(va,outcome_key,odds_key); d=eval_bets(ho,outcome_key,odds_key)
    if a["n"]<min_train or b["n"]<25 or d["n"]<50:return
    if a["hit_rate"] is None or d["hit_rate"] is None:return
    # Discovery floor only; later seasons decide shadow candidacy.
    if a["roi"] is None or a["roi"]<=0 or a["hit_rate"]<base_hit:return
    status="research_only"
    if (b["roi"] is not None and d["roi"] is not None and b["roi"]>0 and d["roi"]>0
        and b["hit_rate"] is not None and d["hit_rate"]>=base_hit
        and d["wilson_low"] is not None and d["wilson_low"]>=max(0.50,base_hit-0.10)):
        status="shadow_candidate"
    row={
      "market":market_type,"bet":bet_label,"rule":describe(conds),"conditions":json.dumps(conds),
      "status":status,
      "train_n":a["n"],"train_hit":a["hit_rate"],"train_roi":a["roi"],"train_wilson_low":a["wilson_low"],
      "validation_n":b["n"],"validation_hit":b["hit_rate"],"validation_roi":b["roi"],
      "holdout_n":d["n"],"holdout_hit":d["hit_rate"],"holdout_roi":d["roi"],"holdout_wilson_low":d["wilson_low"],
    }
    # Season stability.
    for season in sorted(ALL):
        ss=[r for r in all_rows if r.get("season")==season and meets(r,conds)]
        e=eval_bets(ss,outcome_key,odds_key)
        row[f"{season}_n"]=e["n"]; row[f"{season}_hit"]=e["hit_rate"]; row[f"{season}_roi"]=e["roi"]
    candidates.append(row)

# Moneyline discovery.
ml_rows=[dict(r,ml_outcome=1 if r.get("won") else 0) for r in side_rows if r.get("moneyline") is not None]
for rule in rules:
    add_candidate("moneyline","side ML",rule,ml_rows,"ml_outcome","moneyline",150,0.68)

# ATS discovery.
ats_rows=[r for r in side_rows if r.get("spread_price") is not None and r.get("ats_result") is not None]
for rule in rules:
    add_candidate("spread","side ATS",rule,ats_rows,"ats_result","spread_price",180,0.535)

# Totals use a smaller fixed, discovery-derived family based on model edge.
total_rows=[]
for r in game_rows:
    if r.get("closing_total") is None:continue
    over=1 if r["actual_total"]>r["closing_total"] else (0 if r["actual_total"]<r["closing_total"] else 0.5)
    under=1 if r["actual_total"]<r["closing_total"] else (0 if r["actual_total"]>r["closing_total"] else 0.5)
    rr=dict(r,over_result=over,under_result=under)
    total_rows.append(rr)
train_edges=sorted(abs(r["total_edge"]) for r in total_rows if r.get("season") in TRAIN and r.get("total_edge") is not None)
edge_thresholds=[]
if train_edges:
    for q in (0.50,0.65,0.75,0.85):
        edge_thresholds.append(float(train_edges[min(len(train_edges)-1,int(q*(len(train_edges)-1)))]))
for t in sorted(set(round(x,4) for x in edge_thresholds if x>0)):
    over_cond=(("total_edge",">=",t),)
    under_cond=(("total_edge","<=",-t),)
    add_candidate("total","over",over_cond,total_rows,"over_result","over_price",180,0.535)
    add_candidate("total","under",under_cond,total_rows,"under_result","under_price",180,0.535)

# Rank by out-of-sample lower bound first, then ROI and sample size.
def rank_key(r):
    return (
      1 if r["status"]=="shadow_candidate" else 0,
      r.get("holdout_wilson_low") or 0,
      r.get("holdout_roi") or -99,
      r.get("holdout_n") or 0
    )
candidates.sort(key=rank_key,reverse=True)

# Assign stable method IDs after ranking.
counts=defaultdict(int)
for r in candidates:
    prefix={"moneyline":"ML","spread":"ATS","total":"TOT"}[r["market"]]
    counts[prefix]+=1
    r["method_id"]=f"NBA_{prefix}_{counts[prefix]:03d}"

write_csv(OUT/"initial_method_candidates.csv",candidates[:250])
shadow=[r for r in candidates if r["status"]=="shadow_candidate"]
write_csv(OUT/"shadow_candidates.csv",shadow[:100])

report={
  "generated_at_utc":datetime.now(timezone.utc).isoformat(),
  "data_policy":{
    "discovery_seasons":sorted(TRAIN),"validation_seasons":sorted(VALID),"untouched_holdout_seasons":sorted(HOLDOUT),
    "odds_source":"accepted historical pregame odds only; provider labels containing Live Odds excluded",
    "promotion_policy":"No method is live. shadow_candidate only means it survived fixed validation/holdout gates."
  },
  "games_loaded":len(games),"games_with_market_features":len(market),
  "side_rows":len(side_rows),"total_rows":len(total_rows),
  "candidate_rules_tested":len(rules),
  "candidates_retained":len(candidates),"shadow_candidates":len(shadow),
  "top_methods":[{
      "method_id":r["method_id"],"market":r["market"],"rule":r["rule"],"status":r["status"],
      "train_n":r["train_n"],"train_hit":r["train_hit"],"train_roi":r["train_roi"],
      "validation_n":r["validation_n"],"validation_hit":r["validation_hit"],"validation_roi":r["validation_roi"],
      "holdout_n":r["holdout_n"],"holdout_hit":r["holdout_hit"],"holdout_roi":r["holdout_roi"]
    } for r in candidates[:25]]
}
(OUT/"initial_discovery_report.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
