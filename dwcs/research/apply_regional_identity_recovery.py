#!/usr/bin/env python3
from __future__ import annotations
import glob,json,re
from pathlib import Path
import duckdb,pandas as pd,numpy as np

ROOT=Path(".")
OUT=ROOT/"dwcs/research/regional_history_v2"
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
    allf=con.execute("SELECT * FROM fights_career_longitudinal").fetchdf()
    allf["event_date"]=pd.to_datetime(allf.event_date,errors="coerce").dt.normalize()
    cols=set(allf.columns)
    f1=next(c for c in ["fighter_1","fighter1","red_fighter"] if c in cols)
    f2=next(c for c in ["fighter_2","fighter2","blue_fighter"] if c in cols)
    winner=next(c for c in ["winner","winner_name"] if c in cols)
    method=next((c for c in ["method_normalized","method"] if c in cols),None)
    org=next((c for c in ["organization","promotion"] if c in cols),None)
    rnd=next((c for c in ["round_num","round"] if c in cols),None)

    allf["f1n"]=allf[f1].map(norm); allf["f2n"]=allf[f2].map(norm); allf["wn"]=allf[winner].map(norm)
    by={}
    for idx,r in allf.iterrows():
        by.setdefault(r.f1n,[]).append(idx); by.setdefault(r.f2n,[]).append(idx)

    base=pd.read_csv(ROOT/"dwcs/research/regional_history/dwcs_prefight_regional_features.csv",low_memory=False)
    base["dwcs_date"]=pd.to_datetime(base.dwcs_date,errors="coerce").dt.normalize()

    aliases={}
    p1=ROOT/"dwcs/research/regional_history_recovery/recovered_histories.csv"
    if p1.exists():
        d=pd.read_csv(p1,low_memory=False)
        for _,x in d.iterrows():
            aliases[(str(pd.to_datetime(x.dwcs_date).date()),str(x.fighter))]=norm(x.matched_norm)
    p2=ROOT/"dwcs/research/regional_identity_recovery_v2/recovered_aliases.csv"
    if p2.exists():
        d=pd.read_csv(p2,low_memory=False)
        for _,x in d.iterrows():
            aliases[(str(pd.to_datetime(x.dwcs_date).date()),str(x.fighter))]=norm(x.archive_alias_norm)

    record_cache={}
    def prior_record(name,date):
        key=(norm(name),pd.Timestamp(date))
        if key in record_cache:return record_cache[key]
        inds=by.get(norm(name),[])
        g=allf.loc[inds] if inds else allf.iloc[0:0]
        g=g[g.event_date<date]
        wins=sum(g.wn.eq(norm(name)))
        losses=sum((g.wn.notna()) & (~g.wn.eq(norm(name))) & (g.wn!=""))
        record_cache[key]=(int(wins),int(losses))
        return record_cache[key]

    outrows=[]
    upgraded=0
    for _,x in base.iterrows():
        z=x.to_dict()
        if bool(x.regional_history_matched):
            z["history_source"]="base_exact"
            outrows.append(z); continue
        key=(str(x.dwcs_date.date()),str(x.fighter))
        an=aliases.get(key)
        if not an:
            z["history_source"]="unmatched"
            outrows.append(z); continue
        inds=by.get(an,[])
        g=allf.loc[inds].copy() if inds else allf.iloc[0:0].copy()
        g=g[g.event_date<x.dwcs_date].sort_values("event_date")
        if not len(g):
            z["history_source"]="alias_no_prior_history"
            outrows.append(z); continue

        wins=losses=draws=kos=subs=decs=r1fin=0
        oppq=[];orgs=set();lastdate=pd.NaT
        for _,r in g.iterrows():
            won=r.wn==an
            if won:wins+=1
            elif r.wn in ("","nan","none"):draws+=1
            else:losses+=1
            m=str(r[method]).lower() if method else ""
            if won and ("ko" in m or "tko" in m):kos+=1
            if won and "sub" in m:subs+=1
            if won and "decision" in m:decs+=1
            rr=pd.to_numeric(r[rnd],errors="coerce") if rnd else np.nan
            if won and pd.notna(rr) and rr<=1 and (("ko" in m) or ("tko" in m) or ("sub" in m)):r1fin+=1
            other=r[f2] if r.f1n==an else r[f1]
            ow,ol=prior_record(other,r.event_date)
            if ow+ol>=1:oppq.append(ow/(ow+ol))
            if org:orgs.add(str(r[org]))
            lastdate=r.event_date
        total=wins+losses+draws
        z.update({
          "prior_fights":total,"prior_wins":wins,"prior_losses":losses,"prior_draws":draws,
          "prior_win_pct":wins/(wins+losses) if wins+losses else np.nan,
          "prior_ko_wins":kos,"prior_sub_wins":subs,"prior_decision_wins":decs,
          "prior_finish_wins":kos+subs,"prior_finish_rate":(kos+subs)/wins if wins else np.nan,
          "prior_first_round_finishes":r1fin,"prior_first_round_finish_rate":r1fin/wins if wins else np.nan,
          "prior_distinct_promotions":len(orgs),
          "prior_avg_opponent_win_pct":float(np.mean(oppq)) if oppq else np.nan,
          "prior_days_since_last_fight":int((x.dwcs_date-lastdate).days) if pd.notna(lastdate) else np.nan,
          "regional_history_matched":True,"history_source":"recovered_event_opponent_alias","archive_alias":an
        })
        upgraded+=1
        outrows.append(z)

    out=pd.DataFrame(outrows)
    out.to_csv(OUT/"dwcs_prefight_regional_features_v2.csv",index=False)
    status={
      "fighter_fight_rows":len(out),
      "rows_with_prior_history":int(out.regional_history_matched.fillna(False).sum()),
      "coverage":float(out.regional_history_matched.fillna(False).mean()),
      "new_rows_upgraded_from_alias_recovery":upgraded,
      "rows_with_3plus_prior_fights":int((pd.to_numeric(out.prior_fights,errors="coerce")>=3).sum()),
      "rows_with_5plus_prior_fights":int((pd.to_numeric(out.prior_fights,errors="coerce")>=5).sum()),
      "rows_with_opponent_quality":int(pd.to_numeric(out.prior_avg_opponent_win_pct,errors="coerce").notna().sum()),
      "strict_before_dwcs_date":True,
      "database_max_date":str(allf.event_date.max().date())
    }
    (OUT/"status.json").write_text(json.dumps(status,indent=2)+"\n")
    print(json.dumps(status,indent=2))

if __name__=="__main__":main()
