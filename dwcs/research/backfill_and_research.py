from __future__ import annotations
import os, re, time, json, math
from pathlib import Path
from urllib.parse import urljoin
import requests
import pandas as pd
import numpy as np
from bs4 import BeautifulSoup

ROOT = Path(os.environ["ROOT"])
OUT = Path(os.environ["OUT"])
OUT.mkdir(parents=True, exist_ok=True)

UA = {"User-Agent":"Mozilla/5.0 Appwiza-DWCS-Research/1.0"}
S = requests.Session()
S.headers.update(UA)

def norm(s):
    s = "" if pd.isna(s) else str(s)
    s = re.sub(r"[^a-z0-9]+"," ",s.lower()).strip()
    return re.sub(r"\s+"," ",s)

def get(url, tries=3):
    last=None
    for i in range(tries):
        try:
            r=S.get(url,timeout=25)
            r.raise_for_status()
            return r.text
        except Exception as e:
            last=e
            time.sleep(1.5*(i+1))
    raise last

def parse_date(s):
    return pd.to_datetime(s, errors="coerce")

index_url="http://ufcstats.com/statistics/events/completed?page=all"
html=get(index_url)
soup=BeautifulSoup(html,"html.parser")
events=[]
for tr in soup.select("tr.b-statistics__table-row"):
    a=tr.select_one("a.b-link")
    if not a: continue
    name=a.get_text(" ",strip=True)
    low=name.lower()
    if "contender series" not in low:
        continue
    tds=tr.select("td")
    date=None
    for td in tds:
        txt=td.get_text(" ",strip=True)
        d=pd.to_datetime(txt,errors="coerce")
        if pd.notna(d):
            date=d.normalize()
            break
    events.append({"event_name":name,"event_url":a.get("href"),"event_date":date})

ev=pd.DataFrame(events).drop_duplicates("event_url")
if ev.empty:
    raise SystemExit("No historical Contender Series events found at UFCStats.")
ev=ev.sort_values("event_date")
ev.to_csv(OUT/"historical_events.csv",index=False)

fights=[]
for _,e in ev.iterrows():
    page=get(e.event_url)
    sp=BeautifulSoup(page,"html.parser")
    for tr in sp.select("tr.b-fight-details__table-row.b-fight-details__table-row__hover"):
        flinks=tr.select("a.b-link.b-link_style_black")
        names=[]
        for a in flinks:
            txt=a.get_text(" ",strip=True)
            if txt and txt not in names:
                names.append(txt)
        if len(names)<2:
            continue
        cols=[x.get_text(" ",strip=True) for x in tr.select("td.b-fight-details__table-col")]
        weight=cols[6] if len(cols)>6 else ""
        method=cols[7] if len(cols)>7 else ""
        rnd=cols[8] if len(cols)>8 else ""
        tm=cols[9] if len(cols)>9 else ""
        fights.append({
            "event_name":e.event_name,
            "event_date":e.event_date,
            "event_url":e.event_url,
            "fight_url":tr.get("data-link",""),
            "fighter_a":names[0],
            "fighter_b":names[1],
            "weight_class":weight,
            "method":method,
            "round":rnd,
            "time":tm,
        })
    time.sleep(0.05)

fx=pd.DataFrame(fights)
fx["event_date"]=pd.to_datetime(fx["event_date"],errors="coerce").dt.normalize()
fx["a_norm"]=fx.fighter_a.map(norm)
fx["b_norm"]=fx.fighter_b.map(norm)
fx.to_csv(OUT/"historical_fights.csv",index=False)

src=ROOT/"new_category_discovery/prefight_favorite_features.csv"
if not src.exists():
    raise SystemExit(f"Missing feature master: {src}")
d=pd.read_csv(src,low_memory=False)
d["event_date"]=pd.to_datetime(d["event_date"],errors="coerce").dt.normalize()
d["fav_norm"]=d["favorite"].map(norm)
d["opp_norm"]=d["opponent"].map(norm)

pair_keys={}
for i,r in fx.iterrows():
    pair_keys[(r.event_date,tuple(sorted((r.a_norm,r.b_norm))))]=i

matched=[]
for i,r in d.iterrows():
    key=(r.event_date,tuple(sorted((r.fav_norm,r.opp_norm))))
    if key in pair_keys:
        z=r.to_dict()
        frow=fx.loc[pair_keys[key]]
        z.update({
            "dwcs_event_name":frow.event_name,
            "dwcs_weight_class":frow.weight_class,
            "dwcs_fight_url":frow.fight_url,
            "dwcs_method":frow.method,
        })
        matched.append(z)

m=pd.DataFrame(matched)
m.to_csv(OUT/"matched_prefight_features.csv",index=False)

coverage={
    "events_found":int(len(ev)),
    "fights_found":int(len(fx)),
    "priced_feature_matches":int(len(m)),
    "fight_match_rate":float(len(m)/len(fx)) if len(fx) else None,
    "date_min":str(ev.event_date.min().date()),
    "date_max":str(ev.event_date.max().date()),
    "feature_columns":list(d.columns),
}
(OUT/"coverage.json").write_text(json.dumps(coverage,indent=2,default=str)+"\n")

if m.empty:
    (OUT/"report.txt").write_text("DWCS historical archive found, but zero rows matched the prefight feature master.\n")
    raise SystemExit(0)

for c in m.columns:
    if c not in {"event_date","favorite","opponent","dwcs_event_name","dwcs_weight_class","dwcs_fight_url","dwcs_method","fav_norm","opp_norm"}:
        try: m[c]=pd.to_numeric(m[c],errors="ignore")
        except: pass
if m["won"].dtype != bool:
    m["won"]=m["won"].astype(str).str.lower().map({"true":True,"1":True,"yes":True,"false":False,"0":False,"no":False})

def metric(g):
    g=g.dropna(subset=["won"])
    if len(g)==0: return {"n":0,"wins":0,"win_rate":np.nan,"roi":np.nan,"profit100":0}
    wins=int(g.won.astype(bool).sum())
    profit=float(pd.to_numeric(g.get("profit100",pd.Series([0]*len(g),index=g.index)),errors="coerce").fillna(0).sum())
    return {"n":len(g),"wins":wins,"win_rate":wins/len(g),"roi":profit/(100*len(g)),"profit100":profit}

summary=[]
summary.append({"family":"ALL_DWCS_PRICED_FAVORITES","rule":"all matched DWCS favorite rows",**metric(m)})

if "market_prob" in m:
    mp=pd.to_numeric(m.market_prob,errors="coerce")
    for lo,hi in [(0.50,0.55),(0.55,0.60),(0.60,0.65),(0.65,0.70),(0.70,0.75),(0.75,0.80),(0.80,0.90),(0.90,1.01)]:
        g=m[(mp>=lo)&(mp<hi)]
        summary.append({"family":"MARKET_BAND","rule":f"{lo:.2f}-{hi:.2f}",**metric(g)})

# Derive reusable differences from whatever the UFC feature master exposes.
pairs=[
 ("experience_gap","f_fights","o_fights"),
 ("winpct_gap","f_win_pct","o_win_pct"),
 ("last3_gap","f_last3","o_last3"),
 ("last5_gap","f_last5","o_last5"),
 ("layoff_gap","o_layoff","f_layoff"),
 ("finish_loss_edge","o_finish_loss_pct","f_finish_loss_pct"),
 ("age_gap","o_age","f_age"),
 ("reach_gap","f_reach","o_reach"),
 ("height_gap","f_height","o_height"),
]
for new,a,b in pairs:
    if a in m and b in m:
        m[new]=pd.to_numeric(m[a],errors="coerce")-pd.to_numeric(m[b],errors="coerce")

rows=[]
def add(family,rule,mask):
    g=m[mask.fillna(False)].copy()
    z=metric(g)
    if z["n"]>=8:
        rows.append({"family":family,"rule":rule,**z})

mp=pd.to_numeric(m["market_prob"],errors="coerce") if "market_prob" in m else pd.Series(np.nan,index=m.index)

# Broad, predeclared DWCS mechanism scans. These are discovery only; no live promotion.
if "experience_gap" in m:
    for eg in [2,4,6,8,10]:
        for lo,hi in [(0.50,0.65),(0.55,0.70),(0.60,0.75),(0.65,0.80)]:
            add("EXPERIENCE_EDGE",f"exp_gap>={eg}; mkt {lo:.2f}-{hi:.2f}",(m.experience_gap>=eg)&(mp>=lo)&(mp<hi))
if "age_gap" in m:
    for ag in [2,3,4,5,6]:
        for lo,hi in [(0.50,0.65),(0.55,0.70),(0.60,0.75),(0.65,0.80)]:
            add("YOUTH_EDGE",f"favorite younger by >={ag}y; mkt {lo:.2f}-{hi:.2f}",(m.age_gap>=ag)&(mp>=lo)&(mp<hi))
if "winpct_gap" in m:
    for wg in [.05,.10,.15,.20,.25]:
        add("CAREER_QUALITY_EDGE",f"career win% gap>={wg:.2f}",m.winpct_gap>=wg)
if "last3_gap" in m:
    for g0 in [.10,.20,.30,.40]:
        add("RECENT_FORM_EDGE",f"last3 gap>={g0:.2f}",m.last3_gap>=g0)
if "layoff_gap" in m:
    for days in [60,90,120,180,240]:
        add("FRESHNESS_EDGE",f"opponent layoff minus favorite layoff>={days}d",m.layoff_gap>=days)
if "reach_gap" in m:
    for rg in [2,3,4,5,6]:
        add("REACH_EDGE",f"favorite reach gap>={rg}",m.reach_gap>=rg)
if "finish_loss_edge" in m:
    for fg in [.05,.10,.15,.20]:
        add("DURABILITY_EDGE",f"opp finish-loss% minus favorite>={fg:.2f}",m.finish_loss_edge>=fg)

# Combined mechanics to look for genuinely DWCS-specific interactions.
if all(c in m for c in ["experience_gap","winpct_gap"]):
    for eg in [2,4,6,8]:
        for wg in [.05,.10,.15,.20]:
            add("EXPERIENCE_PLUS_QUALITY",f"exp_gap>={eg}; winpct_gap>={wg:.2f}",(m.experience_gap>=eg)&(m.winpct_gap>=wg))
if all(c in m for c in ["age_gap","experience_gap"]):
    for ag in [2,3,4]:
        for eg in [2,4,6]:
            add("YOUTH_PLUS_EXPERIENCE",f"younger>={ag}y; exp_gap>={eg}",(m.age_gap>=ag)&(m.experience_gap>=eg))
if all(c in m for c in ["experience_gap","layoff_gap"]):
    for eg in [2,4,6]:
        for lg in [60,120,180]:
            add("EXPERIENCE_PLUS_FRESHNESS",f"exp_gap>={eg}; layoff_gap>={lg}",(m.experience_gap>=eg)&(m.layoff_gap>=lg))
if all(c in m for c in ["winpct_gap","last3_gap"]):
    for wg in [.05,.10,.15]:
        for rg in [.10,.20,.30]:
            add("QUALITY_PLUS_FORM",f"winpct_gap>={wg:.2f}; last3_gap>={rg:.2f}",(m.winpct_gap>=wg)&(m.last3_gap>=rg))

cand=pd.DataFrame(rows)
if not cand.empty:
    cand=cand.sort_values(["roi","win_rate","n"],ascending=[False,False,False])
cand.to_csv(OUT/"candidate_rules.csv",index=False)
pd.DataFrame(summary).to_csv(OUT/"baseline_summary.csv",index=False)

# Event / season breakdown.
m["year"]=m.event_date.dt.year
yb=[]
for y,g in m.groupby("year"):
    yb.append({"year":int(y),**metric(g)})
pd.DataFrame(yb).to_csv(OUT/"yearly_summary.csv",index=False)

# Identify strongest candidates with minimum sample constraints.
strong=cand[(cand.n>=15)&(cand.win_rate>=0.70)&(cand.roi>0)].copy() if not cand.empty else cand
strong.to_csv(OUT/"strong_candidates.csv",index=False)

lines=[
 "DWCS HISTORICAL RESEARCH",
 "="*96,
 f"Historical Contender Series events found: {len(ev)}",
 f"Historical fights found: {len(fx)}",
 f"Matched to UFC prefight feature master: {len(m)} ({len(m)/len(fx)*100:.1f}% of fights)",
 f"Coverage: {ev.event_date.min().date()} through {ev.event_date.max().date()}",
 "",
 "IMPORTANT: all outputs are isolated DWCS research. Nothing here changes the UFC record.",
 "",
 "BASELINE",
 "-"*96,
]
for z in summary:
    wr=z["win_rate"]*100 if pd.notna(z["win_rate"]) else float("nan")
    roi=z["roi"]*100 if pd.notna(z["roi"]) else float("nan")
    lines.append(f"{z['family']:<24} {z['rule']:<34} n={z['n']:3d} win={wr:5.1f}% ROI={roi:+6.2f}%")
lines += ["","STRONGEST DISCOVERY CANDIDATES (n>=15, win>=70%, positive ROI)","-"*96]
if strong is None or len(strong)==0:
    lines.append("No candidate met the provisional strength gate.")
else:
    for _,z in strong.head(30).iterrows():
        lines.append(f"{z.family:<28} n={int(z.n):3d} win={z.win_rate*100:5.1f}% ROI={z.roi*100:+6.2f}% | {z.rule}")
(OUT/"report.txt").write_text("\n".join(lines)+"\n")
print("\n".join(lines))
