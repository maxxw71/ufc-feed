#!/usr/bin/env python3
from __future__ import annotations
import json,re,unicodedata
from pathlib import Path
import numpy as np,pandas as pd

ROOT=Path(".")
OUT=ROOT/"dwcs/research/official_ufc_merged"
OUT.mkdir(parents=True,exist_ok=True)

def norm(v):
    x=unicodedata.normalize("NFKD",str(v or ""))
    x="".join(ch for ch in x if not unicodedata.combining(ch))
    x=x.lower().replace("’","'").replace("-"," ")
    x=re.sub(r"\b(jr|sr|ii|iii|iv)\b"," ",x)
    return " ".join(re.sub(r"[^a-z0-9]+"," ",x).split())

def num(v):
    try:
        x=float(v)
        return x if np.isfinite(x) else np.nan
    except:return np.nan

def main():
    prof_path=ROOT/"dwcs/research/ufc_com_search_resolved/official_profiles.csv"
    if not prof_path.exists():
        raise SystemExit(f"missing {prof_path}")
    prof=pd.read_csv(prof_path,low_memory=False)
    if "verified" in prof:
        prof=prof[prof.verified.astype(str).str.lower().eq("true")].copy()
    prof["_n"]=prof.fighter.map(norm)
    prof=prof.drop_duplicates("_n",keep="first")

    phys=pd.read_csv(ROOT/"dwcs/research/enrichment_probe/dwcs_fighter_physicals.csv",low_memory=False)
    phys["_n"]=phys.fighter.map(norm)
    pm=prof.set_index("_n")
    rows=[]
    for _,x in phys.iterrows():
        n=x["_n"]
        p=pm.loc[n] if n in pm.index else None
        bh=num(x.get("height_in")); br=num(x.get("reach_in"))
        uh=num(p.get("height_current")) if p is not None else np.nan
        ur=num(p.get("reach_current")) if p is not None else np.nan
        # plausible official values; UFC pages report inches
        if not (55<=uh<=90): uh=np.nan
        if not (55<=ur<=90): ur=np.nan
        hc=uh if pd.notna(uh) else bh
        rc=ur if pd.notna(ur) else br
        rows.append({
          "fighter":x.fighter,
          "ufc_profile_verified":bool(p is not None),
          "ufc_profile_url":p.get("profile_url","") if p is not None else "",
          "height_boutmetrics":bh,"height_ufc_official":uh,"height_in_canonical":hc,
          "height_conflict_gt1in":bool(pd.notna(bh) and pd.notna(uh) and abs(bh-uh)>1),
          "reach_boutmetrics":br,"reach_ufc_official":ur,"reach_in_canonical":rc,
          "reach_conflict_gt1in":bool(pd.notna(br) and pd.notna(ur) and abs(br-ur)>1),
          "stance_boutmetrics":x.get("stance",""),
          "pro_since_year":num(p.get("pro_since")) if p is not None else np.nan,
          "octagon_debut":p.get("octagon_debut","") if p is not None else "",
          "place_of_birth_current":p.get("place_of_birth","") if p is not None else "",
          "fighting_style_current":p.get("fighting_style","") if p is not None else "",
          "trains_at_current":p.get("trains_at","") if p is not None else "",
          "source_priority_height":"UFC.com" if pd.notna(uh) else ("BoutMetrics" if pd.notna(bh) else ""),
          "source_priority_reach":"UFC.com" if pd.notna(ur) else ("BoutMetrics" if pd.notna(br) else "")
        })
    canon=pd.DataFrame(rows)
    canon.to_csv(OUT/"canonical_stable_fighter_features.csv",index=False)

    hist=pd.read_csv(ROOT/"dwcs/research/results_boutmetrics/historical_fights.csv",low_memory=False)
    hist["event_date"]=pd.to_datetime(hist.event_date,errors="coerce")
    cmap=canon.set_index(canon.fighter.map(norm))
    snaps=[]
    for _,x in hist.iterrows():
        for fighter,opp in [(x.fighter_a,x.fighter_b),(x.fighter_b,x.fighter_a)]:
            n=norm(fighter); c=cmap.loc[n] if n in cmap.index else None
            py=num(c.get("pro_since_year")) if c is not None else np.nan
            years_pro=(x.event_date.year-py) if pd.notna(py) and pd.notna(x.event_date) else np.nan
            od=pd.to_datetime(c.get("octagon_debut",""),errors="coerce") if c is not None else pd.NaT
            snaps.append({
              "season":x.season,"event_name":x.event_name,"event_date":x.event_date,
              "fighter":fighter,"opponent":opp,
              "height_in":c.get("height_in_canonical",np.nan) if c is not None else np.nan,
              "reach_in":c.get("reach_in_canonical",np.nan) if c is not None else np.nan,
              "pro_since_year":py,"years_pro_at_dwcs":years_pro,
              "octagon_debut":c.get("octagon_debut","") if c is not None else "",
              "had_ufc_octagon_debut_before_dwcs":bool(pd.notna(od) and pd.notna(x.event_date) and od.normalize()<x.event_date.normalize()),
              "official_ufc_profile_available":bool(c.get("ufc_profile_verified",False)) if c is not None else False,
              "historical_safe_fields":"height,reach,pro_since,octagon_debut"
            })
    sdf=pd.DataFrame(snaps)
    sdf.to_csv(OUT/"prefight_stable_official_features.csv",index=False)

    status={
      "unique_fighters":len(canon),
      "verified_ufc_profiles":int(canon.ufc_profile_verified.sum()),
      "canonical_height_known":int(canon.height_in_canonical.notna().sum()),
      "canonical_reach_known":int(canon.reach_in_canonical.notna().sum()),
      "height_official_known":int(canon.height_ufc_official.notna().sum()),
      "reach_official_known":int(canon.reach_ufc_official.notna().sum()),
      "height_conflicts_gt1in":int(canon.height_conflict_gt1in.sum()),
      "reach_conflicts_gt1in":int(canon.reach_conflict_gt1in.sum()),
      "pro_since_known":int(canon.pro_since_year.notna().sum()),
      "historical_snapshot_rows":len(sdf),
      "rows_with_years_pro":int(sdf.years_pro_at_dwcs.notna().sum()),
      "policy":"Only stable UFC.com fields are permitted to backfill historical rows. Current UFC.com performance aggregates remain excluded from historical backtests."
    }
    (OUT/"status.json").write_text(json.dumps(status,indent=2)+"\n")
    print(json.dumps(status,indent=2))

if __name__=="__main__":main()
