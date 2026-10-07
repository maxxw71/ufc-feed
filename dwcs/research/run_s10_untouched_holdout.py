#!/usr/bin/env python3
from __future__ import annotations
import json,re,time,unicodedata
from pathlib import Path
import numpy as np,pandas as pd,requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

ROOT=Path(".")
OUT=ROOT/"dwcs/research/season10_untouched_holdout"
OUT.mkdir(parents=True,exist_ok=True)
BASE="https://www.bestfightodds.com"

def norm(s):
    x=unicodedata.normalize("NFKD",str(s or ""))
    x="".join(ch for ch in x if not unicodedata.combining(ch))
    x=x.lower().replace("’","'").replace("-"," ")
    x=re.sub(r"\b(jr|sr|ii|iii|iv)\b"," ",x)
    return " ".join(re.sub(r"[^a-z0-9]+"," ",x).split())

def score(a,b):
    a,b=norm(a),norm(b)
    if not a or not b:return 0.
    return float(fuzz.WRatio(a,b))

def odds_tokens(txt):
    return [int(x) for x in re.findall(r"(?<!\d)([+-]\d{2,5})(?!\d)",txt)]

ses=requests.Session()
ses.headers.update({"User-Agent":"Mozilla/5.0 Appwiza-DWCS-S10-Holdout/1.0","Accept-Language":"en-US,en;q=0.9"})

def get(url,tries=6):
    last=None
    for i in range(tries):
        try:
            r=ses.get(url,timeout=30);last=r
            if r.status_code==200:return r
            if r.status_code in (429,503):
                time.sleep(min(20,2*(i+1)));continue
            return r
        except Exception:
            time.sleep(i+1)
    return last

def parse_profile(profile_url,self_name,opp_name,date):
    r=get(profile_url)
    if r is None or r.status_code!=200:return None
    soup=BeautifulSoup(r.text,"html.parser")
    trs=[" ".join(tr.get_text(" ",strip=True).split()) for tr in soup.find_all("tr")]
    sn,on=norm(self_name),norm(opp_name)
    yr=str(date.year);mon=date.strftime("%b").lower()
    for j,txt in enumerate(trs):
        if ("contender series" not in txt.lower() and "dwcs" not in txt.lower() and "dana" not in txt.lower()):
            continue
        if sn not in norm(txt):continue
        win=" ".join(trs[max(0,j-3):min(len(trs),j+5)])
        if yr not in win or mon not in win.lower():continue
        tt=odds_tokens(txt)
        if not tt:continue
        op=tt[0];pl=[v for v in tt[1:] if 100<=abs(v)<=10000];cl=pl[-1] if pl else op
        oo=oc=None
        for k in range(max(0,j-2),min(len(trs),j+5)):
            if k==j:continue
            if on in norm(trs[k]):
                ot=odds_tokens(trs[k])
                if ot:
                    oo=ot[0];qq=[v for v in ot[1:] if 100<=abs(v)<=10000];oc=qq[-1] if qq else oo
                break
        return op,cl,oo,oc,txt
    return None

def best_pair(a,b,pairs):
    best=None
    for _,p in pairs.iterrows():
        s1=(score(a,p.fighter_a)+score(b,p.fighter_b))/2
        s2=(score(a,p.fighter_b)+score(b,p.fighter_a))/2
        if s1>=s2:
            indiv=(score(a,p.fighter_a),score(b,p.fighter_b));swap=False
        else:
            indiv=(score(a,p.fighter_b),score(b,p.fighter_a));swap=True
        sc=(indiv[0]+indiv[1])/2
        # conservative: both names need to resemble target, unless one is nearly exact and other strong alias.
        if min(indiv)<72 or max(indiv)<90:continue
        cand=(sc,min(indiv),swap,p)
        if best is None or cand[0:2]>best[0:2]:best=cand
    return best

def american_profit(o,won):
    if pd.isna(o):return np.nan
    return -100. if not won else (10000/abs(o) if o<0 else o)

def main():
    s10=pd.read_csv(ROOT/"dwcs/data/season10_2026_master.csv",low_memory=False)
    s10=s10[s10.status.eq("completed")].copy()
    s10["event_date"]=pd.to_datetime(s10.event_date,errors="coerce").dt.normalize()
    dob=pd.read_csv(ROOT/"dwcs/research/age_enrichment/fighter_dobs.csv",low_memory=False)
    dob["dob_dt"]=pd.to_datetime(dob.dob,errors="coerce").dt.normalize()
    dm={norm(x.fighter):x.dob_dt for _,x in dob[dob.dob_dt.notna()].iterrows()}

    reg_path=ROOT/"dwcs/research/regional_history_v2/dwcs_prefight_regional_features_v2.csv"
    reg=pd.read_csv(reg_path,low_memory=False) if reg_path.exists() else pd.DataFrame()
    if len(reg):
        reg["dwcs_date"]=pd.to_datetime(reg.dwcs_date,errors="coerce").dt.normalize()
        rmap={(x.dwcs_date,norm(x.fighter)):x for _,x in reg.iterrows()}
    else:rmap={}

    pairs=pd.read_csv(ROOT/"dwcs/research/historical_odds/bfo_event_matchup_map.csv",low_memory=False)
    # Keep only 2026 event pages to avoid week-name collisions.
    pairs=pairs[pairs.event_title.astype(str).str.contains("2026|Dana",case=False,na=False)].copy()

    rows=[]
    for _,x in s10.iterrows():
        a,b=str(x.winner),str(x.loser)  # orientation is irrelevant to symmetric selection rules
        da,db=dm.get(norm(a)),dm.get(norm(b))
        aa=((x.event_date-da).days/365.2425) if da is not None else np.nan
        ab=((x.event_date-db).days/365.2425) if db is not None else np.nan

        rec={"season":10,"week":int(x.week),"event_date":x.event_date.date().isoformat(),
             "fighter_a":a,"fighter_b":b,"winner":a,"a_age":aa,"b_age":ab,
             "odds_source":"","pair_match_score":np.nan}

        bp=best_pair(a,b,pairs)
        if bp:
            sc,minsc,swap,p=bp
            if not swap:
                ca,cb=p.fighter_a,p.fighter_b;ua,ub=p.fighter_a_url,p.fighter_b_url
            else:
                ca,cb=p.fighter_b,p.fighter_a;ua,ub=p.fighter_b_url,p.fighter_a_url
            q=parse_profile(ua,ca,cb,x.event_date)
            selected_is_a=True
            if q is None:
                q=parse_profile(ub,cb,ca,x.event_date)
                selected_is_a=False
            if q:
                op,cl,oo,oc,raw=q
                if selected_is_a:
                    rec.update({"a_open_odds":op,"a_close_odds":cl,"b_open_odds":oo,"b_close_odds":oc})
                else:
                    rec.update({"b_open_odds":op,"b_close_odds":cl,"a_open_odds":oo,"a_close_odds":oc})
                rec.update({"odds_source":"BestFightOdds historical S10","pair_match_score":sc,
                            "bfo_event_url":p.event_url,"raw_odds_row":raw})

        for side,name in [("a",a),("b",b)]:
            rg=rmap.get((x.event_date,norm(name)))
            if rg is not None:
                rec[f"{side}_prior_fights"]=rg.get("prior_fights",np.nan)
                rec[f"{side}_regional_history_matched"]=rg.get("regional_history_matched",False)
        rows.append(rec)

    d=pd.DataFrame(rows)
    d.to_csv(OUT/"s10_prefight_holdout.csv",index=False)

    def younger(r,g):
        aa,bb=r.a_age,r.b_age
        if pd.isna(aa) or pd.isna(bb):return None
        if bb-aa>=g:return "a"
        if aa-bb>=g:return "b"
        return None
    def eval_rule(name):
        picks=[]
        for _,r in d.iterrows():
            side=None
            if name=="AGE5":side=younger(r,5)
            elif name=="AGE6":side=younger(r,6)
            elif name=="YOUNG_DOG":
                side=younger(r,3)
                if side:
                    o=pd.to_numeric(pd.Series([r.get(f"{side}_close_odds")]),errors="coerce").iloc[0]
                    if pd.isna(o) or not (100<=o<=250):side=None
            elif name=="YOUTH_EXP":
                side=younger(r,5)
                if side:
                    oth="b" if side=="a" else "a"
                    e1=pd.to_numeric(pd.Series([r.get(f"{side}_prior_fights")]),errors="coerce").iloc[0]
                    e2=pd.to_numeric(pd.Series([r.get(f"{oth}_prior_fights")]),errors="coerce").iloc[0]
                    if pd.isna(e1) or pd.isna(e2) or e1-e2<0:side=None
            if not side:continue
            o=pd.to_numeric(pd.Series([r.get(f"{side}_close_odds")]),errors="coerce").iloc[0]
            if pd.isna(o):continue
            pick=r[f"fighter_{side}"];won=(pick==r.winner)
            picks.append({"method":name,"week":r.week,"event_date":r.event_date,"pick":pick,
                          "opponent":r[f"fighter_{'b' if side=='a' else 'a'}"],"close_odds":o,
                          "won":won,"profit100":american_profit(o,won)})
        p=pd.DataFrame(picks)
        if p.empty:return {"method":name,"n":0},p
        by=[]
        for w,g in p.groupby("week"):
            by.append(f"{int(w)}:{len(g)}:{int(g.won.sum())}:{g.won.mean():.3f}:{g.profit100.sum()/(100*len(g)):.3f}")
        return {"method":name,"n":len(p),"wins":int(p.won.sum()),"win_rate":float(p.won.mean()),
                "roi":float(p.profit100.sum()/(100*len(p))),"week_detail":"|".join(by)},p

    summaries=[];allp=[]
    for m in ["AGE5","AGE6","YOUNG_DOG","YOUTH_EXP"]:
        z,p=eval_rule(m);summaries.append(z)
        if len(p):allp.append(p)
    pd.DataFrame(summaries).to_csv(OUT/"s10_holdout_results.csv",index=False)
    if allp:pd.concat(allp,ignore_index=True).to_csv(OUT/"s10_holdout_picks.csv",index=False)

    status={
      "completed_s10_fights":len(d),
      "fights_both_age_known":int((d.a_age.notna()&d.b_age.notna()).sum()),
      "fights_with_both_closing_odds":int((pd.to_numeric(d.get("a_close_odds"),errors="coerce").notna()&
                                           pd.to_numeric(d.get("b_close_odds"),errors="coerce").notna()).sum()) if "a_close_odds" in d and "b_close_odds" in d else 0,
      "fights_with_any_closing_odds":int((pd.to_numeric(d.get("a_close_odds"),errors="coerce").notna()|
                                          pd.to_numeric(d.get("b_close_odds"),errors="coerce").notna()).sum()) if "a_close_odds" in d and "b_close_odds" in d else 0,
      "rules_frozen_before_s10_evaluation":True,
      "s10_used_for_threshold_selection":False,
      "results":summaries
    }
    (OUT/"status.json").write_text(json.dumps(status,indent=2)+"\n")
    print(json.dumps(status,indent=2))

if __name__=="__main__":main()
