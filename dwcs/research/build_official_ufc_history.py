#!/usr/bin/env python3
from __future__ import annotations
import io,re,json,unicodedata,urllib.request
from pathlib import Path
import numpy as np,pandas as pd,requests

ROOT=Path(".")
OUT=ROOT/"dwcs/research/official_ufc_history"
OUT.mkdir(parents=True,exist_ok=True)
COMP_URL="https://raw.githubusercontent.com/DanMcInerney/mma-ai/main/data/raw/ufcstats/competitions.csv"
IND_URL="https://raw.githubusercontent.com/DanMcInerney/mma-ai/main/data/raw/ufcstats/individuals.csv"
JINA="https://r.jina.ai/"
UA="Appwiza-DWCS-Official-UFC/1.0"

def norm(v):
    x=unicodedata.normalize("NFKD",str(v or ""))
    x="".join(ch for ch in x if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z0-9]+"," ",x.lower()).split())

def fetch_csv(url):
    req=urllib.request.Request(url,headers={"User-Agent":UA})
    with urllib.request.urlopen(req,timeout=120) as r:
        return pd.read_csv(io.BytesIO(r.read()),low_memory=False)

def parse_of(v):
    m=re.search(r"(\d+)\s+of\s+(\d+)",str(v),re.I)
    return (float(m.group(1)),float(m.group(2))) if m else (0.,0.)

def parse_ctrl(v):
    m=re.match(r"\s*(\d+):(\d+)\s*$",str(v))
    return (60*int(m.group(1))+int(m.group(2)))/60 if m else 0.

def parse_num(v):
    m=re.search(r"-?\d+(?:\.\d+)?",str(v))
    return float(m.group()) if m else 0.

def fight_minutes(r):
    try:rnd=max(1,int(float(r.get("round",1) or 1)))
    except:rnd=1
    m=re.match(r"(\d+):(\d+)",str(r.get("time","0:00")))
    sec=int(m.group(1))*60+int(m.group(2)) if m else 0
    return max(1,(rnd-1)*300+sec)/60

def fighter_profile(comp,name,cutoff):
    nk=norm(name); fights=[]
    for _,r in comp[comp.event_date<cutoff].iterrows():
        n1,n2=norm(r.get("player1","")),norm(r.get("player2",""))
        if nk==n1:fights.append((r,1,2))
        elif nk==n2:fights.append((r,2,1))
    q=dict(fights=0,mins=0.,sl=0.,sa=0.,abs=0.,absa=0.,kd=0.,kdabs=0.,
           tdl=0.,tda=0.,tdallowed=0.,tdfaced=0.,ctrl=0.,oppctrl=0.,sub=0.)
    for r,side,opp in fights:
        mins=fight_minutes(r); q["fights"]+=1; q["mins"]+=mins
        for rd in range(1,6):
            a,c=parse_of(r.get(f"p{side}_rd{rd}_Sig_str")); q["sl"]+=a; q["sa"]+=c
            a,c=parse_of(r.get(f"p{opp}_rd{rd}_Sig_str")); q["abs"]+=a; q["absa"]+=c
            q["kd"]+=parse_num(r.get(f"p{side}_rd{rd}_KD")); q["kdabs"]+=parse_num(r.get(f"p{opp}_rd{rd}_KD"))
            a,c=parse_of(r.get(f"p{side}_rd{rd}_Td")); q["tdl"]+=a; q["tda"]+=c
            a,c=parse_of(r.get(f"p{opp}_rd{rd}_Td")); q["tdallowed"]+=a; q["tdfaced"]+=c
            q["ctrl"]+=parse_ctrl(r.get(f"p{side}_rd{rd}_Ctrl")); q["oppctrl"]+=parse_ctrl(r.get(f"p{opp}_rd{rd}_Ctrl"))
            q["sub"]+=parse_num(r.get(f"p{side}_rd{rd}_Sub_att"))
    if not q["fights"] or not q["mins"]: return None
    m=q["mins"]; sc15=15/m
    return {
      "prior_ufcstats_fights":q["fights"],"prior_minutes":m,
      "sig_l_pm":q["sl"]/m,"sig_abs_pm":q["abs"]/m,"sig_diff_pm":(q["sl"]-q["abs"])/m,
      "sig_acc":q["sl"]/q["sa"] if q["sa"] else np.nan,
      "sig_def":1-q["abs"]/q["absa"] if q["absa"] else np.nan,
      "kd15":q["kd"]*sc15,"kd_abs15":q["kdabs"]*sc15,
      "td_l15":q["tdl"]*sc15,"td_a15":q["tda"]*sc15,
      "td_acc":q["tdl"]/q["tda"] if q["tda"] else np.nan,
      "td_def":1-q["tdallowed"]/q["tdfaced"] if q["tdfaced"] else np.nan,
      "ctrl15":q["ctrl"]*sc15,"ctrl_diff15":(q["ctrl"]-q["oppctrl"])*sc15,
      "sub15":q["sub"]*sc15
    }

def main():
    fights=pd.read_csv(ROOT/"dwcs/research/results_boutmetrics/historical_fights.csv",low_memory=False)
    fights["event_date"]=pd.to_datetime(fights.event_date,errors="coerce").dt.normalize()
    comp=fetch_csv(COMP_URL)
    comp["event_date"]=pd.to_datetime(comp.event_date,errors="coerce").dt.normalize()
    ind=fetch_csv(IND_URL)

    rows=[]
    for _,x in fights.iterrows():
        for fighter,opp in [(x.fighter_a,x.fighter_b),(x.fighter_b,x.fighter_a)]:
            p=fighter_profile(comp,fighter,x.event_date)
            z={"season":x.season,"event_name":x.event_name,"event_date":x.event_date,
               "fighter":fighter,"opponent":opp,"source":"UFCStats historical rows",
               "strict_before_fight_date":True}
            if p:z.update(p)
            rows.append(z)
    hist=pd.DataFrame(rows)
    hist.to_csv(OUT/"prefight_official_ufcstats_snapshots.csv",index=False)

    # UFC.com athlete pages: capture current official profile metadata separately.
    # These are dated current snapshots and are NEVER backfilled into old fight rows.
    names=sorted(set(fights.fighter_a.dropna().astype(str))|set(fights.fighter_b.dropna().astype(str)))
    urows=[]
    for name in names:
        slug=norm(name).replace(" ","-")
        url=f"https://www.ufc.com/athlete/{slug}"
        try:
            r=requests.get(JINA+url,headers={"User-Agent":UA,"Accept":"text/plain"},timeout=35)
            txt=r.text if r.status_code==200 else ""
            matched=(r.status_code==200 and norm(name) in norm(txt[:5000]))
            urows.append({"fighter":name,"ufc_url":url,"http_status":r.status_code,"matched":matched,
                          "snapshot_scope":"current_only_not_historical","raw_excerpt":txt[:8000] if matched else ""})
        except Exception as e:
            urows.append({"fighter":name,"ufc_url":url,"matched":False,"error":repr(e),
                          "snapshot_scope":"current_only_not_historical"})
    udf=pd.DataFrame(urows)
    udf.to_csv(OUT/"ufc_com_current_profiles.csv",index=False)

    cov={
      "dwcs_historical_fights":len(fights),
      "fighter_fight_rows":len(hist),
      "rows_with_prior_ufcstats_history":int(hist.prior_ufcstats_fights.notna().sum()) if "prior_ufcstats_fights" in hist else 0,
      "rows_with_sig_diff":int(hist.sig_diff_pm.notna().sum()) if "sig_diff_pm" in hist else 0,
      "rows_with_td_def":int(hist.td_def.notna().sum()) if "td_def" in hist else 0,
      "rows_with_ctrl":int(hist.ctrl15.notna().sum()) if "ctrl15" in hist else 0,
      "ufc_com_profiles_attempted":len(udf),
      "ufc_com_profiles_matched":int(udf.matched.fillna(False).sum()),
      "historical_rule":"Only UFCStats bouts dated strictly before each DWCS fight are used in historical method features. UFC.com current aggregates are quarantined from historical backtests."
    }
    (OUT/"coverage.json").write_text(json.dumps(cov,indent=2,default=str)+"\n")
    print(json.dumps(cov,indent=2))

if __name__=="__main__":main()
