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
totals_rows=[]

events=df[["event_url","event_name","event_date","season"]].drop_duplicates()
for _,e in events.iterrows():
    r=S.get(e.event_url,timeout=30); r.raise_for_status()
    soup=BeautifulSoup(r.text,"html.parser")
    seen=set()
    for a in soup.find_all("a",href=True):
        href=a["href"]
        txt=clean(a.get_text(" ",strip=True))
        if not href.startswith("/dwcs/") or "-vs-" not in href: continue
        if not txt or "Full fight stats" in txt: continue
        furl="https://boutmetrics.com"+href
        if furl in seen: continue
        seen.add(furl)
        try:
            fr=S.get(furl,timeout=30); fr.raise_for_status()
            fs=BeautifulSoup(fr.text,"html.parser")
            red=fs.select_one(".fight-detail-red a")
            blue=fs.select_one(".fight-detail-blue a")
            fa=clean(red.get_text(" ",strip=True)) if red else ""
            fb=clean(blue.get_text(" ",strip=True)) if blue else ""
            if not fa or not fb:
                parts=re.split(r"\\s+vs\\.?\\s+",txt,flags=re.I)
                if len(parts)==2: fa,fb=map(clean,parts)
            cand=df[df.event_name==e.event_name]
            match=None
            for _,z in cand.iterrows():
                if {norm(z.fighter_a),norm(z.fighter_b)}=={norm(fa),norm(fb)}:
                    match=z; break
            if match is None: continue

            sec=fs.select_one(".fight-totals-section")
            if sec is None: raise ValueError("fight totals section missing")
            st=clean(sec.get_text(" ",strip=True))

            def pair(label):
                m=re.search(r"(\\d+)\\s+of\\s+(\\d+)\\s+"+re.escape(label)+r"\\s+(\\d+)\\s+of\\s+(\\d+)",st,re.I)
                return tuple(map(int,m.groups())) if m else (np.nan,np.nan,np.nan,np.nan)
            def scalar(label):
                m=re.search(r"(\\d+)\\s+"+re.escape(label)+r"\\s+(\\d+)",st,re.I)
                return tuple(map(int,m.groups())) if m else (np.nan,np.nan)
            def times(label):
                m=re.search(r"(\\d+:\\d{2})\\s+"+re.escape(label)+r"\\s+(\\d+:\\d{2})",st,re.I)
                return (ctrl_sec(m.group(1)),ctrl_sec(m.group(2))) if m else (np.nan,np.nan)

            sig=pair("Significant strikes")
            td=pair("Takedowns")
            ctrl=times("Control time")
            kd=scalar("Knockdowns")
            sub=scalar("Submission attempts")

            vals=[
                dict(season=e.season,event_name=e.event_name,event_date=e.event_date,fighter=fa,opponent=fb,
                     sig_landed=sig[0],sig_attempted=sig[1],td_landed=td[0],td_attempted=td[1],
                     control_seconds=ctrl[0],knockdowns=kd[0],sub_attempts=sub[0],source_url=furl),
                dict(season=e.season,event_name=e.event_name,event_date=e.event_date,fighter=fb,opponent=fa,
                     sig_landed=sig[2],sig_attempted=sig[3],td_landed=td[2],td_attempted=td[3],
                     control_seconds=ctrl[1],knockdowns=kd[1],sub_attempts=sub[1],source_url=furl),
            ]
            totals_rows.extend(vals)
            fight_rows.append(dict(season=e.season,event_name=e.event_name,event_date=e.event_date,
                                   fighter_a=match.fighter_a,fighter_b=match.fighter_b,winner=match.winner,
                                   fight_url=furl,parsed=True))
        except Exception as ex:
            fight_rows.append(dict(season=e.season,event_name=e.event_name,event_date=e.event_date,
                                   fighter_a=fa if 'fa' in locals() else "",fighter_b=fb if 'fb' in locals() else "",
                                   fight_url=furl,parsed=False,error=repr(ex)))
        time.sleep(.02)

totals=pd.DataFrame(totals_rows)
fights=pd.DataFrame(fight_rows)
fights.to_csv(OUT/"technical_fight_pages.csv",index=False)
totals.to_csv(OUT/"technical_fight_totals.csv",index=False)

snaps=[]
if not totals.empty:
    pairmap={(str(x.event_name),norm(x.fighter)):x for _,x in totals.iterrows()}
    hist={}
    for _,x in totals.sort_values(["event_date","event_name"]).iterrows():
        fn=norm(x.fighter); on=norm(x.opponent)
        prior=hist.get(fn,[])
        def sm(k): return sum((0 if pd.isna(z.get(k)) else float(z.get(k))) for z in prior)
        sigL,sigA=sm("sig_landed"),sm("sig_attempted")
        oppSigL,oppSigA=sm("opp_sig_landed"),sm("opp_sig_attempted")
        tdL,tdA=sm("td_landed"),sm("td_attempted")
        oppTdL,oppTdA=sm("opp_td_landed"),sm("opp_td_attempted")
        ctrl,oppctrl=sm("control_seconds"),sm("opp_control_seconds")
        kd,sub=sm("knockdowns"),sm("sub_attempts")
        snaps.append(dict(
            season=x.season,event_name=x.event_name,event_date=x.event_date,fighter=x.fighter,opponent=x.opponent,
            prior_technical_fights=len(prior),
            prior_sig_landed=sigL,prior_sig_attempted=sigA,
            prior_sig_accuracy=sigL/sigA if sigA else np.nan,
            prior_sig_absorbed=oppSigL,
            prior_sig_defense=1-(oppSigL/oppSigA) if oppSigA else np.nan,
            prior_sig_diff=sigL-oppSigL,
            prior_td_landed=tdL,prior_td_attempted=tdA,
            prior_td_accuracy=tdL/tdA if tdA else np.nan,
            prior_td_allowed=oppTdL,
            prior_td_defense=1-(oppTdL/oppTdA) if oppTdA else np.nan,
            prior_control_seconds=ctrl,prior_control_diff=ctrl-oppctrl,
            prior_knockdowns=kd,prior_sub_attempts=sub
        ))
        opp=pairmap.get((str(x.event_name),on))
        cur=dict(
            sig_landed=x.sig_landed,sig_attempted=x.sig_attempted,td_landed=x.td_landed,td_attempted=x.td_attempted,
            control_seconds=x.control_seconds,knockdowns=x.knockdowns,sub_attempts=x.sub_attempts,
            opp_sig_landed=0 if opp is None else opp.sig_landed,
            opp_sig_attempted=0 if opp is None else opp.sig_attempted,
            opp_td_landed=0 if opp is None else opp.td_landed,
            opp_td_attempted=0 if opp is None else opp.td_attempted,
            opp_control_seconds=0 if opp is None else opp.control_seconds
        )
        hist.setdefault(fn,[]).append(cur)
    pd.DataFrame(snaps).to_csv(OUT/"prefight_technical_snapshots.csv",index=False)

status={
  "archive_fights":int(len(df)),
  "fight_pages_seen":int(len(fights)),
  "fight_pages_parsed":int(fights.get("parsed",pd.Series(dtype=bool)).fillna(False).sum()),
  "fighter_fight_total_rows":int(len(totals)),
  "snapshot_rows":int(len(snaps)),
  "snapshots_with_prior_technical_history":int(sum(1 for x in snaps if x["prior_technical_fights"]>0))
}
(OUT/"status.json").write_text(json.dumps(status,indent=2,default=str)+"\\n")
print(json.dumps(status,indent=2))
