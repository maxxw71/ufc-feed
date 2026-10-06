from __future__ import annotations
import os,re,time,json
from pathlib import Path
from urllib.parse import urljoin
import requests
import pandas as pd
import numpy as np
from bs4 import BeautifulSoup

ROOT=Path(os.environ.get("ROOT","."))
OUT=Path(os.environ["OUT"]); OUT.mkdir(parents=True,exist_ok=True)
S=requests.Session(); S.headers.update({"User-Agent":"Mozilla/5.0 Appwiza-DWCS-Research/1.0"})

def norm(s):
    s="" if pd.isna(s) else str(s)
    return re.sub(r"\s+"," ",re.sub(r"[^a-z0-9]+"," ",s.lower())).strip()

def get(url):
    r=S.get(url,timeout=30); r.raise_for_status(); return r.text

events=[]; fights=[]
for season in range(1,10):
    surl=f"https://boutmetrics.com/dwcs/season/{season}"
    soup=BeautifulSoup(get(surl),"html.parser")
    links=[]
    for a in soup.find_all("a",href=True):
        href=a["href"]
        txt=a.get_text(" ",strip=True)
        if re.search(r"/dwcs/dwcs-\d+-\d+",href):
            full=urljoin(surl,href)
            if full not in [x[0] for x in links]:
                links.append((full,txt))
    for eurl,txt in links:
        es=BeautifulSoup(get(eurl),"html.parser")
        h1=es.find("h1")
        ename=h1.get_text(" ",strip=True) if h1 else txt
        body=es.get_text("\n",strip=True)
        mdate=re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}",body)
        edate=pd.to_datetime(mdate.group(0),errors="coerce") if mdate else pd.NaT
        events.append({"season":season,"event_name":ename,"event_date":edate,"event_url":eurl})
        for h2 in es.find_all("h2"):
            title=h2.get_text(" ",strip=True)
            if " vs. " not in title: continue
            a,b=[x.strip() for x in title.split(" vs. ",1)]
            nxt=h2.find_next_sibling()
            winner=""; result=""; weight=""
            seen=0
            while nxt is not None and getattr(nxt,"name",None)!="h2" and seen<8:
                txt2=nxt.get_text(" ",strip=True) if hasattr(nxt,"get_text") else ""
                if " Bout" in txt2 and " def. " in txt2:
                    result=txt2
                    wm=re.search(r"(.+?)\s+def\.\s+(.+)$",txt2)
                    if wm: winner=wm.group(1).split(" Bout · ")[-1].strip()
                    weight=txt2.split(" Bout",1)[0].strip()
                    break
                nxt=nxt.find_next_sibling(); seen+=1
            fights.append({"season":season,"event_name":ename,"event_date":edate,"event_url":eurl,
                           "fighter_a":a,"fighter_b":b,"winner":winner,"result_text":result,"weight_class":weight})
        time.sleep(0.03)

ev=pd.DataFrame(events).drop_duplicates("event_url").sort_values(["event_date","event_name"])
fx=pd.DataFrame(fights)
fx["event_date"]=pd.to_datetime(fx["event_date"],errors="coerce").dt.normalize()
fx["a_norm"]=fx.fighter_a.map(norm); fx["b_norm"]=fx.fighter_b.map(norm); fx["winner_norm"]=fx.winner.map(norm)
fx.to_csv(OUT/"historical_events.csv",index=False)
fx.to_csv(OUT/"historical_fights.csv",index=False)
ev.to_csv(OUT/"historical_event_index.csv",index=False)

sources=[
 ROOT/"ufc_age_reach_overlap/age_reach_market_sample.csv",
 ROOT/"ufc_height_method_analysis/height_market_sample.csv",
 ROOT/"ufc_reach_method_analysis/reach_market_sample.csv",
]
source_reports=[]; matched_frames=[]
for src in sources:
    if not src.exists(): continue
    d=pd.read_csv(src,low_memory=False)
    source_reports.append({"source":str(src),"rows":len(d),"columns":"|".join(d.columns)})
    need={"event_date","favorite","opponent"}
    if not need.issubset(d.columns): continue
    d["event_date"]=pd.to_datetime(d["event_date"],errors="coerce").dt.normalize()
    d["fav_norm"]=d.favorite.map(norm); d["opp_norm"]=d.opponent.map(norm)
    keys={(r.event_date,tuple(sorted((r.a_norm,r.b_norm)))):i for i,r in fx.iterrows()}
    keep=[]
    for _,r in d.iterrows():
        k=(r.event_date,tuple(sorted((r.fav_norm,r.opp_norm))))
        if k in keys:
            fr=fx.loc[keys[k]]
            z=r.to_dict()
            z["dwcs_season"]=fr.season; z["dwcs_event_name"]=fr.event_name; z["dwcs_winner"]=fr.winner
            if "won" not in z or pd.isna(z.get("won")):
                z["won"]=norm(r.favorite)==fr.winner_norm
            keep.append(z)
    if keep:
        q=pd.DataFrame(keep); q["source_file"]=str(src); matched_frames.append(q)

pd.DataFrame(source_reports).to_csv(OUT/"feature_sources.csv",index=False)
if matched_frames:
    # Prefer age+reach source, then add uniquely named columns from other matched sources by date/fighter/opponent.
    base=matched_frames[0].copy()
    for extra in matched_frames[1:]:
        key=["event_date","favorite","opponent"]
        add=[c for c in extra.columns if c not in base.columns and c not in key]
        if add: base=base.merge(extra[key+add],on=key,how="left")
    m=base
else:
    m=pd.DataFrame()
m.to_csv(OUT/"matched_prefight_features.csv",index=False)

coverage={"events":int(len(ev)),"fights":int(len(fx)),"date_min":str(fx.event_date.min().date()),
          "date_max":str(fx.event_date.max().date()),"matched":int(len(m)),
          "match_rate":float(len(m)/len(fx)) if len(fx) else None}
(OUT/"coverage.json").write_text(json.dumps(coverage,indent=2)+"\n")

lines=["DWCS BOUTMETRICS BACKFILL","="*88,
       f"Events: {len(ev)}",f"Fights: {len(fx)}",
       f"Coverage: {fx.event_date.min().date()} -> {fx.event_date.max().date()}",
       f"Matched prefight rows: {len(m)} ({(len(m)/len(fx)*100 if len(fx) else 0):.1f}%)",""]

if not m.empty and "won" in m.columns:
    if m.won.dtype!=bool:
        m["won"]=m.won.astype(str).str.lower().map({"true":True,"1":True,"yes":True,"false":False,"0":False,"no":False})
    def metric(g):
        g=g.dropna(subset=["won"]); n=len(g)
        if not n:return 0,0,np.nan,np.nan
        w=int(g.won.sum())
        if "profit100" in g:
            p=float(pd.to_numeric(g.profit100,errors="coerce").fillna(0).sum()); roi=p/(100*n)
        elif "fav_decimal" in g:
            dec=pd.to_numeric(g.fav_decimal,errors="coerce")
            p=float(np.where(g.won,100*(dec-1),-100).sum()); roi=p/(100*n)
        else: p=np.nan; roi=np.nan
        return n,w,w/n,roi

    n,w,wr,roi=metric(m)
    lines += [f"All matched favorites: {w}/{n} = {wr*100:.1f}% | ROI {roi*100:+.2f}%" if pd.notna(roi) else f"All matched favorites: {w}/{n} = {wr*100:.1f}%", ""]

    rows=[]
    mp=pd.to_numeric(m.market_prob,errors="coerce") if "market_prob" in m else pd.Series(np.nan,index=m.index)
    for lo,hi in [(0.50,.55),(.55,.60),(.60,.65),(.65,.70),(.70,.75),(.75,.80),(.80,.90),(.90,1.01)]:
        n,w,wr,roi=metric(m[(mp>=lo)&(mp<hi)])
        if n: rows.append({"family":"MARKET_BAND","rule":f"{lo:.2f}-{hi:.2f}","n":n,"wins":w,"win_rate":wr,"roi":roi})
    # Discover based on whatever columns are actually present.
    aliases={
      "age_gap":[("o_age","f_age"),("opp_age","fav_age"),("age_diff",None)],
      "reach_gap":[("f_reach","o_reach"),("fav_reach","opp_reach"),("reach_diff",None)],
      "height_gap":[("f_height","o_height"),("fav_height","opp_height"),("height_diff",None)],
    }
    for new,opts in aliases.items():
        for a,b in opts:
            if a in m.columns and (b is None or b in m.columns):
                m[new]=pd.to_numeric(m[a],errors="coerce") if b is None else pd.to_numeric(m[a],errors="coerce")-pd.to_numeric(m[b],errors="coerce")
                break

    def add(family,rule,mask):
        n,w,wr,roi=metric(m[mask.fillna(False)])
        if n>=8: rows.append({"family":family,"rule":rule,"n":n,"wins":w,"win_rate":wr,"roi":roi})

    if "age_gap" in m:
        # Positive = favorite younger when computed o_age-f_age; direct age_diff semantics may differ, so report only computed form.
        for g in [2,3,4,5,6]:
            for lo,hi in [(.50,.65),(.55,.70),(.60,.75),(.65,.80),(.70,.90)]:
                add("YOUTH_MARKET",f"age_gap>={g}; mkt {lo:.2f}-{hi:.2f}",(m.age_gap>=g)&(mp>=lo)&(mp<hi))
    if "reach_gap" in m:
        for g in [2,3,4,5,6]:
            for lo,hi in [(.50,.70),(.55,.75),(.60,.80),(.65,.90)]:
                add("REACH_MARKET",f"reach_gap>={g}; mkt {lo:.2f}-{hi:.2f}",(m.reach_gap>=g)&(mp>=lo)&(mp<hi))
    if all(c in m for c in ["age_gap","reach_gap"]):
        for ag in [2,3,4]:
            for rg in [2,3,4]:
                add("YOUTH_PLUS_REACH",f"age_gap>={ag}; reach_gap>={rg}",(m.age_gap>=ag)&(m.reach_gap>=rg))

    res=pd.DataFrame(rows)
    if not res.empty: res=res.sort_values(["roi","win_rate","n"],ascending=[False,False,False])
    res.to_csv(OUT/"candidate_rules.csv",index=False)
    strong=res[(res.n>=15)&(res.win_rate>=.70)&(res.roi>0)] if not res.empty else res
    strong.to_csv(OUT/"strong_candidates.csv",index=False)
    lines+=["STRONG CANDIDATES (n>=15, win>=70%, ROI>0)","-"*88]
    if strong.empty: lines.append("None under current first-pass features.")
    else:
        for _,z in strong.head(25).iterrows():
            lines.append(f"{z.family:<22} n={int(z.n):3d} win={z.win_rate*100:5.1f}% ROI={z.roi*100:+6.2f}% | {z.rule}")

(OUT/"report.txt").write_text("\n".join(lines)+"\n")
print("\n".join(lines))
