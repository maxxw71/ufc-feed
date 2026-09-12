from __future__ import annotations

import io
import os
import re
import zipfile
from pathlib import Path

import duckdb
import pandas as pd
import requests
from bs4 import BeautifulSoup

UA = "mma-hybrid-research/1.0"
KAGGLE = "https://www.kaggle.com/api/v1/datasets/download/leandroiber/mmastats"


def main():
    print("Downloading public MMA global dataset...", flush=True)
    r = requests.get(KAGGLE, timeout=180, headers={"User-Agent": UA}, allow_redirects=True)
    print("KAGGLE_STATUS", r.status_code, "BYTES", len(r.content), "TYPE", r.headers.get("content-type"), flush=True)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    print("ZIP_FILES", names[:50], flush=True)
    dbs = [n for n in names if n.lower().endswith(".duckdb")]
    if not dbs:
        raise RuntimeError("No duckdb file in Kaggle archive")
    db_path = Path("/tmp/pfl_probe.duckdb")
    with z.open(dbs[0]) as src, open(db_path, "wb") as dst:
        dst.write(src.read())

    con = duckdb.connect(str(db_path), read_only=True)
    print("TABLES", con.execute("show tables").fetchall(), flush=True)
    print("FIGHT_COLS", con.execute("describe fights_career_longitudinal").fetchdf().to_dict("records"), flush=True)
    print("FIGHTER_COLS", con.execute("describe fighters_master").fetchdf().to_dict("records"), flush=True)
    print("ORG_MATCHES", con.execute("""
        select organization, count(*) n, min(event_date) min_date, max(event_date) max_date
        from fights_career_longitudinal
        where lower(organization) like '%pfl%'
           or lower(organization) like '%professional fighters%'
           or lower(organization) like '%world series of fighting%'
           or lower(organization) = 'wsof'
        group by 1 order by min_date
    """).fetchdf().to_dict("records"), flush=True)
    print("PFL_SAMPLE", con.execute("""
        select * from fights_career_longitudinal
        where lower(organization) like '%pfl%'
        order by event_date desc limit 5
    """).fetchdf().to_dict("records"), flush=True)

    # Discover likely PFL events from BestFightOdds archive search.
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0"})
    for q in ["PFL 2018", "PFL 2019", "PFL 2021", "PFL 2022", "PFL 2023", "PFL 2024", "Professional Fighters League 2025"]:
        u = "https://www.bestfightodds.com/search"
        rr = s.get(u, params={"query": q}, timeout=45)
        print("BFO_SEARCH", q, rr.status_code, len(rr.text), rr.url, flush=True)
        soup = BeautifulSoup(rr.text, "lxml")
        links = []
        for a in soup.select('a[href*="/events/"]'):
            href = a.get("href") or ""
            txt = " ".join(a.get_text(" ", strip=True).split())
            if href and href not in [x[0] for x in links]:
                links.append((href, txt))
        print("EVENT_LINKS", q, links[:30], flush=True)

    # Inspect one known event page structure.
    ev = "https://www.bestfightodds.com/events/pfl-2021-5-2175"
    rr = s.get(ev, timeout=45)
    print("BFO_EVENT", rr.status_code, len(rr.text), flush=True)
    soup = BeautifulSoup(rr.text, "lxml")
    tables = soup.find_all("table")
    print("TABLE_COUNT", len(tables), flush=True)
    for i,t in enumerate(tables[:8]):
        heads = [" ".join(x.get_text(" ", strip=True).split()) for x in t.find_all("th")]
        rows = []
        for tr in t.find_all("tr")[:4]:
            cells = [" ".join(x.get_text(" ", strip=True).split()) for x in tr.find_all(["th","td"])]
            if cells: rows.append(cells)
        print("TABLE", i, "HEADS", heads, "ROWS", rows, flush=True)

if __name__ == "__main__":
    main()
