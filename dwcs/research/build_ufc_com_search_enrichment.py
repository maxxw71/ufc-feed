#!/usr/bin/env python3
from __future__ import annotations
import json,re,unicodedata,urllib.parse,time
from pathlib import Path
import pandas as pd,requests

ROOT=Path(".")
OUT=ROOT/"dwcs/research/ufc_com_search_enrichment"
OUT.mkdir(parents=True,exist_ok=True)
JINA="https://r.jina.ai/"
UA="ufc-feed/1.1"

def norm(v):
    x=unicodedata.normalize("NFKD",str(v or ""))
    x="".join(ch for ch in x if not unicodedata.combining(ch))
    x=x.lower().replace("’","'").replace("-"," ")
    x=re.sub(r"\b(jr|sr|ii|iii|iv)\b"," ",x)
    return " ".join(re.sub(r"[^a-z0-9]+"," ",x).split())

def grab(txt,pat):
    m=re.search(pat,txt,re.I|re.S)
    return m.group(1).strip() if m else ""

def search_one(name):
    q=urllib.parse.quote(name)
    surl=f"https://www.ufc.com/athletes/all?search={q}"
    try:
        r=requests.get(JINA+surl,headers={"User-Agent":UA,"Accept":"text/plain"},timeout=30)
        txt=r.text if r.status_code==200 else ""
        links=re.findall(r"\[Athlete Profile\]\((https://www\.ufc\.com/athlete/[^)]+)\)",txt,re.I)
        countm=re.search(r"# Athletes - All\s+(\d+) Athletes",txt,re.I|re.S)
        count=int(countm.group(1)) if countm else None
        chosen=""
        if links:
            # search result is filtered; prefer exact name occurrence nearest a profile link
            for u in links:
                slug=u.rstrip("/").split("/")[-1]
                if norm(slug.replace("-"," "))==norm(name):
                    chosen=u;break
            if not chosen and count==1: chosen=links[0]
            if not chosen:
                # accept only conservative fuzzy-ish normalized containment
                for u in links:
                    slugnorm=norm(u.rstrip("/").split("/")[-1].replace("-"," "))
                    if norm(name) in slugnorm or slugnorm in norm(name):
                        chosen=u;break
        return {"fighter":name,"search_url":surl,"search_status":r.status_code,"result_count":count,
                "profile_url":chosen,"profile_candidates":";".join(links[:10]),"resolved":bool(chosen)}
    except Exception as e:
        return {"fighter":name,"search_url":surl,"resolved":False,"error":repr(e)}

def fetch_profile(row):
    if not row.get("profile_url"): return row
    try:
        r=requests.get(JINA+row["profile_url"],headers={"User-Agent":UA,"Accept":"text/plain"},timeout=30)
        txt=r.text if r.status_code==200 else ""
        matched=r.status_code==200 and norm(row["fighter"]) in norm(txt)
        z={**row,"profile_status":r.status_code,"verified":matched,
           "snapshot_scope":"current_official_profile_not_historical"}
        if matched:
            z.update({
              "official_heading":grab(txt,r"^#\s+([^\n]+)$"),
              "record_current":grab(txt,r"(\d+-\d+(?:-\d+)?)\s*\(W-L-D\)"),
              "age_current":grab(txt,r"\bAge\s*\n?\s*(\d{1,2})\b"),
              "height_current":grab(txt,r"\bHeight\s*\n?\s*([0-9.]+)"),
              "reach_current":grab(txt,r"\bReach\s*\n?\s*([0-9.]+)"),
              "leg_reach_current":grab(txt,r"\bLeg reach\s*\n?\s*([0-9.]+)"),
              "octagon_debut":grab(txt,r"Octagon Debut\s*\n?\s*([^\n]+)"),
              "pro_since":grab(txt,r"Pro since\s+(\d{4})"),
              "sig_str_landed_total":grab(txt,r"Sig\. Strikes Landed\s+(\d+)"),
              "sig_str_attempted_total":grab(txt,r"Sig\. Strikes Attempted\s+(\d+)"),
              "sig_str_landed_per_min":grab(txt,r"([0-9.]+)\s*Sig\. Str\. Landed\s*Per Min"),
              "sig_str_absorbed_per_min":grab(txt,r"([0-9.]+)\s*Sig\. Str\. Absorbed\s*Per Min"),
              "sig_str_defense_pct":grab(txt,r"(\d{1,3})\s*%?\s*Sig\. Str\. Defense"),
              "takedowns_landed_total":grab(txt,r"Takedowns Landed\s+(\d+)"),
              "takedowns_attempted_total":grab(txt,r"Takedowns Attempted\s+(\d+)"),
              "takedown_avg_15":grab(txt,r"([0-9.]+)\s*Takedown avg\s*Per 15 Min"),
              "takedown_defense_pct":grab(txt,r"(\d{1,3})\s*%?\s*Takedown Defense"),
              "submission_avg_15":grab(txt,r"([0-9.]+)\s*Submission avg\s*Per 15 Min"),
              "knockdown_avg":grab(txt,r"([0-9.]+)\s*Knockdown Avg"),
              "raw_excerpt":txt[:14000]
            })
        return z
    except Exception as e:
        return {**row,"verified":False,"profile_error":repr(e)}

def main():
    fights=pd.read_csv(ROOT/"dwcs/research/results_boutmetrics/historical_fights.csv",low_memory=False)
    names=sorted(set(fights.fighter_a.dropna().astype(str))|set(fights.fighter_b.dropna().astype(str)))
    prior_path=OUT/"search_results.csv"
    prior=pd.read_csv(prior_path,low_memory=False) if prior_path.exists() else pd.DataFrame()
    prior_ok={}
    if len(prior):
        for _,z in prior.iterrows():
            if bool(z.get("resolved",False)) or str(z.get("search_status",""))=="200":
                prior_ok[str(z.get("fighter"))]=z.to_dict()
    searched=[]
    sess=requests.Session()
    sess.headers.update({"User-Agent":UA,"Accept":"text/plain"})
    for i,name in enumerate(names,1):
        if name in prior_ok:
            searched.append(prior_ok[name]); continue
        q=urllib.parse.quote(name)
        surl=f"https://www.ufc.com/athletes/all?search={q}"
        rec=None
        for attempt,delay in enumerate([0,1,3,7,15,30]):
            if delay: time.sleep(delay)
            try:
                r=sess.get(JINA+surl,timeout=35)
                if r.status_code==429: continue
                txt=r.text if r.status_code==200 else ""
                links=re.findall(r"\[Athlete Profile\]\((https://www\.ufc\.com/athlete/[^)]+)\)",txt,re.I)
                countm=re.search(r"# Athletes - All\s+(\d+) Athletes",txt,re.I|re.S)
                count=int(countm.group(1)) if countm else None
                chosen=""
                for u in links:
                    if norm(u.rstrip("/").split("/")[-1].replace("-"," "))==norm(name):
                        chosen=u; break
                if not chosen and count==1 and links: chosen=links[0]
                if not chosen:
                    for u in links:
                        sn=norm(u.rstrip("/").split("/")[-1].replace("-"," "))
                        if norm(name) in sn or sn in norm(name):
                            chosen=u; break
                rec={"fighter":name,"search_url":surl,"search_status":r.status_code,"result_count":count,
                     "profile_url":chosen,"profile_candidates":";".join(links[:20]),"resolved":bool(chosen)}
                break
            except Exception as e:
                rec={"fighter":name,"search_url":surl,"resolved":False,"error":repr(e)}
        if rec is None:
            rec={"fighter":name,"search_url":surl,"search_status":429,"resolved":False,"error":"rate_limited_after_retries"}
        searched.append(rec)
        if i%25==0:
            pd.DataFrame(searched).to_csv(prior_path,index=False)
            print("searched",i,"/",len(names),"resolved",sum(bool(x.get("resolved")) for x in searched),flush=True)
        time.sleep(.35)
    sdf=pd.DataFrame(searched)
    sdf.to_csv(OUT/"search_results.csv",index=False)

    resolved=[x for x in searched if x.get("resolved")]
    prof=[]
    # Keep profile-page fetching deliberately slow and separate from search bursts.
    for i,x in enumerate(resolved,1):
        rec=None
        for delay in [0,1,3,7,15]:
            if delay: time.sleep(delay)
            rec=fetch_profile(x)
            if rec.get("profile_status")!=429: break
        prof.append(rec)
        if i%20==0:
            pd.DataFrame(prof).to_csv(OUT/"official_profiles.csv",index=False)
            print("profiles",i,"/",len(resolved),"verified",sum(bool(z.get("verified")) for z in prof),flush=True)
        time.sleep(.5)
    pdf=pd.DataFrame(prof)
    pdf.to_csv(OUT/"official_profiles.csv",index=False)

    status={
      "dwcs_unique_fighters":len(names),
      "ufc_search_resolved":int(sdf.resolved.fillna(False).sum()) if len(sdf) else 0,
      "ufc_profile_verified":int(pdf.verified.fillna(False).sum()) if len(pdf) and "verified" in pdf else 0,
      "with_sig_str_landed_per_min":int(pdf.sig_str_landed_per_min.astype(str).str.len().gt(0).sum()) if len(pdf) and "sig_str_landed_per_min" in pdf else 0,
      "with_sig_str_defense":int(pdf.sig_str_defense_pct.astype(str).str.len().gt(0).sum()) if len(pdf) and "sig_str_defense_pct" in pdf else 0,
      "with_td_defense":int(pdf.takedown_defense_pct.astype(str).str.len().gt(0).sum()) if len(pdf) and "takedown_defense_pct" in pdf else 0,
      "with_td_avg":int(pdf.takedown_avg_15.astype(str).str.len().gt(0).sum()) if len(pdf) and "takedown_avg_15" in pdf else 0,
      "current_profile_metrics_quarantined_from_historical_backtests":True
    }
    (OUT/"status.json").write_text(json.dumps(status,indent=2)+"\n")
    print(json.dumps(status,indent=2))

if __name__=="__main__":main()
