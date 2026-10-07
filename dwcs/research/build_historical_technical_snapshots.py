from __future__ import annotations
import os,re,json,time
from pathlib import Path
import pandas as pd, numpy as np, requests
from bs4 import BeautifulSoup

ROOT=Path(os.environ.get("ROOT","."))
OUT=Path(os.environ["OUT"]); OUT.mkdir(parents=True,exist_ok=True)
FIGHTS=ROOT/"dwcs/research/results_boutmetrics/historical_fights.csv"
df=pd.read_csv(FIGHTS,low_memory=False)
df["event_date"]=pd.to_datetime(df["event_date"],errors="coerce").dt.normalize()

S=requests.Session()
S.headers.update({"User-Agent":"Mozilla/5.0 Appwiza-DWCS-Historical/1.0","Accept-Language":"en-US,en;q=0.9"})

def clean(s): return re.sub(r"\s+"," ",s or "").strip()
def norm(s):
    s=str(s or "").lower().replace("’","'").replace("-"," ")
    s=re.sub(r"\b(jr|sr|ii|iii|iv)\b"," ",s)
    s=re.sub(r"[^a-z0-9]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()
def ctrl_sec(s):
    if pd.isna(s): return np.nan
    m=re.match(r"^(\d+):(\d+)$",str(s).strip())
    return int(m.group(1))*60+int(m.group(2)) if m else np.nan

fight_rows=[]
round_rows=[]

events=df[["event_url","event_name","event_date","season"]].drop_duplicates()
for ei,e in events.iterrows():
    r=S.get(e.event_url,timeout=30); r.raise_for_status()
    soup=BeautifulSoup(r.text,"html.parser")
    for a in soup.find_all("a",href=True):
        href=a["href"]
        txt=clean(a.get_text(" ",strip=True))
        if not href.startswith("/dwcs/") or "-vs-" not in href: continue
        if not txt or "Full fight stats" in txt: continue
        furl="https://boutmetrics.com"+href
        try:
            fr=S.get(furl,timeout=30); fr.raise_for_status()
            fs=BeautifulSoup(fr.text,"html.parser")
            body=[clean(x) for x in fs.get_text("\n").splitlines() if clean(x)]
            full=" | ".join(body)
            # Use known matchup text from link; pair to archive row by normalized names.
            parts=re.split(r"\s+vs\.?\s+",txt,flags=re.I)
            if len(parts)!=2: continue
            fa,fb=clean(parts[0]),clean(parts[1])
            # Locate corresponding archive row to get winner.
            cand=df[(df.event_name==e.event_name)]
            match=None
            for _,z in cand.iterrows():
                if {norm(z.fighter_a),norm(z.fighter_b)}=={norm(fa),norm(fb)}:
                    match=z; break
            if match is None: continue

            # Parse round-stat table from text sequence. BoutMetrics fight pages carry
            # repeated fighter, round, sigstr L/A, KD, TD L/A, sub, control.
            text=clean(fs.get_text(" ",strip=True))
            # Capture totals if present from summary tables first.
            # Fallback aggregation will come from round rows if exact DOM table parsing succeeds.
            tables=fs.find_all("table")
            local=[]
            for table in tables:
                headers=[clean(th.get_text(" ",strip=True)).lower() for th in table.find_all("th")]
                if not any("sig" in h for h in headers): continue
                for tr in table.find_all("tr"):
                    cells=[clean(td.get_text(" ",strip=True)) for td in tr.find_all(["td","th"])]
                    if len(cells)<6: continue
                    rowtxt=" | ".join(cells)
                    fighter=None
                    for nm in [fa,fb]:
                        if norm(nm) in norm(rowtxt): fighter=nm; break
                    if fighter is None: continue
                    rndm=re.search(r"\b([1-5])\b",rowtxt)
                    sigm=re.search(r"(\d+)\s*/\s*(\d+)",rowtxt)
                    pairs=re.findall(r"(\d+)\s*/\s*(\d+)",rowtxt)
                    if not rndm or not pairs: continue
                    # usually first pair is sig strikes, second is TD
                    sigL,sigA=map(int,pairs[0])
                    tdL,tdA=(map(int,pairs[1]) if len(pairs)>1 else (0,0))
                    # pull scalar fields heuristically after significant-strike pair
                    nums=[int(x) for x in re.findall(r"(?<![:/])\b\d+\b(?![:/])",rowtxt)]
                    ctrlm=re.findall(r"\b\d+:\d{2}\b",rowtxt)
                    rec={"season":e.season,"event_name":e.event_name,"event_date":e.event_date,
                         "fighter":fighter,"opponent":fb if norm(fighter)==norm(fa) else fa,
                         "round":int(rndm.group(1)),"sig_landed":sigL,"sig_attempted":sigA,
                         "td_landed":int(tdL),"td_attempted":int(tdA),
                         "control_seconds":ctrl_sec(ctrlm[-1]) if ctrlm else np.nan,
                         "source_url":furl}
                    local.append(rec)
            if local:
                round_rows.extend(local)
            fight_rows.append({"season":e.season,"event_name":e.event_name,"event_date":e.event_date,
                              "fighter_a":match.fighter_a,"fighter_b":match.fighter_b,"winner":match.winner,
                              "fight_url":furl,"round_rows":len(local)})
        except Exception as ex:
            fight_rows.append({"season":e.season,"event_name":e.event_name,"event_date":e.event_date,
                              "fighter_a":fa if 'fa' in locals() else "","fighter_b":fb if 'fb' in locals() else "",
                              "fight_url":furl,"error":repr(ex),"round_rows":0})
        time.sleep(.03)

rounds=pd.DataFrame(round_rows)
fights=pd.DataFrame(fight_rows)
fights.to_csv(OUT/"technical_fight_pages.csv",index=False)
rounds.to_csv(OUT/"technical_round_rows.csv",index=False)

# Aggregate historical technical rows and create strictly point-in-time rolling features.
if not rounds.empty:
    agg=rounds.groupby(["season","event_name","event_date","fighter","opponent"],as_index=False).agg(
        sig_landed=("sig_landed","sum"),sig_attempted=("sig_attempted","sum"),
        td_landed=("td_landed","sum"),td_attempted=("td_attempted","sum"),
        control_seconds=("control_seconds","sum"),rounds_observed=("round","nunique")
    )
    # Derive opponent allowed stats by mirrored pair.
    pairmap={}
    for _,x in agg.iterrows():
        pairmap[(str(x.event_name),norm(x.fighter))]=x
    hist={}
    snaps=[]
    for _,x in agg.sort_values(["event_date","event_name"]).iterrows():
        fn=norm(x.fighter); on=norm(x.opponent)
        prior=hist.get(fn,[])
        sigL=sum(z["sig_landed"] for z in prior); sigA=sum(z["sig_attempted"] for z in prior)
        tdL=sum(z["td_landed"] for z in prior); tdA=sum(z["td_attempted"] for z in prior)
        oppSigL=sum(z["opp_sig_landed"] for z in prior); oppSigA=sum(z["opp_sig_attempted"] for z in prior)
        oppTdL=sum(z["opp_td_landed"] for z in prior); oppTdA=sum(z["opp_td_attempted"] for z in prior)
        ctrl=sum(z["control_seconds"] for z in prior); oppctrl=sum(z["opp_control_seconds"] for z in prior)
        snaps.append({
            "season":x.season,"event_name":x.event_name,"event_date":x.event_date,
            "fighter":x.fighter,"opponent":x.opponent,"prior_technical_fights":len(prior),
            "prior_sig_landed":sigL,"prior_sig_attempted":sigA,
            "prior_sig_accuracy":sigL/sigA if sigA else np.nan,
            "prior_sig_absorbed":oppSigL,"prior_sig_defense":1-(oppSigL/oppSigA) if oppSigA else np.nan,
            "prior_sig_diff":sigL-oppSigL,
            "prior_td_landed":tdL,"prior_td_attempted":tdA,
            "prior_td_accuracy":tdL/tdA if tdA else np.nan,
            "prior_td_allowed":oppTdL,"prior_td_defense":1-(oppTdL/oppTdA) if oppTdA else np.nan,
            "prior_control_seconds":ctrl,"prior_control_diff":ctrl-oppctrl
        })
        opp=pairmap.get((str(x.event_name),on))
        cur={"sig_landed":x.sig_landed,"sig_attempted":x.sig_attempted,"td_landed":x.td_landed,
             "td_attempted":x.td_attempted,"control_seconds":0 if pd.isna(x.control_seconds) else x.control_seconds,
             "opp_sig_landed":0 if opp is None else opp.sig_landed,
             "opp_sig_attempted":0 if opp is None else opp.sig_attempted,
             "opp_td_landed":0 if opp is None else opp.td_landed,
             "opp_td_attempted":0 if opp is None else opp.td_attempted,
             "opp_control_seconds":0 if opp is None or pd.isna(opp.control_seconds) else opp.control_seconds}
        hist.setdefault(fn,[]).append(cur)
    pd.DataFrame(snaps).to_csv(OUT/"prefight_technical_snapshots.csv",index=False)
    agg.to_csv(OUT/"technical_fight_totals.csv",index=False)

status={
  "archive_fights":int(len(df)),
  "fight_pages_seen":int(len(fights)),
  "fight_pages_with_round_rows":int((fights.get("round_rows",pd.Series(dtype=int)).fillna(0)>0).sum()),
  "round_rows":int(len(rounds)),
  "snapshot_rows":int(len(pd.DataFrame(snaps))) if not rounds.empty else 0
}
(OUT/"status.json").write_text(json.dumps(status,indent=2,default=str)+"\n")
print(json.dumps(status,indent=2))
