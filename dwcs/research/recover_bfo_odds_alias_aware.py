#!/usr/bin/env python3
from __future__ import annotations
import json,re,time
from pathlib import Path
import pandas as pd,requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

ROOT=Path(".")
ODDS=ROOT/"dwcs/research/historical_odds/historical_odds.csv"
FIGHTS=ROOT/"dwcs/research/results_boutmetrics/historical_fights.csv"
PAIRMAP=ROOT/"dwcs/research/historical_odds/bfo_event_matchup_map.csv"
ALIASES=ROOT/"dwcs/research/regional_identity_recovery_v2/recovered_aliases.csv"
OUT=ROOT/"dwcs/research/historical_odds"
BASE="https://www.bestfightodds.com"

def norm(s):
    s=str(s or "").lower().replace("’","'").replace("-"," ")
    s=re.sub(r"\b(jr|sr|ii|iii|iv)\b"," ",s)
    s=re.sub(r"[^a-z0-9]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()

def first_initial(s):
    p=norm(s).split()
    return p[0][:1] if p else ""

def last_name(s):
    p=norm(s).split()
    return p[-1] if p else ""

def sim(a,b):
    a,b=norm(a),norm(b)
    if not a or not b:return 0.0
    sc=float(fuzz.WRatio(a,b))
    if last_name(a)==last_name(b) and first_initial(a)==first_initial(b):
        sc=max(sc,94.0)
    elif fuzz.ratio(last_name(a),last_name(b))>=90 and first_initial(a)==first_initial(b):
        sc=max(sc,88.0)
    return sc

def odds_tokens(txt):
    return [int(x) for x in re.findall(r"(?<!\d)([+-]\d{2,5})(?!\d)",txt)]

ses=requests.Session()
ses.headers.update({"User-Agent":"Mozilla/5.0 Appwiza-DWCS-BFO-Alias/1.0","Accept-Language":"en-US,en;q=0.9"})

def get(url,tries=6):
    last=None
    for i in range(tries):
        try:
            r=ses.get(url,timeout=30); last=r
            if r.status_code==200:return r
            if r.status_code in (429,503):
                time.sleep(min(20,2*(i+1)));continue
            return r
        except Exception:
            time.sleep(i+1)
    return last

def parse_profile(profile_url,canonical_self,canonical_opp,date):
    r=get(profile_url)
    if r is None or r.status_code!=200:return None
    soup=BeautifulSoup(r.text,"html.parser")
    trs=[" ".join(tr.get_text(" ",strip=True).split()) for tr in soup.find_all("tr")]
    sn,on=norm(canonical_self),norm(canonical_opp)
    yr=str(date.year); mon=date.strftime("%b").lower()
    for j,txt in enumerate(trs):
        low=txt.lower()
        if "contender series" not in low and "dwcs" not in low:continue
        if sn not in norm(txt):continue
        win=" ".join(trs[max(0,j-3):min(len(trs),j+5)])
        # Always require exact historical month/year context to avoid same-week-name ambiguity across seasons.
        if yr not in win or mon not in win.lower():continue
        tt=odds_tokens(txt)
        if not tt:continue
        op=tt[0];pl=[v for v in tt[1:] if 100<=abs(v)<=10000]
        cl=pl[-1] if pl else op
        oo=oc=None
        for k in range(max(0,j-2),min(len(trs),j+5)):
            if k==j:continue
            if on in norm(trs[k]):
                ot=odds_tokens(trs[k])
                if ot:
                    oo=ot[0];qq=[v for v in ot[1:] if 100<=abs(v)<=10000];oc=qq[-1] if qq else oo
                break
        return {"selected_open":op,"selected_close":cl,"opponent_open":oo,"opponent_close":oc,"raw_row":txt}
    return None

def alias_options(name,date,alias_map):
    opts=[name]
    a=alias_map.get((str(date.date()),str(name)))
    if a:opts.append(a)
    return opts

def pair_score(x,p,alias_map):
    aopts=alias_options(x.fighter_a,x.event_date,alias_map)
    bopts=alias_options(x.fighter_b,x.event_date,alias_map)
    best=None
    for ao in aopts:
        for bo in bopts:
            s1a,s1b=sim(ao,p.fighter_a),sim(bo,p.fighter_b)
            s2a,s2b=sim(ao,p.fighter_b),sim(bo,p.fighter_a)
            if (s1a+s1b) >= (s2a+s2b):
                vals=(s1a,s1b,False)
            else:
                vals=(s2a,s2b,True)
            score=(vals[0]+vals[1])/2
            cand=(score,min(vals[0],vals[1]),max(vals[0],vals[1]),vals[2])
            if best is None or cand[:3]>best[:3]:best=cand
    return best

def main():
    fights=pd.read_csv(FIGHTS,low_memory=False)
    fights["event_date"]=pd.to_datetime(fights.event_date,errors="coerce")
    odds=pd.read_csv(ODDS,low_memory=False)
    pairs=pd.read_csv(PAIRMAP,low_memory=False)
    alias_map={}
    if ALIASES.exists():
        a=pd.read_csv(ALIASES,low_memory=False)
        for _,x in a.iterrows():
            alias_map[(str(pd.to_datetime(x.dwcs_date).date()),str(x.fighter))]=str(x.archive_alias)

    have=set((str(pd.to_datetime(x.event_date).date()),tuple(sorted((norm(x.fighter_a),norm(x.fighter_b))))) for _,x in odds.iterrows())
    missing=[x for _,x in fights.iterrows() if (str(x.event_date.date()),tuple(sorted((norm(x.fighter_a),norm(x.fighter_b))))) not in have]

    recovered=[];audit=[]
    for x in missing:
        scored=[]
        for _,p in pairs.iterrows():
            q=pair_score(x,p,alias_map)
            if q is None:continue
            score,minsc,maxsc,swapped=q
            # Conservative candidate gate: both fairly strong, or one exact-ish and the other acceptable.
            if not (minsc>=82 or (maxsc>=98 and minsc>=70)):
                continue
            scored.append((score,minsc,maxsc,swapped,p))
        scored.sort(key=lambda z:(z[0],z[1],z[2]),reverse=True)

        found=None
        for score,minsc,maxsc,swapped,p in scored[:12]:
            if not swapped:
                canon_a,canon_b=p.fighter_a,p.fighter_b
                url_a,url_b=p.fighter_a_url,p.fighter_b_url
            else:
                canon_a,canon_b=p.fighter_b,p.fighter_a
                url_a,url_b=p.fighter_b_url,p.fighter_a_url

            q=parse_profile(url_a,canon_a,canon_b,x.event_date)
            selected_target=x.fighter_a
            selected_url=url_a
            selected_canon=canon_a
            opp_canon=canon_b
            if q is None:
                q=parse_profile(url_b,canon_b,canon_a,x.event_date)
                if q is not None:
                    selected_target=x.fighter_b
                    selected_url=url_b
                    selected_canon=canon_b
                    opp_canon=canon_a
            if q is None:continue

            found={
              "season":x.season,"event_date":x.event_date.date().isoformat(),"event_name":x.event_name,
              "fighter_a":x.fighter_a,"fighter_b":x.fighter_b,"winner":x.winner,
              "selected_profile":selected_target,
              "opponent":x.fighter_b if norm(selected_target)==norm(x.fighter_a) else x.fighter_a,
              "selected_open":q["selected_open"],"selected_close":q["selected_close"],
              "opponent_open":q["opponent_open"],"opponent_close":q["opponent_close"],
              "selected_won":norm(x.winner)==norm(selected_target),
              "profile_url":selected_url,"raw_row":q["raw_row"],
              "bfo_event_url":p.event_url,"event_pair_match_score":score,
              "recovery_source":"alias_aware_event_identity",
              "canonical_bfo_selected":selected_canon,"canonical_bfo_opponent":opp_canon
            }
            break

        audit.append({
          "event_date":x.event_date.date().isoformat(),"fighter_a":x.fighter_a,"fighter_b":x.fighter_b,
          "candidate_pairs":len(scored),"recovered":bool(found),
          "top_score":scored[0][0] if scored else None,
          "top_pair":f"{scored[0][4].fighter_a} vs {scored[0][4].fighter_b}" if scored else ""
        })
        if found:recovered.append(found)

    new=pd.DataFrame(recovered)
    merged=pd.concat([odds,new],ignore_index=True) if len(new) else odds.copy()
    merged=merged.drop_duplicates(["event_date","fighter_a","fighter_b"],keep="first")
    merged.to_csv(ODDS,index=False)
    pd.DataFrame(audit).to_csv(OUT/"alias_recovery_audit.csv",index=False)
    status={
      "missing_start":len(missing),"new_recovered":len(new),
      "total_priced_after":len(merged),"coverage_after":len(merged)/len(fights),
      "remaining_missing":len(fights)-len(merged)
    }
    (OUT/"alias_recovery_status.json").write_text(json.dumps(status,indent=2)+"\n")
    print(json.dumps(status,indent=2))

if __name__=="__main__":main()
