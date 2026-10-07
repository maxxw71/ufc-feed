#!/usr/bin/env python3
from __future__ import annotations
import json,re
from pathlib import Path
import numpy as np,pandas as pd

ROOT=Path(".")
OUT=ROOT/"dwcs/research/static_profile_merge"
OUT.mkdir(parents=True,exist_ok=True)

def norm(s):
    s=str(s or "").lower().replace("’","'").replace("-"," ")
    s=re.sub(r"\b(jr|sr|ii|iii|iv)\b"," ",s)
    s=re.sub(r"[^a-z0-9]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()

def valid_height(v):
    x=pd.to_numeric(v,errors="coerce")
    return float(x) if pd.notna(x) and 55<=x<=84 else np.nan

def valid_reach(v):
    x=pd.to_numeric(v,errors="coerce")
    return float(x) if pd.notna(x) and 55<=x<=90 else np.nan

def main():
    base=pd.read_csv(ROOT/"dwcs/research/enrichment_probe/dwcs_fighter_physicals.csv",low_memory=False)
    ufc=pd.read_csv(ROOT/"dwcs/research/ufc_com_verified/verified_profiles.csv",low_memory=False)
    ufc=ufc[ufc.verified.fillna(False)].copy()
    ufc["_n"]=ufc.fighter.map(norm)
    um={x._n:x for _,x in ufc.iterrows()}

    rows=[];conf=[]
    for _,x in base.iterrows():
        z=x.to_dict()
        n=norm(x.fighter); u=um.get(n)
        bh=pd.to_numeric(pd.Series([x.get("height_in")]),errors="coerce").iloc[0]
        br=pd.to_numeric(pd.Series([x.get("reach_in")]),errors="coerce").iloc[0]
        uh=valid_height(u.get("height_current")) if u is not None else np.nan
        ur=valid_reach(u.get("reach_current")) if u is not None else np.nan

        z["height_source"]="boutmetrics" if pd.notna(bh) else ""
        z["reach_source"]="boutmetrics" if pd.notna(br) else ""
        z["ufc_com_verified_profile"]=bool(u is not None)
        z["ufc_com_url"]=u.get("profile_url","") if u is not None else ""

        if pd.isna(bh) and pd.notna(uh):
            z["height_in"]=uh; z["height_source"]="ufc.com_verified_static_fill"
        elif pd.notna(bh) and pd.notna(uh) and abs(float(bh)-uh)>1:
            conf.append({"fighter":x.fighter,"field":"height_in","boutmetrics":float(bh),"ufc_com":uh,
                         "action":"retain_existing_flag_conflict"})
        if pd.isna(br) and pd.notna(ur):
            z["reach_in"]=ur; z["reach_source"]="ufc.com_verified_static_fill"
        elif pd.notna(br) and pd.notna(ur) and abs(float(br)-ur)>1:
            conf.append({"fighter":x.fighter,"field":"reach_in","boutmetrics":float(br),"ufc_com":ur,
                         "action":"retain_existing_flag_conflict"})
        rows.append(z)

    out=pd.DataFrame(rows)
    out.to_csv(OUT/"dwcs_fighter_physicals_merged.csv",index=False)
    pd.DataFrame(conf).to_csv(OUT/"static_conflicts.csv",index=False)
    status={
      "fighters":len(out),
      "height_known":int(pd.to_numeric(out.height_in,errors="coerce").notna().sum()),
      "reach_known":int(pd.to_numeric(out.reach_in,errors="coerce").notna().sum()),
      "stance_known":int(out.stance.astype(str).str.strip().replace("nan","").ne("").sum()),
      "height_filled_from_verified_ufc_com":int((out.height_source=="ufc.com_verified_static_fill").sum()),
      "reach_filled_from_verified_ufc_com":int((out.reach_source=="ufc.com_verified_static_fill").sum()),
      "static_conflicts_flagged":len(conf),
      "dynamic_ufc_com_metrics_merged":False
    }
    (OUT/"status.json").write_text(json.dumps(status,indent=2)+"\n")
    print(json.dumps(status,indent=2))

if __name__=="__main__":main()
