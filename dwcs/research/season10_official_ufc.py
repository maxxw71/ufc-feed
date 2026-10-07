from __future__ import annotations
import os,re,json,csv,time
from pathlib import Path
import requests
from bs4 import BeautifulSoup
import pandas as pd

OUT=Path(os.environ["OUT"]); OUT.mkdir(parents=True,exist_ok=True)
S=requests.Session()
S.headers.update({"User-Agent":"Mozilla/5.0 Appwiza-DWCS-2026/1.0","Accept-Language":"en-US,en;q=0.9"})

def get(url):
    r=S.get(url,timeout=30); r.raise_for_status(); return r.text

def clean(s): return re.sub(r"\s+"," ",s or "").strip()
def norm(s): return re.sub(r"\s+"," ",re.sub(r"[^a-z0-9]+"," ",(s or "").lower())).strip()

rows=[]
event_rows=[]
for week in range(1,10):
    url=f"https://www.ufc.com/news/dana-whites-contender-series-season-10-week-{week}-results"
    try:
        html=get(url)
    except Exception as e:
        event_rows.append({"season":10,"week":week,"event_url":url,"status":"fetch_failed","error":repr(e)})
        continue
    soup=BeautifulSoup(html,"html.parser")
    title=clean((soup.find("h1") or soup.title).get_text(" ",strip=True))
    body=clean(soup.get_text(" ",strip=True))
    dm=re.search(r"(August|September|October)\s+\d{1,2},\s+2026",body)
    edate=pd.to_datetime(dm.group(0),errors="coerce") if dm else pd.NaT
    event_rows.append({"season":10,"week":week,"event_url":url,"event_name":title,"event_date":edate,"status":"ok"})
    # UFC result articles use headings like "Fighter defeats Opponent via ..."
    for h in soup.find_all(["h2","h3","h4"]):
        txt=clean(h.get_text(" ",strip=True))
        m=re.match(r"(.+?)\s+defeats\s+(.+?)\s+via\s+(.+)$",txt,re.I)
        if not m: continue
        winner=clean(m.group(1)); loser=clean(m.group(2)); tail=clean(m.group(3))
        method=tail; rnd=""; tm=""
        rm=re.search(r"[Rr]ound\s+(\d+)",tail)
        if rm: rnd=rm.group(1)
        tm_m=re.search(r"(\d{1,2}:\d{2})",tail)
        if tm_m: tm=tm_m.group(1)
        rows.append({
            "season":10,"week":week,"event_date":edate,"event_url":url,"event_name":title,
            "fighter_a":winner,"fighter_b":loser,"winner":winner,"loser":loser,
            "method_text":tail,"round":rnd,"time":tm
        })
    time.sleep(.1)

fx=pd.DataFrame(rows)
ev=pd.DataFrame(event_rows)
fx.to_csv(OUT/"season10_results.csv",index=False)
ev.to_csv(OUT/"season10_events.csv",index=False)

# Parse official all-season weigh-in page for 2026.
wurl="https://www.ufc.com/news/official-weigh-in-results-dana-whites-contender-series-season-10"
weights=[]
try:
    ws=BeautifulSoup(get(wurl),"html.parser")
    current_week=None
    for tag in ws.find_all(["h2","h3","h4","p"]):
        txt=clean(tag.get_text(" ",strip=True))
        wm=re.search(r"DWCS Week\s+(\d+) Official Weigh-In Results",txt,re.I)
        if wm:
            current_week=int(wm.group(1)); continue
        if current_week is None: continue
        # e.g. "Welterweight Bout: X (170) vs Y (169.5)"
        m=re.search(r"^(.*?Bout):\s+(.+?)\s+\((\d+(?:\.\d+)?)\)\s+vs\.?\s+(.+?)\s+\((\d+(?:\.\d+)?)\)",txt,re.I)
        if m:
            weights.append({"season":10,"week":current_week,"bout_label":clean(m.group(1)),
                            "fighter_a":clean(m.group(2)),"weight_a":float(m.group(3)),
                            "fighter_b":clean(m.group(4)),"weight_b":float(m.group(5)),
                            "source_url":wurl})
except Exception as e:
    (OUT/"weighin_error.txt").write_text(repr(e)+"\n")
pd.DataFrame(weights).to_csv(OUT/"season10_weights.csv",index=False)

# Merge weights to results by unordered normalized fighter pair.
if not fx.empty:
    fx["pair"]=fx.apply(lambda r:"|".join(sorted([norm(r.fighter_a),norm(r.fighter_b)])),axis=1)
if weights:
    w=pd.DataFrame(weights)
    w["pair"]=w.apply(lambda r:"|".join(sorted([norm(r.fighter_a),norm(r.fighter_b)])),axis=1)
    merged=fx.merge(w[["week","pair","bout_label","fighter_a","weight_a","fighter_b","weight_b"]],on=["week","pair"],how="left",suffixes=("","_weigh"))
else:
    merged=fx.copy()
merged.to_csv(OUT/"season10_combined.csv",index=False)

status={
  "events_expected":9,
  "events_fetched":int((ev.status=="ok").sum()) if not ev.empty else 0,
  "fights_parsed":int(len(fx)),
  "weights_parsed":int(len(weights)),
  "through_week":9,
  "through_date":"2026-10-06"
}
(OUT/"status.json").write_text(json.dumps(status,indent=2)+"\n")
print(json.dumps(status,indent=2))
