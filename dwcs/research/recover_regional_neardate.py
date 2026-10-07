#!/usr/bin/env python3
from __future__ import annotations

import glob
import json
import re
from pathlib import Path

import duckdb
import pandas as pd
from rapidfuzz import fuzz

ROOT=Path(".")
OUT=ROOT/"dwcs/research/regional_gap_diagnostic"
OUT.mkdir(parents=True,exist_ok=True)

def norm(s):
    s=str(s or "").lower().replace("’","'").replace("-"," ")
    s=re.sub(r"\b(jr|sr|ii|iii|iv)\b"," ",s)
    s=re.sub(r"[^a-z0-9]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()

def main():
    dbs=glob.glob("/tmp/mmadb/**/*.duckdb",recursive=True)
    if not dbs:
        raise SystemExit("No MMA archive duckdb in /tmp/mmadb")
    con=duckdb.connect(dbs[0],read_only=True)
    f=con.execute("SELECT * FROM fights_career_longitudinal").fetchdf()
    f["event_date"]=pd.to_datetime(f.event_date,errors="coerce").dt.normalize()
    f=f[f.event_date.notna()].copy()
    cols=set(f.columns)
    f1=next(c for c in ["fighter_1","fighter1","red_fighter"] if c in cols)
    f2=next(c for c in ["fighter_2","fighter2","blue_fighter"] if c in cols)
    f["n1"]=f[f1].map(norm); f["n2"]=f[f2].map(norm)

    regp=ROOT/"dwcs/research/regional_history_v2/dwcs_prefight_regional_features_v2.csv"
    reg=pd.read_csv(regp,low_memory=False)
    reg["dwcs_date"]=pd.to_datetime(reg.dwcs_date,errors="coerce").dt.normalize()
    unmatched=reg[~reg.regional_history_matched.fillna(False)].copy()

    by={}
    for idx,r in f.iterrows():
        by.setdefault(r.n1,[]).append(idx)
        by.setdefault(r.n2,[]).append(idx)

    diagnostics=[]
    recovered=[]

    for _,x in unmatched.iterrows():
        target=norm(x.fighter)
        opp=norm(x.opponent_dwcs)
        date=x.dwcs_date
        exact_inds=by.get(target,[])
        exact_prior=f.loc[exact_inds] if exact_inds else f.iloc[0:0]
        exact_prior=exact_prior[exact_prior.event_date<date]

        if len(exact_prior):
            diagnostics.append({
                "season":x.season,"dwcs_event":x.dwcs_event,"dwcs_date":date.date().isoformat(),
                "fighter":x.fighter,"opponent_dwcs":x.opponent_dwcs,
                "classification":"exact_identity_prior_exists_pipeline_gap",
                "candidate_alias":target,"prior_fights":len(exact_prior),
                "opponent_match_score":100.0,"fighter_name_score":100.0
            })
            recovered.append({
                "season":x.season,"dwcs_event":x.dwcs_event,"dwcs_date":date.date().isoformat(),
                "fighter":x.fighter,"opponent_dwcs":x.opponent_dwcs,
                "archive_alias":x.fighter,"archive_alias_norm":target,
                "opponent_match_score":100.0,"fighter_name_score":100.0,
                "date_distance_days":0,
                "identity_basis":"exact_identity_with_verified_prior_history"
            })
            continue

        # Search only the event neighborhood. This is specifically for archive date
        # normalization/timezone drift or name aliases, never a broad fuzzy identity search.
        nearby=f[(f.event_date>=date-pd.Timedelta(days=3)) & (f.event_date<=date+pd.Timedelta(days=3))]
        candidates=[]
        for _,r in nearby.iterrows():
            a,b=r.n1,r.n2
            sa,sb=fuzz.WRatio(opp,a),fuzz.WRatio(opp,b)
            if sa>=98:
                candidates.append({
                    "alias_norm":b,"alias_name":r[f2],"opp_score":sa,
                    "target_score":fuzz.WRatio(target,b),
                    "date_distance":abs((r.event_date-date).days),
                    "archive_date":r.event_date,
                })
            if sb>=98:
                candidates.append({
                    "alias_norm":a,"alias_name":r[f1],"opp_score":sb,
                    "target_score":fuzz.WRatio(target,a),
                    "date_distance":abs((r.event_date-date).days),
                    "archive_date":r.event_date,
                })

        # Only aliases that themselves have real history strictly before DWCS date count.
        viable=[]
        for c in candidates:
            inds=by.get(c["alias_norm"],[])
            g=f.loc[inds] if inds else f.iloc[0:0]
            g=g[g.event_date<date]
            c["prior_fights"]=len(g)
            if len(g):
                viable.append(c)

        viable.sort(key=lambda c:(c["target_score"],c["opp_score"],-c["date_distance"]),reverse=True)
        accepted=None
        if viable:
            top=viable[0]
            second=viable[1] if len(viable)>1 else None
            unique_margin=(top["target_score"]-(second["target_score"] if second else 0))
            # Conservative acceptance: strong opponent identity + meaningful target
            # resemblance, with either high target score or uniqueness.
            if top["opp_score"]>=98 and (
                top["target_score"]>=85 or
                (top["target_score"]>=72 and unique_margin>=12 and top["date_distance"]<=1)
            ):
                accepted=top

        if accepted:
            recovered.append({
                "season":x.season,"dwcs_event":x.dwcs_event,"dwcs_date":date.date().isoformat(),
                "fighter":x.fighter,"opponent_dwcs":x.opponent_dwcs,
                "archive_alias":accepted["alias_name"],
                "archive_alias_norm":accepted["alias_norm"],
                "opponent_match_score":accepted["opp_score"],
                "fighter_name_score":accepted["target_score"],
                "date_distance_days":accepted["date_distance"],
                "identity_basis":"near_date_plus_exact_opponent_conservative"
            })
            classification="near_date_alias_recovered"
            cand=accepted
        elif exact_inds:
            classification="exact_identity_but_zero_prior_history"
            cand=None
        elif viable:
            classification="near_date_candidate_rejected_low_confidence"
            cand=viable[0]
        else:
            classification="archive_identity_unresolved"
            cand=None

        diagnostics.append({
            "season":x.season,"dwcs_event":x.dwcs_event,"dwcs_date":date.date().isoformat(),
            "fighter":x.fighter,"opponent_dwcs":x.opponent_dwcs,
            "classification":classification,
            "candidate_alias":cand["alias_name"] if cand else "",
            "prior_fights":cand["prior_fights"] if cand else 0,
            "opponent_match_score":cand["opp_score"] if cand else None,
            "fighter_name_score":cand["target_score"] if cand else None,
            "date_distance_days":cand["date_distance"] if cand else None,
        })

    dd=pd.DataFrame(diagnostics)
    rr=pd.DataFrame(recovered).drop_duplicates(["dwcs_date","fighter"]) if recovered else pd.DataFrame()
    dd.to_csv(OUT/"gap_diagnostic.csv",index=False)
    rr.to_csv(OUT/"near_date_recovered_aliases.csv",index=False)

    counts=dd.classification.value_counts().to_dict() if len(dd) else {}
    status={
        "unmatched_rows_start":int(len(unmatched)),
        "recovered_alias_rows":int(len(rr)),
        "classification_counts":{str(k):int(v) for k,v in counts.items()},
        "near_date_window_days":3,
        "strict_prior_history_before_dwcs_date":True,
        "recovery_rule":"Exact identity with prior history OR conservative +/-3-day event identity using exact opponent and strong fighter-name resemblance."
    }
    (OUT/"status.json").write_text(json.dumps(status,indent=2)+"\n")
    print(json.dumps(status,indent=2))

if __name__=="__main__":
    main()
