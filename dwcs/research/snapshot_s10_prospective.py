#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import statistics
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

ROOT = Path(".")
OUT = ROOT / "dwcs/research/season10_prospective_snapshots"
OUT.mkdir(parents=True, exist_ok=True)
BASE = "https://www.bestfightodds.com"

S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 Appwiza-DWCS-Prospective/1.0",
    "Accept-Language": "en-US,en;q=0.9",
})


def norm(s):
    x = str(s or "").lower().replace("’", "'").replace("-", " ")
    x = re.sub(r"\b(jr|sr|ii|iii|iv)\b", " ", x)
    x = re.sub(r"[^a-z0-9]+", " ", x)
    return re.sub(r"\s+", " ", x).strip()


def get(url):
    r = S.get(url, timeout=30)
    r.raise_for_status()
    return r


def odds_tokens(txt):
    return [int(x) for x in re.findall(r"(?<!\d)([+-]\d{2,4})(?!\d)", txt)]


def representative_odds(txt):
    vals = [x for x in odds_tokens(txt) if 100 <= abs(x) <= 9999]
    if not vals:
        return None
    # Event tables contain multiple books. Median is a stable snapshot consensus,
    # not a historical closing-price replacement.
    return int(round(statistics.median(vals)))


def next_week():
    p = ROOT / "dwcs/data/season10_2026_master.csv"
    if not p.exists():
        return 10
    d = pd.read_csv(p, low_memory=False)
    if "week" not in d:
        return 10
    w = pd.to_numeric(d.loc[d.get("status", "").astype(str).eq("completed"), "week"], errors="coerce")
    return int(w.max()) + 1 if w.notna().any() else 10


def discover_event(week):
    queries = [
        f"DWCS Week {week} 2026",
        f"Dana White's Contender Series Week {week} 2026",
        f"Contender Series Season 10 Week {week}",
    ]
    candidates = {}
    for q in queries:
        u = BASE + "/search?query=" + urllib.parse.quote_plus(q)
        try:
            soup = BeautifulSoup(get(u).text, "html.parser")
        except Exception:
            continue
        for a in soup.find_all("a", href=True):
            href = a["href"]
            txt = " ".join(a.get_text(" ", strip=True).split())
            low = txt.lower()
            if href.startswith("/events/") and ("dwcs" in low or "contender" in low):
                score = 0
                score += 5 if str(week) in txt else 0
                score += 4 if "2026" in txt else 0
                score += 2 if "week" in low else 0
                candidates[href] = max(score, candidates.get(href, -1))
    if not candidates:
        return None
    href = sorted(candidates, key=lambda h: candidates[h], reverse=True)[0]
    return BASE + href


def parse_event(url):
    soup = BeautifulSoup(get(url).text, "html.parser")
    title = " ".join((soup.find("h1") or soup.title).get_text(" ", strip=True).split())
    page_text = " ".join(soup.get_text(" ", strip=True).split())
    dm = re.search(
        r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|"
        r"Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}(?:st|nd|rd|th)?[,]?\s+2026",
        page_text,
        re.I,
    )
    event_date = ""
    if dm:
        clean = re.sub(r"(\d)(st|nd|rd|th)", r"\1", dm.group(0), flags=re.I)
        dt = pd.to_datetime(clean, errors="coerce")
        if pd.notna(dt):
            event_date = dt.date().isoformat()

    table = soup.select_one("table.odds-table-responsive-header")
    if table is None:
        return title, event_date, []

    trs = table.select("tbody tr")
    out = []
    i = 0
    while i < len(trs):
        tr = trs[i]
        rid = tr.get("id", "")
        if not rid.startswith("mu-"):
            i += 1
            continue
        a = tr.find("a", href=re.compile(r"^/fighters/"))
        b = None
        brow = None
        j = i + 1
        while j < len(trs):
            if trs[j].get("id", "").startswith("mu-"):
                break
            x = trs[j].find("a", href=re.compile(r"^/fighters/"))
            if x:
                b = x
                brow = trs[j]
                break
            j += 1
        if a and b and brow is not None:
            out.append({
                "matchup_id": rid[3:],
                "fighter_a": " ".join(a.get_text(" ", strip=True).split()),
                "fighter_b": " ".join(b.get_text(" ", strip=True).split()),
                "a_snapshot_odds": representative_odds(" ".join(tr.get_text(" ", strip=True).split())),
                "b_snapshot_odds": representative_odds(" ".join(brow.get_text(" ", strip=True).split())),
            })
        i += 1
    return title, event_date, out


def dob_map():
    p = ROOT / "dwcs/research/age_enrichment/fighter_dobs.csv"
    d = pd.read_csv(p, low_memory=False)
    d["dob_dt"] = pd.to_datetime(d["dob"], errors="coerce")
    return {norm(x.fighter): x.dob_dt for _, x in d[d.dob_dt.notna()].iterrows()}


def age_gap_years(d1, d2):
    return abs((d1 - d2).days) / 365.2425


def current_rules_hash():
    p = ROOT / "dwcs/data/shadow_method_registry.csv"
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else ""


def emit_signal(rows, base, method_key, method_name, tier, pick, opponent, odds, age_gap):
    rows.append({
        **base,
        "method_key": method_key,
        "method_name": method_name,
        "tier": tier,
        "pick": pick,
        "opponent": opponent,
        "snapshot_consensus_odds": odds,
        "age_gap_years": age_gap,
        "signal_status": "PRE_EVENT_SHADOW",
        "eligible_for_tuning": False,
        "result_known_at_snapshot": False,
    })


def main():
    now = datetime.now(timezone.utc)
    week = next_week()
    url = discover_event(week)

    status = {
        "snapshot_utc": now.isoformat(),
        "season": 10,
        "target_week": week,
        "event_url": url or "",
        "rules_hash": current_rules_hash(),
        "rules_frozen": True,
        "eligible_for_tuning": False,
        "result_fields_read": False,
    }

    if not url:
        status["status"] = "event_not_discovered_yet"
        (OUT / "latest_status.json").write_text(json.dumps(status, indent=2) + "\n")
        print(json.dumps(status, indent=2))
        return

    title, event_date, fights = parse_event(url)
    status.update({"event_title": title, "event_date": event_date, "matchups_found": len(fights)})
    dm = dob_map()
    signals = []
    card_rows = []

    for f in fights:
        a, b = f["fighter_a"], f["fighter_b"]
        da, db = dm.get(norm(a)), dm.get(norm(b))
        gap = age_gap_years(da, db) if da is not None and db is not None else None
        younger = older = None
        younger_odds = None
        if gap is not None:
            if da > db:
                younger, older, younger_odds = a, b, f["a_snapshot_odds"]
            elif db > da:
                younger, older, younger_odds = b, a, f["b_snapshot_odds"]

        base = {
            "snapshot_utc": now.isoformat(),
            "season": 10,
            "week": week,
            "event_date": event_date,
            "event_title": title,
            "event_url": url,
            "matchup_id": f["matchup_id"],
            "rules_hash": status["rules_hash"],
        }
        card_rows.append({
            **base,
            "fighter_a": a,
            "fighter_b": b,
            "a_snapshot_consensus_odds": f["a_snapshot_odds"],
            "b_snapshot_consensus_odds": f["b_snapshot_odds"],
            "age_gap_years": gap,
            "younger_fighter": younger or "",
        })

        if younger is None:
            continue

        # Global risk gate: four-digit favorites are never shadow qualifiers.
        price_allowed = younger_odds is None or younger_odds >= -999

        if gap >= 5 and price_allowed:
            emit_signal(signals, base, "DW-A01", "DWCS Age Edge", "strong" if gap >= 6 else "base",
                        younger, older, younger_odds, gap)

        # Historical rule was closing +100..+250. A pre-event snapshot can only
        # be a provisional qualifier until the final pre-fight snapshot is known.
        if gap >= 3 and younger_odds is not None and 100 <= younger_odds <= 250:
            emit_signal(signals, base, "DW-V01", "DWCS Young Value Underdog",
                        "provisional_market", younger, older, younger_odds, gap)

    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    pd.DataFrame(card_rows).to_csv(OUT / f"card_snapshot_{stamp}.csv", index=False)
    pd.DataFrame(signals).to_csv(OUT / f"signals_{stamp}.csv", index=False)

    ledger_path = OUT / "prospective_signal_ledger.csv"
    new = pd.DataFrame(signals)
    if ledger_path.exists():
        old = pd.read_csv(ledger_path, low_memory=False)
        led = pd.concat([old, new], ignore_index=True)
    else:
        led = new
    if len(led):
        led = led.drop_duplicates(
            ["snapshot_utc", "method_key", "event_url", "matchup_id", "pick"],
            keep="first",
        )
    led.to_csv(ledger_path, index=False)

    status.update({
        "status": "snapshot_complete",
        "signals_found": len(signals),
        "age_edge_signals": sum(x["method_key"] == "DW-A01" for x in signals),
        "young_value_dog_signals": sum(x["method_key"] == "DW-V01" for x in signals),
        "snapshot_market_is_not_retroactive_closing_odds": True,
    })
    (OUT / "latest_status.json").write_text(json.dumps(status, indent=2) + "\n")
    print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
