from __future__ import annotations

from pathlib import Path
from collections import Counter
import html
import json
import os
import re
import time
import requests
import pandas as pd

ROOT = Path(os.environ.get("NFL_ROOT", "/home/anestishkurti92/nfl-predictor-v1"))
RAW = ROOT / "data" / "raw"
SCHEDULES = RAW / "schedules_2006_2026.parquet"
OUT = RAW / "coaching_staff_2006_2026.csv"
STATUS = ROOT / "NFL_COACHING_STAFF_STATUS.json"
API = "https://en.wikipedia.org/w/api.php"

BASE_NAMES = {
    "ARI":"Arizona Cardinals","ATL":"Atlanta Falcons","BAL":"Baltimore Ravens","BUF":"Buffalo Bills",
    "CAR":"Carolina Panthers","CHI":"Chicago Bears","CIN":"Cincinnati Bengals","CLE":"Cleveland Browns",
    "DAL":"Dallas Cowboys","DEN":"Denver Broncos","DET":"Detroit Lions","GB":"Green Bay Packers",
    "HOU":"Houston Texans","IND":"Indianapolis Colts","JAX":"Jacksonville Jaguars","KC":"Kansas City Chiefs",
    "MIA":"Miami Dolphins","MIN":"Minnesota Vikings","NE":"New England Patriots","NO":"New Orleans Saints",
    "NYG":"New York Giants","NYJ":"New York Jets","PHI":"Philadelphia Eagles","PIT":"Pittsburgh Steelers",
    "SF":"San Francisco 49ers","SEA":"Seattle Seahawks","TB":"Tampa Bay Buccaneers","TEN":"Tennessee Titans",
}

def franchise_name(team: str, season: int) -> str | None:
    t = str(team).upper()
    if t in BASE_NAMES: return BASE_NAMES[t]
    if t in {"OAK","LV"}: return "Oakland Raiders" if season <= 2019 else "Las Vegas Raiders"
    if t in {"SD","LAC"}: return "San Diego Chargers" if season <= 2016 else "Los Angeles Chargers"
    if t in {"STL","LA","LAR"}: return "St. Louis Rams" if season <= 2015 else "Los Angeles Rams"
    if t == "WAS":
        if season <= 2019: return "Washington Redskins"
        if season <= 2021: return "Washington Football Team"
        return "Washington Commanders"
    return None

def clean_value(v: str | None) -> str | None:
    if not v: return None
    s = html.unescape(v)
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"\{\{small\|([^{}]*)\}\}", r"\1", s, flags=re.I)
    s = re.sub(r"\{\{nowrap\|([^{}]*)\}\}", r"\1", s, flags=re.I)
    s = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]", r"\1", s)
    s = re.sub(r"<br\s*/?>", "; ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\s+", " ", s).strip(" ;,\n\t")
    if not s or "=" in s or s.startswith("|"): return None
    return s

def extract_field(wikitext: str, names: list[str]) -> tuple[str|None,str|None]:
    for field in names:
        m = re.search(r"^\|\s*" + re.escape(field) + r"\s*=\s*(.*?)\s*$", wikitext, flags=re.I|re.M)
        if m:
            cleaned = clean_value(m.group(1))
            if cleaned:
                return cleaned, field
    return None, None

def fetch_staff(season: int, team: str, session: requests.Session):
    name = franchise_name(team, season)
    if not name: return None
    title = f"{season} {name} season"
    params = {"action":"parse","page":title,"prop":"wikitext","section":0,"format":"json","formatversion":2}
    r = session.get(API, params=params, timeout=25, headers={"User-Agent":"AppWiza-NFL-research/1.0"})
    r.raise_for_status()
    obj = r.json()
    parse = obj.get("parse")
    if not parse: return {"title":title,"oc":None,"dc":None,"page_found":False}
    w = parse.get("wikitext", "")
    oc, oc_field = extract_field(w, ["off_coach","offensive_coordinator","offensive coach"])
    dc, dc_field = extract_field(w, ["def_coach","defensive_coordinator","defensive coach"])
    return {"title":parse.get("title", title),"oc":oc,"dc":dc,"oc_field":oc_field,"dc_field":dc_field,"page_found":True}

def primary(values):
    vals=[str(x).strip() for x in values if pd.notna(x) and str(x).strip()]
    return Counter(vals).most_common(1)[0][0] if vals else None

s = pd.read_parquet(SCHEDULES)
s = s[s.game_type.eq("REG")].copy()
rows=[]
for side in ("home","away"):
    rows.append(pd.DataFrame({
        "season":s.season.astype(int), "week":s.week, "team":s[f"{side}_team"],
        "head_coach":s[f"{side}_coach"], "game_id":s.game_id,
    }))
g = pd.concat(rows, ignore_index=True)
team_seasons = g[["season","team"]].drop_duplicates().sort_values(["season","team"])
hc = g.groupby(["season","team"], as_index=False).agg(
    head_coach=("head_coach", primary),
    head_coach_count=("head_coach", lambda x: len(set(v for v in x.dropna().astype(str) if v.strip()))),
)

sess=requests.Session()
out=[]
failures=[]
for i, rec in enumerate(team_seasons.itertuples(index=False), 1):
    season, team = int(rec.season), str(rec.team)
    try:
        st=fetch_staff(season, team, sess)
        if st is None:
            failures.append({"season":season,"team":team,"reason":"unknown_franchise"}); st={}
    except Exception as e:
        failures.append({"season":season,"team":team,"reason":repr(e)}); st={}
    out.append({
        "season":season,"team":team,
        "offensive_coordinator":st.get("oc"),"defensive_coordinator":st.get("dc"),
        "staff_page":st.get("title"),"staff_page_found":bool(st.get("page_found",False)),
        "oc_source_field":st.get("oc_field"),"dc_source_field":st.get("dc_field"),
        "staff_source":"wikipedia_team_season_infobox",
    })
    if i % 50 == 0: print(f"staff {i}/{len(team_seasons)}", flush=True)
    time.sleep(0.08)

staff=pd.DataFrame(out).merge(hc, on=["season","team"], how="left").sort_values(["team","season"])
for role in ["head_coach","offensive_coordinator","defensive_coordinator"]:
    prev=staff.groupby("team")[role].shift(1)
    staff[f"{role}_changed"]=(staff[role].notna() & prev.notna() & staff[role].ne(prev)).astype(int)
staff["coordinator_changes"] = staff["offensive_coordinator_changed"] + staff["defensive_coordinator_changed"]
staff["major_staff_changes"] = staff["head_coach_changed"] + staff["coordinator_changes"]
staff.to_csv(OUT,index=False)

status={
    "team_seasons":len(staff),
    "head_coach_filled":int(staff.head_coach.notna().sum()),
    "oc_filled":int(staff.offensive_coordinator.notna().sum()),
    "dc_filled":int(staff.defensive_coordinator.notna().sum()),
    "both_coordinators_filled":int((staff.offensive_coordinator.notna() & staff.defensive_coordinator.notna()).sum()),
    "failures":failures[:100],
    "output":str(OUT),
}
STATUS.write_text(json.dumps(status,indent=2))
print(json.dumps(status,indent=2))
