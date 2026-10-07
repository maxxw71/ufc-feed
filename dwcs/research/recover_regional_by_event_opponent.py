#!/usr/bin/env python3
from __future__ import annotations
import glob,json,re
from pathlib import Path
import duckdb,pandas as pd
from rapidfuzz import fuzz

ROOT=Path(".")
OUT=ROOT/"dwcs/research/regional_identity_recovery_v2"
OUT.mkdir(parents=True,exist_ok=True)

def norm(s):
    s=str(s or "").lower().replace("’","'").replace("-"," ")
    s=re.sub(r"\b(jr|sr|ii|iii|iv)\b"," ",s)
    s=re.sub(r"[^a-z0-9]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()

def main():
    dbs=glob.glob("/tmp/mmadb/**/*.duckdb",recursive=True)
    if not dbs: raise SystemExit("No MMA archive duckdb")
    con=duckdb.connect(dbs[0],read_only=True)
    f=con.execute("SELECT * FROM fights_career_longitudinal").fetchdf()
    f["event_date"]=pd.to_datetime(f.event_date,errors="coerce").dt.normalize()
    cols=set(f.columns)
    f1=next(c for c in ["fighter_1","fighter1","red_fighter"] if c in cols)
    f2=next(c for c in ["fighter_2","fighter2","blue_fighter"] if c in cols)
    f["n1"]=f[f1].map(norm); f["n2"]=f[f2].map(norm)

    reg=pd.read_csv(ROOT/"dwcs/research/regional_history/dwcs_prefight_regional_features.csv",low_memory=False)
    reg["dwcs_date"]=pd.to_datetime(reg.dwcs_date,errors="coerce").dt.normalize()
    unmatched=reg[~reg.regional_history_matched.fillna(False)].copy()

    recovered=[]
    for _,x in unmatched.iterrows():
        date=x.dwcs_date
        target=norm(x.fighter)
        opp=norm(x.opponent_dwcs)
        day=f[f.event_date.eq(date)]
        candidates=[]
        for _,r in day.iterrows():
            a,b=r.n1,r.n2
            # identify rows where one side matches known opponent strongly
            sa=fuzz.WRatio(opp,a); sb=fuzz.WRatio(opp,b)
            if sa>=94:
                candidates.append((b, r[f2], sa, fuzz.WRatio(target,b), r[f1], r[f2]))
            if sb>=94:
                candidates.append((a, r[f1], sb, fuzz.WRatio(target,a), r[f1], r[f2]))
        if not candidates: continue
        candidates.sort(key=lambda z:(z[2],z[3]),reverse=True)
        best=candidates[0]
        alias_norm,alias_name,opp_score,target_score,ra,rb=best
        # Date+opponent is the primary identity key. Require target-name resemblance too
        # unless there is only one fight against that opponent on the date.
        same_opp=[c for c in candidates if c[2]>=97]
        accept=(opp_score>=97 and target_score>=70) or (len(same_opp)==1 and opp_score>=99 and target_score>=55)
        if accept:
            recovered.append({
              "season":x.season,"dwcs_event":x.dwcs_event,"dwcs_date":date.date().isoformat(),
              "fighter":x.fighter,"opponent_dwcs":x.opponent_dwcs,
              "archive_alias":alias_name,"archive_alias_norm":alias_norm,
              "opponent_match_score":opp_score,"fighter_name_score":target_score,
              "archive_fighter_1":ra,"archive_fighter_2":rb,
              "identity_basis":"exact_event_date_plus_known_opponent"
            })
    rdf=pd.DataFrame(recovered).drop_duplicates(["dwcs_date","fighter"]) if recovered else pd.DataFrame()
    rdf.to_csv(OUT/"recovered_aliases.csv",index=False)

    # Combine with earlier conservative aliases.
    alias={}
    old=ROOT/"dwcs/research/regional_history_recovery/recovered_histories.csv"
    if old.exists():
        od=pd.read_csv(old,low_memory=False)
        for _,x in od.iterrows():
            alias[(str(pd.to_datetime(x.dwcs_date).date()),str(x.fighter))]=str(x.matched_norm)
    if len(rdf):
        for _,x in rdf.iterrows():
            alias[(str(x.dwcs_date),str(x.fighter))]=str(x.archive_alias_norm)

    # Estimate new coverage by checking whether alias has any fights before the DWCS date.
    f["all_aliases_a"]=f.n1; f["all_aliases_b"]=f.n2
    by={}
    for idx,r in f.iterrows():
        by.setdefault(r.n1,[]).append(idx); by.setdefault(r.n2,[]).append(idx)
    recovered_history=0
    detail=[]
    for _,x in unmatched.iterrows():
        key=(str(x.dwcs_date.date()),str(x.fighter))
        an=alias.get(key)
        if not an: continue
        inds=by.get(norm(an),[])
        g=f.loc[inds] if inds else f.iloc[0:0]
        g=g[g.event_date<x.dwcs_date]
        if len(g):
            recovered_history+=1
            detail.append({"dwcs_date":key[0],"fighter":x.fighter,"alias":an,"prior_fights":len(g)})

    before=int(reg.regional_history_matched.fillna(False).sum())
    # unique recovered rows beyond base, not double counting
    keys=set()
    for z in detail: keys.add((z["dwcs_date"],z["fighter"]))
    after=before+len(keys)
    status={
      "base_rows":len(reg),
      "base_matched":before,
      "base_coverage":before/len(reg),
      "event_opponent_aliases_recovered":len(rdf),
      "unmatched_rows_with_prior_history_recovered":len(keys),
      "estimated_matched_after":after,
      "estimated_coverage_after":after/len(reg),
      "identity_rule":"Exact DWCS date + known opponent match, with conservative fighter-name resemblance."
    }
    pd.DataFrame(detail).to_csv(OUT/"recovered_prior_history.csv",index=False)
    (OUT/"status.json").write_text(json.dumps(status,indent=2)+"\n")
    print(json.dumps(status,indent=2))

if __name__=="__main__":main()
