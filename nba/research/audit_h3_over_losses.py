#!/usr/bin/env python3
import contextlib,csv,gzip,io,json,math,runpy,statistics
from pathlib import Path
from datetime import datetime,timezone

NBA=Path(__file__).resolve().parents[1];F=NBA/"features";OUT=NBA/"research"/"hunt_v3";OUT.mkdir(parents=True,exist_ok=True)

buf=io.StringIO()
with contextlib.redirect_stdout(buf):
    ns=runpy.run_path(str(NBA/"research"/"audit_hunt_v3_over.py"))
rows=ns["rows"];qual=ns["qual"];met=ns["met"];A=ns["A"];B=ns["B"];V=ns["V"];H=ns["H"]
parent=qual("NBA_H3_OVER_002")

def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def n(v):
    try:return float(v)
    except:return None
def idx(name):return {(r.get("game_id"),str(r.get("team_id") or "")):r for r in rgz(F/name)}
def d(a,b,k):
    x=n((a or {}).get(k));y=n((b or {}).get(k));return x-y if x is not None and y is not None else None
def absd(a,b,k):
    z=d(a,b,k);return abs(z) if z is not None else None
def sum2(a,b,k):
    x=n((a or {}).get(k));y=n((b or {}).get(k));return x+y if x is not None and y is not None else None
def q(vals,p):
    vals=sorted(x for x in vals if x is not None)
    return vals[min(len(vals)-1,int(p*(len(vals)-1)))] if vals else None
def phase(rr,ss,pk="median"):return met([r for r in rr if r["season"] in ss],pk)

games={}
for p in (NBA/"data").glob("*_*/regular_season/games.csv.gz"):
    for g in rgz(p):
        if g.get("game_id"):games[g["game_id"]]=g
roll=idx("team_rolling.csv.gz");ctx={(r.get("game_id"),str(r.get("team_id") or "")):r for r in rgz(NBA/"team_game_context.csv.gz")}
travel=idx("travel_pregame.csv.gz");lq=idx("lineup_quality_pregame.csv.gz");style=idx("team_style_rolling.csv.gz")
elo=idx("elo_sos_pregame.csv.gz");coach=idx("coach_event_pregame.csv.gz");tx=idx("transaction_pregame.csv.gz")
official={r.get("game_id"):r for r in rgz(F/"official_crew_pregame.csv.gz")}

extra=[]
for r in parent:
    g=games.get(r["game_id"],{});h=str(g.get("home_team_id") or "");a=str(g.get("away_team_id") or "")
    hr,ar=roll.get((r["game_id"],h),{}),roll.get((r["game_id"],a),{})
    hc,ac=ctx.get((r["game_id"],h),{}),ctx.get((r["game_id"],a),{})
    ht,at=travel.get((r["game_id"],h),{}),travel.get((r["game_id"],a),{})
    hl,al=lq.get((r["game_id"],h),{}),lq.get((r["game_id"],a),{})
    hs,as_=style.get((r["game_id"],h),{}),style.get((r["game_id"],a),{})
    he,ae=elo.get((r["game_id"],h),{}),elo.get((r["game_id"],a),{})
    hco,aco=coach.get((r["game_id"],h),{}),coach.get((r["game_id"],a),{})
    htx,atx=tx.get((r["game_id"],h),{}),tx.get((r["game_id"],a),{})
    oc=official.get(r["game_id"],{})
    rh=n(hc.get("days_since_prev_game"));ra=n(ac.get("days_since_prev_game"))
    t7h=n(ht.get("travel_miles_prev_7d_including_arrival"));t7a=n(at.get("travel_miles_prev_7d_including_arrival"))
    paces=[n(hr.get("_possessions_est_last5_avg")),n(ar.get("_possessions_est_last5_avg"))];paces=[x for x in paces if x is not None]
    z=dict(r)
    z.update({
      "pace5":statistics.mean(paces) if paces else None,
      "ts_sum":sum2(hr,ar,"_true_shooting_est_last5_avg"),
      "threepa_sum":sum2(hr,ar,"_three_pa_rate_last5_avg"),
      "net_abs_gap":absd(hr,ar,"_net_rating_est_last5_avg"),
      "rest_sum":rh+ra if rh is not None and ra is not None else None,
      "rest_abs_gap":abs(rh-ra) if rh is not None and ra is not None else None,
      "travel7d_sum":t7h+t7a if t7h is not None and t7a is not None else None,
      "travel7d_abs_gap":abs(t7h-t7a) if t7h is not None and t7a is not None else None,
      "lq_abs_gap":absd(hl,al,"current_start5_prior_pm48"),
      "paint_sum":sum2(hs,as_,"paint_share_last5_avg"),
      "foul_sum":sum2(hs,as_,"fouls_per100_last5_avg"),
      "oreb_sum":sum2(hs,as_,"oreb_rate_last5_avg"),
      "forced_tov_sum":sum2(hs,as_,"forced_turnovers_last5_avg"),
      "elo_abs_gap":absd(he,ae,"elo_pre"),
      "coach_change_sum":sum2(hco,aco,"coach_events_prev_30d"),
      "tx30_sum":sum2(htx,atx,"transactions_prev_30d"),
      "crew_fta25":n(oc.get("crew_total_fta_last25_avg")),
      "crew_fouls25":n(oc.get("crew_total_fouls_last25_avg"))
    })
    extra.append(z)

features=["pace5","ts_sum","threepa_sum","net_abs_gap","rest_sum","rest_abs_gap",
          "travel7d_sum","travel7d_abs_gap","lq_abs_gap","paint_sum","foul_sum","oreb_sum",
          "forced_tov_sum","elo_abs_gap","coach_change_sum","tx30_sum","crew_fta25","crew_fouls25"]

wins=[r for r in extra if r["out"]==1];losses=[r for r in extra if r["out"]==0]
contrasts=[]
for f in features:
    w=[r.get(f) for r in wins if r.get(f) is not None];l=[r.get(f) for r in losses if r.get(f) is not None]
    if len(w)>=20 and len(l)>=15:
        scale=statistics.pstdev(w+l) if len(w+l)>1 else 0
        contrasts.append({"feature":f,"win_mean":statistics.mean(w),"loss_mean":statistics.mean(l),
                          "win_median":statistics.median(w),"loss_median":statistics.median(l),
                          "standardized_win_minus_loss":(statistics.mean(w)-statistics.mean(l))/scale if scale else 0})
contrasts.sort(key=lambda x:abs(x["standardized_win_minus_loss"]),reverse=True)

parent_phase={name:phase(extra,ss) for name,ss in (("discA",A),("discB",B),("validation",V),("holdout",H))}
disc=[r for r in extra if r["season"] in A]
candidates=[]
for f in features:
    vals=[r.get(f) for r in disc if r.get(f) is not None]
    if len(vals)<70:continue
    for qq in (.20,.35,.65,.80):
        cut=q(vals,qq);op="<=" if qq<.5 else ">="
        if cut is None:continue
        def keep(r):
            x=r.get(f)
            return x is not None and (x<=cut if op=="<=" else x>=cut)
        rr=[r for r in extra if keep(r)]
        ph={name:phase(rr,ss) for name,ss in (("discA",A),("discB",B),("validation",V),("holdout",H))}
        a,b,v,h=[ph[x] for x in ("discA","discB","validation","holdout")]
        pa,pb,pv,phold=[parent_phase[x] for x in ("discA","discB","validation","holdout")]
        if a["n"]<35 or b["n"]<20 or v["n"]<18 or h["n"]<25:continue
        if any(x["roi"] is None or x["hit"] is None for x in (a,b,v,h)):continue
        if a["roi"]<(pa["roi"] or 0)+0.03 or a["hit"]<(pa["hit"] or 0)+0.015:continue
        if b["roi"]<=0 or v["roi"]<=0 or h["roi"]<=0:continue
        if h["hit"]<(phold["hit"] or 0)-0.02:continue
        worst=met(rr,"worst");hworst=met([r for r in rr if r["season"] in H],"worst")
        if worst["roi"] is None or worst["roi"]<=0 or hworst["roi"] is None or hworst["roi"]<=0:continue
        candidates.append({"feature":f,"op":op,"cut":cut,"phases":ph,
                           "overall":met(rr),"worst_price":worst,"holdout_worst_price":hworst,
                           "holdout_roi_lift":h["roi"]-(phold["roi"] or 0),
                           "holdout_hit_lift":h["hit"]-(phold["hit"] or 0)})
candidates.sort(key=lambda x:(x["holdout_roi_lift"],x["holdout_hit_lift"],x["phases"]["holdout"]["n"]),reverse=True)

diagnostic_caps={}
for name,pred in {
    "clutch_gap_under_4":lambda r:r.get("clutch_gap") is not None and r["clutch_gap"]<4,
    "starterpm_gap_under_10":lambda r:r.get("starterpm_gap") is not None and r["starterpm_gap"]<10,
    "starterpm_gap_15_plus":lambda r:r.get("starterpm_gap") is not None and r["starterpm_gap"]>=15
}.items():
    rr=[r for r in extra if pred(r)]
    diagnostic_caps[name]={
      "note":"Mechanism diagnostic only; not eligible for promotion because this slice was motivated after full-sample inspection.",
      "overall":met(rr),
      "phases":{k:phase(rr,ss) for k,ss in (("discA",A),("discB",B),("validation",V),("holdout",H))},
      "worst_price":met(rr,"worst")
    }

report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "method_id":"NBA_H3_OVER_002",
        "parent":{"overall":met(extra),"phases":parent_phase,"worst_price":met(extra,"worst")},
        "win_loss_contrasts":contrasts,
        "validated_extra_filter_candidates":candidates[:20],
        "posthoc_mechanism_diagnostics":diagnostic_caps,
        "policy":"Extra filters are learned only from 2018-21 H3_OVER_002 signals and must remain positive in 2021-23 confirmation, 2023-24 validation, 2024-26 holdout, and worst-price stress. Posthoc mechanism slices are reported separately and are never promotion-eligible."}
(OUT/"h3_over_002_loss_forensics.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps({"parent":report["parent"],"filters":report["validated_extra_filter_candidates"][:10],
                  "contrasts":report["win_loss_contrasts"][:8],"diagnostics":report["posthoc_mechanism_diagnostics"]},indent=2))
