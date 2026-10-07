#!/usr/bin/env python3
from __future__ import annotations
import glob,json,re
from collections import defaultdict
from pathlib import Path
import duckdb,pandas as pd,numpy as np

ROOT=Path(".")
OUT=ROOT/"dwcs/research/point_in_time_sos"
OUT.mkdir(parents=True,exist_ok=True)

def norm(s):
    s=str(s or "").lower().replace("’","'").replace("-"," ")
    s=re.sub(r"\b(jr|sr|ii|iii|iv)\b"," ",s)
    s=re.sub(r"[^a-z0-9]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()

def main():
    dbs=glob.glob("/tmp/mmadb/**/*.duckdb",recursive=True)
    if not dbs: raise SystemExit("No MMA global duckdb in /tmp/mmadb")
    con=duckdb.connect(dbs[0],read_only=True)
    f=con.execute("SELECT * FROM fights_career_longitudinal").fetchdf()
    f["event_date"]=pd.to_datetime(f["event_date"],errors="coerce").dt.normalize()
    f=f[f.event_date.notna()].sort_values("event_date").reset_index(drop=True)
    cols=set(f.columns)
    f1=next(c for c in ["fighter_1","fighter1","red_fighter"] if c in cols)
    f2=next(c for c in ["fighter_2","fighter2","blue_fighter"] if c in cols)
    wc=next(c for c in ["winner","winner_name"] if c in cols)
    f["n1"]=f[f1].map(norm); f["n2"]=f[f2].map(norm); f["wn"]=f[wc].map(norm)

    elo=defaultdict(lambda:1500.0)
    nfight=defaultdict(int)
    annotated=[]
    K=24.0
    for _,x in f.iterrows():
        a,b=x.n1,x.n2
        if not a or not b or a==b: continue
        ea,eb=elo[a],elo[b]
        fa,fb=nfight[a],nfight[b]
        winner=x.wn
        if winner==a: oa,ob=1.0,0.0
        elif winner==b: oa,ob=0.0,1.0
        else: oa=ob=0.5
        expa=1/(1+10**((eb-ea)/400))
        expb=1-expa
        annotated.append({
          "event_date":x.event_date,"fighter_a":x[f1],"fighter_b":x[f2],
          "a_norm":a,"b_norm":b,"winner_norm":winner,
          "a_pre_elo":ea,"b_pre_elo":eb,
          "a_pre_fights":fa,"b_pre_fights":fb,
          "a_outcome":oa,"b_outcome":ob,
          "a_expected":expa,"b_expected":expb
        })
        elo[a]=ea+K*(oa-expa); elo[b]=eb+K*(ob-expb)
        nfight[a]+=1;nfight[b]+=1

    ann=pd.DataFrame(annotated)
    by=defaultdict(list)
    for i,x in ann.iterrows():
        by[x.a_norm].append((i,"a"))
        by[x.b_norm].append((i,"b"))

    hist=pd.read_csv(ROOT/"dwcs/research/results_boutmetrics/historical_fights.csv",low_memory=False)
    hist["event_date"]=pd.to_datetime(hist.event_date,errors="coerce").dt.normalize()
    targets=[]
    for _,x in hist.iterrows():
        targets += [(int(x.season),x.event_name,x.event_date,x.fighter_a,x.fighter_b),
                    (int(x.season),x.event_name,x.event_date,x.fighter_b,x.fighter_a)]
    s10p=ROOT/"dwcs/data/season10_2026_master.csv"
    if s10p.exists():
        s10=pd.read_csv(s10p,low_memory=False)
        s10["event_date"]=pd.to_datetime(s10.event_date,errors="coerce").dt.normalize()
        for _,x in s10[s10.status.eq("completed")].iterrows():
            targets += [(10,f"DWCS 10.{int(x.week)}",x.event_date,x.winner,x.loser),
                        (10,f"DWCS 10.{int(x.week)}",x.event_date,x.loser,x.winner)]

    aliases={}
    rp=ROOT/"dwcs/research/regional_history_recovery/recovered_histories.csv"
    if rp.exists():
        rec=pd.read_csv(rp,low_memory=False)
        for _,x in rec.iterrows():
            aliases[(str(x.fighter),str(pd.to_datetime(x.dwcs_date).date()))]=str(x.matched_norm)
    rp2=ROOT/"dwcs/research/regional_identity_recovery_v2/recovered_aliases.csv"
    if rp2.exists():
        rec2=pd.read_csv(rp2,low_memory=False)
        for _,x in rec2.iterrows():
            aliases[(str(x.fighter),str(pd.to_datetime(x.dwcs_date).date()))]=str(x.archive_alias_norm)

    rows=[]
    for season,event,date,name,opp in targets:
        nn=norm(name)
        inds=by.get(nn,[])
        if not inds:
            alt=aliases.get((str(name),str(pd.Timestamp(date).date())))
            if alt: inds=by.get(norm(alt),[])
        prior=[]
        for idx,side in inds:
            x=ann.loc[idx]
            if x.event_date>=date: continue
            if side=="a":
                prior.append((x.event_date,x.a_pre_elo,x.b_pre_elo,x.a_pre_fights,x.b_pre_fights,x.a_outcome,x.a_expected))
            else:
                prior.append((x.event_date,x.b_pre_elo,x.a_pre_elo,x.b_pre_fights,x.a_pre_fights,x.b_outcome,x.b_expected))
        prior=sorted(prior,key=lambda z:z[0])
        if prior:
            # current Elo immediately before DWCS = recursively replay these fighter deltas only.
            cur=1500.0
            for z in prior: cur += K*(z[5]-z[6])
            oppelos=[z[2] for z in prior]
            residuals=[z[5]-z[6] for z in prior]
            strong=[z for z in prior if z[2]>=1550]
            elite=[z for z in prior if z[2]>=1600]
            rows.append({
              "season":season,"dwcs_event":event,"dwcs_date":date,"fighter":name,"opponent_dwcs":opp,
              "prior_global_fights":len(prior),
              "prefight_global_elo":cur,
              "avg_opponent_pre_elo":float(np.mean(oppelos)),
              "median_opponent_pre_elo":float(np.median(oppelos)),
              "max_opponent_pre_elo":float(np.max(oppelos)),
              "strong_opponents_1550":len(strong),
              "elite_opponents_1600":len(elite),
              "wins_vs_1550":sum(z[5]==1.0 for z in strong),
              "wins_vs_1600":sum(z[5]==1.0 for z in elite),
              "quality_residual_sum":float(np.sum(residuals)),
              "quality_residual_avg":float(np.mean(residuals)),
              "last3_avg_opponent_elo":float(np.mean([z[2] for z in prior[-3:]])),
              "last5_avg_opponent_elo":float(np.mean([z[2] for z in prior[-5:]])),
              "days_since_last_global_fight":int((date-prior[-1][0]).days),
              "sos_matched":True
            })
        else:
            rows.append({
              "season":season,"dwcs_event":event,"dwcs_date":date,"fighter":name,"opponent_dwcs":opp,
              "prior_global_fights":0,"sos_matched":False
            })
    out=pd.DataFrame(rows)
    out.to_csv(OUT/"dwcs_prefight_global_elo_sos.csv",index=False)
    status={
      "fighter_fight_rows":len(out),
      "rows_with_global_history":int(out.sos_matched.fillna(False).sum()),
      "coverage":float(out.sos_matched.fillna(False).mean()) if len(out) else 0,
      "rows_with_3plus_global_fights":int((pd.to_numeric(out.prior_global_fights,errors="coerce")>=3).sum()),
      "rows_with_5plus_global_fights":int((pd.to_numeric(out.prior_global_fights,errors="coerce")>=5).sum()),
      "archive_database_max_date":str(f.event_date.max().date()),
      "elo_k":K,
      "strict_before_dwcs_date":True
    }
    (OUT/"status.json").write_text(json.dumps(status,indent=2)+"\n")
    print(json.dumps(status,indent=2))

if __name__=="__main__":main()
