#!/usr/bin/env python3
"""Loss forensics for frozen H5 selections; no promotion/retuning.

Search cutoffs are learned exclusively from 2018-21 discovery outcomes.
2021-23 confirmation, 2023-24 validation, and 2024-26 holdout
are evaluated but never used to choose a filter or adjust thresholds.
Injury-specific tests use a later, nonoverlapping split because official
pre-tip report coverage starts in 2021-22. Missing reports are unknown.
"""
import contextlib,csv,gzip,io,json,math,runpy,statistics
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path

NBA=Path(__file__).resolve().parents[1];F=NBA/"features"
OUT=NBA/"research"/"hunt_v5";OUT.mkdir(parents=True,exist_ok=True)
with contextlib.redirect_stdout(io.StringIO()):
    base=runpy.run_path(str(OUT.parent/"audit_h5_candidates.py"))
rows=base["dataset"]; source=base["report"]; matches=base["matches"];cs_for=base["cs_for"]
metric=base["metrics"]; games=base["games"];YEARS=base["YEARS"]
A={"2018-19","2019-20","2020-21"};B={"2021-22","2022-23"};V={"2023-24"};H={"2024-25","2025-26"}
ids=("NBA_H5_ML_001","NBA_H5_ML_002","NBA_H5_ATS_001")
def rgz(p):
    if not p.exists():return []
    with gzip.open(p,"rt",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))
def idx(p):return {(r.get("game_id"),str(r.get("team_id"))):r for r in rgz(p)}
def n(x):
    try:
        v=float(x)
        return v if math.isfinite(v) else None
    except (ValueError,TypeError):return None
def diff(a,b,key):
    x=n(a.get(key));y=n(b.get(key))
    return x-y if x is not None and y is not None else None
def quantile(values,p):
    vals=sorted(x for x in values if x is not None)
    return vals[min(len(vals)-1,int((len(vals)-1)*p))] if vals else None
def summaries(rr):
    m=metric(rr)
    return {"n":m["n"],"raw_signals":len(rr),"wins":m["wins"],
            "losses":m["losses"],"pushes":m["pushes"],
            "roi":m["roi"],"median_roi":m["roi"],
            "worst_price_roi":metric(rr,"worst")["roi"],
            "profit_units":m["profit_units"]}
def contrast(rr,key):
    winners=[r.get(key) for r in rr if r["out"]==1 and r.get(key) is not None]
    losers=[r.get(key) for r in rr if r["out"]==0 and r.get(key) is not None]
    def agg(xs):
        return {"n":len(xs),"mean":statistics.mean(xs) if xs else None,
                "median":statistics.median(xs) if xs else None}
    return {"winner":agg(winners),"loser":agg(losers),
            "difference_win_minus_loss":statistics.mean(winners)-statistics.mean(losers) if winners and losers else None}
def filt(rr,spec):
    key,op,cut=spec
    return [r for r in rr if r.get(key) is not None and (r[key]<=cut if op=="<=" else r[key]>=cut)]
def eval_filter(parent,spec):
    kept=filt(parent,spec)
    removed=[r for r in parent if r not in kept] # all observations preserved
    p=summaries(parent);k=summaries(kept);rm=summaries(removed)
    return {"base":p,"kept":k,"removed":rm,
            "wins_lost":p["wins"]-k["wins"],
            "losses_avoided":p["losses"]-k["losses"],
            "roi_lift":k["roi"]-p["roi"] if k["roi"] is not None and p["roi"] is not None else None,
            "unit_profit_change":k["profit_units"]-p["profit_units"],
            "bets_retained_pct":k["n"]/p["n"] if p["n"] else None}
# Existing per-game pre-tip inputs are not calculated from eventual target result.
roll=idx(F/"team_rolling.csv.gz")
style=idx(F/"team_style_rolling.csv.gz")
travel=idx(F/"travel_pregame.csv.gz")
line=idx(F/"lineup_pregame.csv.gz")
work=idx(F/"team_workload_pregame.csv.gz")
injury=idx(F/"historical_injury_teams.csv.gz")

for r in rows:
    g=games.get(r["game_id"],{})
    tid=str(r.get("team_id"));hid=str(g.get("home_team_id"));aid=str(g.get("away_team_id"))
    oid=aid if tid==hid else hid
    own,opp=roll.get((r["game_id"],tid),{}),roll.get((r["game_id"],oid),{})
    so,sp=style.get((r["game_id"],tid),{}),style.get((r["game_id"],oid),{})
    to,tp=travel.get((r["game_id"],tid),{}),travel.get((r["game_id"],oid),{})
    wo,wp=work.get((r["game_id"],tid),{}),work.get((r["game_id"],oid),{})
    lo,lp=line.get((r["game_id"],tid),{}),line.get((r["game_id"],oid),{})
    io,ip=injury.get((r["game_id"],tid)),injury.get((r["game_id"],oid))
    r.update({
      "net5_gap":diff(own,opp,"_net_rating_est_last5_avg"),
      "ts5_gap":diff(own,opp,"_true_shooting_est_last5_avg"),
      "threepa5_gap":diff(own,opp,"_three_pa_rate_last5_avg"),
      "style_oreb_edge":diff(so,sp,"oreb_rate_last5_avg"),
      "style_foul_adv":diff(sp,so,"fouls_per100_last5_avg"),
      "travel_adv":diff(tp,to,"travel_miles_prev_7d_including_arrival"),
      "workload_adv":diff(wp,wo,"top5_minutes_prev_3d"),
      "starter_overlap_adv":diff(lo,lp,"starter_overlap_prev_game"),
      "prior_games_min":min(n(own.get("prior_games_available")),n(opp.get("prior_games_available"))) if n(own.get("prior_games_available")) is not None and n(opp.get("prior_games_available")) is not None else None,
      "injury_known":int(bool(io and ip)),
      "injury_minutes_adv":diff(ip,io,"weighted_missing_minutes_last5") if io and ip else None,
      "injury_points_adv":diff(ip,io,"weighted_missing_points_last5") if io and ip else None,
      "injury_out_count_adv":diff(ip,io,"out_players") if io and ip else None
    })

# Candidate-independent inputs, used only if they could have been observed pregame.
FEATURES=[
"price_prob","book_count","net5_gap","ts5_gap","threepa5_gap","style_oreb_edge",
"style_foul_adv","travel_adv","workload_adv","starter_overlap_adv","prior_games_min",
"prior_minutes_share_gap","starter_negative_adv","strength_pointdiff_gap","clutch10_gap",
"rank_gap","tx_minutes14_gap","rest_diff"
]
out={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"policy":{
    "discovery":"2018-19 through 2020-21: train-only filter cutoffs and sorting",
    "confirmation":"2021-22 through 2022-23: displayed out-of-training, never used for threshold selection",
    "validation":"2023-24: independent report only",
    "holdout":"2024-25 through 2025-26: frozen thresholds, never used for selection",
    "injury":"Injury report features unknown when official coverage absent. Separate injury exploratory split trains 2021-22, confirms 2022-23, validates 2023-24 and holds out 2024-26 once available.",
    "limitations":"Post-hoc candidate mining and many filter comparisons can inflate apparent edge even with chronological splits. No automatic rule mutation or live promotion."
},"methods":{}}
for mid in ids:
    parent_conds=cs_for(mid,source)
    parent_features={f for f,op,cut in parent_conds}
    market_name="ats" if "_ATS_" in mid else "moneyline"
    subset=[r for r in rows if r["market"]==market_name and matches(r,parent_conds)]
    disc=[r for r in subset if r["season"] in A]
    winloss_by_season={}
    for s in sorted(YEARS):
        z=[r for r in subset if r["season"]==s]
        winloss_by_season[s]={"baseline":summaries(z),
             "feature_contrasts":{f:contrast(z,f) for f in ("price_prob","net5_gap","ts5_gap","travel_adv","starter_negative_adv","rest_diff","injury_points_adv")}}
    # Market-cap check is an economic break-even diagnostic, *not* a discovered rule.
    fixed_price_gates={}
    for gate in (.60,.70,.80,.85):
        fixed_price_gates[f"implied_prob_below_{int(gate*100)}pct"]={
            k:eval_filter([r for r in subset if r["season"] in ss],("price_prob","<=",gate))
            for k,ss in (("discovery",A),("confirmation",B),("validation",V),("holdout",H))}
    # Exploratory loss-filter hunt: all candidate cuts are discovered only in A.
    candidates=[]
    for feature in FEATURES:
        if feature in parent_features:continue
        vals=[r[feature] for r in disc if r.get(feature) is not None]
        if len(vals)<max(30,int(.5*len(disc))):continue
        for op,quantiles in (("<=",(.20,.35)),(">=",(.65,.80))):
            for pct in quantiles:
                cut=quantile(vals,pct)
                if cut is None:continue
                sp=(feature,op,cut)
                train=eval_filter(disc,sp)
                # Must avoid enough TRAIN losses with disproportionate loss
                # removal; a filter that only drops profitable bets is not an edge.
                keep=train["kept"]
                if (keep["n"]<max(22,int(.35*len(disc))) or
                    keep["roi"] is None or keep["roi"]<(train["base"]["roi"] or 0)+.015 or
                    train["losses_avoided"]<2 or train["wins_lost"]>=len(disc)*.50):
                    continue
                pwin=train["base"]["wins"];ploss=train["base"]["losses"]
                win_removed_frac=train["wins_lost"]/pwin if pwin else 0
                loss_removed_frac=train["losses_avoided"]/ploss if ploss else 0
                if loss_removed_frac<=win_removed_frac:continue
                later={k:eval_filter([r for r in subset if r["season"] in ss],sp)
                       for k,ss in (("confirmation",B),("validation",V),("holdout",H))}
                # Sort solely by discovery outcomes, never by holdout ROI.
                score=(train["roi_lift"] or 0)*(train["bets_retained_pct"] or 0) + .15*(loss_removed_frac-win_removed_frac)
                candidates.append({"feature":feature,"operator":op,"cut":cut,"train_quantile":pct,
                                   "train_only_rank_score":score,
                                   "discovery_loss_elimination_rate":loss_removed_frac,
                                   "discovery_win_elimination_rate":win_removed_frac,
                                   "discovery":train,"later_phases":later,
                                   "heldout_worst_price_roi":metric(
                                       filt([r for r in subset if r["season"] in H],sp),"worst")["roi"],
                                   "qualifies_for_further_investigation":(
                                       later["confirmation"]["kept"]["n"]>=12 and
                                       later["validation"]["kept"]["n"]>=8 and
                                       later["holdout"]["kept"]["n"]>=12 and
                                       all(later[k]["roi_lift"] is not None and later[k]["roi_lift"]>0 for k in ("confirmation","validation","holdout")) and
                                       (metric(filt([r for r in subset if r["season"] in H],sp),"worst")["roi"] or -999)>0)})
    candidates.sort(key=lambda z:(z["train_only_rank_score"],z["discovery"]["losses_avoided"]),reverse=True)
    # Injury-only exploratory contrasts rely on recent years and require
    # actual official reports for BOTH competing teams.
    injury_phases={}
    for label,seasons in (("injury_discovery_2021_22",{"2021-22"}),
                          ("injury_confirmation_2022_23",{"2022-23"}),
                          ("injury_validation_2023_24",{"2023-24"}),
                          ("injury_holdout_2024_26",H)):
        rr=[r for r in subset if r["season"] in seasons]
        known=[r for r in rr if r["injury_known"]]
        injury_phases[label]={"candidate_signals":len(rr),"both_reports_observed":len(known),
                              "coverage_fraction":len(known)/len(rr) if rr else None,
                              "observed_only":summaries(known),
                              "win_loss_contrasts":{f:contrast(known,f) for f in
                                  ("injury_minutes_adv","injury_points_adv","injury_out_count_adv")}}
    out["methods"][mid]={
        "frozen_parent_rule":parent_conds,
        "baseline":summaries(subset),
        "season_loss_forensics":winloss_by_season,
        "feature_contrasts_discovery":{f:contrast(disc,f) for f in FEATURES},
        "fixed_price_probability_slices":fixed_price_gates,
        "eligible_train_only_loss_filters":len(candidates),
        "shortlist_train_ranked_not_holdout_ranked":candidates[:30],
        "train_eligible_filters_improving_roi_in_all_later_phases":sum(1 for z in candidates if z["qualifies_for_further_investigation"]),
        "injury_context":injury_phases
    }
path=OUT/"h5_loss_forensics.json"
path.write_text(json.dumps(out,indent=2)+"\n")
print(json.dumps({m:{
    "baseline":v["baseline"],
    "candidate_filters":v["eligible_train_only_loss_filters"],
    "improved_all_later_phases":v["train_eligible_filters_improving_roi_in_all_later_phases"],
    "top":v["shortlist_train_ranked_not_holdout_ranked"][:2]}
  for m,v in out["methods"].items()},indent=2))
