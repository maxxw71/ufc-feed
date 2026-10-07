#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
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
VERIFIED_CARDS = ROOT / "dwcs/data/season10_verified_cards.csv"

S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 Appwiza-DWCS-Prospective/1.1",
    "Accept-Language": "en-US,en;q=0.9",
})


def norm(s):
    x = str(s or "").lower().replace("’", "'").replace("-", " ")
    x = re.sub(r"\b(jr|sr|ii|iii|iv)\b", " ", x)
    x = re.sub(r"[^a-z0-9]+", " ", x)
    return re.sub(r"\s+", " ", x).strip()


def pair_key(a, b):
    return tuple(sorted((norm(a), norm(b))))


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
    return int(round(statistics.median(vals)))


def next_week():
    p = ROOT / "dwcs/data/season10_2026_master.csv"
    if not p.exists():
        return 10
    d = pd.read_csv(p, low_memory=False)
    if "week" not in d:
        return 10
    status = d["status"].astype(str) if "status" in d else pd.Series("", index=d.index)
    w = pd.to_numeric(d.loc[status.eq("completed"), "week"], errors="coerce")
    return int(w.max()) + 1 if w.notna().any() else 10


def verified_card(week):
    if not VERIFIED_CARDS.exists():
        raise SystemExit("Verified Season 10 card registry missing")
    d = pd.read_csv(VERIFIED_CARDS, low_memory=False)
    d = d[
        pd.to_numeric(d["season"], errors="coerce").eq(10)
        & pd.to_numeric(d["week"], errors="coerce").eq(week)
        & d["verification_status"].astype(str).str.startswith("verified")
    ].copy()
    if d.empty:
        return d
    dates = d["event_date"].dropna().astype(str).unique().tolist()
    if len(dates) != 1:
        raise SystemExit(f"Verified card has inconsistent dates for week {week}: {dates}")
    return d


def discover_event_candidates(week):
    queries = [
        f"DWCS Week {week} 2026",
        f"Dana White's Contender Series Week {week} 2026",
        f"Contender Series Season 10 Week {week} 2026",
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
            if not href.startswith("/events/") or ("dwcs" not in low and "contender" not in low):
                continue
            score = 0
            score += 6 if str(week) in txt else 0
            score += 8 if "2026" in txt else 0
            score += 2 if "week" in low else 0
            candidates[href] = max(score, candidates.get(href, -1))
    return [BASE + h for h in sorted(candidates, key=lambda h: candidates[h], reverse=True)]


def parse_event(url):
    soup = BeautifulSoup(get(url).text, "html.parser")
    title = " ".join((soup.find("h1") or soup.title).get_text(" ", strip=True).split())
    table = soup.select_one("table.odds-table-responsive-header")
    if table is None:
        return title, []

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
                b, brow = x, trs[j]
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
    return title, out


def validate_bfo_event(verified, candidates):
    expected = {pair_key(x.fighter_a, x.fighter_b) for _, x in verified.iterrows()}
    needed = max(1, math.ceil(0.60 * len(expected)))
    audit = []
    for url in candidates:
        try:
            title, fights = parse_event(url)
        except Exception as exc:
            audit.append({"event_url": url, "accepted": False, "reason": repr(exc), "overlap": 0})
            continue
        observed = {pair_key(x["fighter_a"], x["fighter_b"]) for x in fights}
        overlap = len(expected & observed)
        accepted = overlap >= needed
        audit.append({
            "event_url": url,
            "event_title": title,
            "accepted": accepted,
            "overlap": overlap,
            "verified_card_fights": len(expected),
            "required_overlap": needed,
        })
        if accepted:
            return url, title, fights, audit
    return "", "", [], audit


def dob_map():
    out = {}
    p = ROOT / "dwcs/research/age_enrichment/fighter_dobs.csv"
    if p.exists():
        d = pd.read_csv(p, low_memory=False)
        d["dob_dt"] = pd.to_datetime(d["dob"], errors="coerce")
        out.update({norm(x.fighter): x.dob_dt for _, x in d[d.dob_dt.notna()].iterrows()})
    if VERIFIED_CARDS.exists():
        c = pd.read_csv(VERIFIED_CARDS, low_memory=False)
        for _, x in c.iterrows():
            for side in ("a", "b"):
                dob = pd.to_datetime(x.get(f"{side}_dob"), errors="coerce")
                if pd.notna(dob):
                    out[norm(x[f"fighter_{side}"])] = dob
    return out


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
        "signal_status": "PRE_EVENT_SHADOW" if odds is not None else "PRE_EVENT_SHADOW_AWAITING_PRICE",
        "eligible_for_tuning": False,
        "result_known_at_snapshot": False,
    })


def main():
    now = datetime.now(timezone.utc)
    week = next_week()
    verified = verified_card(week)

    status = {
        "snapshot_utc": now.isoformat(),
        "season": 10,
        "target_week": week,
        "rules_hash": current_rules_hash(),
        "rules_frozen": True,
        "eligible_for_tuning": False,
        "result_fields_read": False,
    }

    if verified.empty:
        status["status"] = "verified_card_not_available_yet"
        (OUT / "latest_status.json").write_text(json.dumps(status, indent=2) + "\n")
        print(json.dumps(status, indent=2))
        return

    event_date = str(verified.iloc[0]["event_date"])
    candidates = discover_event_candidates(week)
    event_url, event_title, market_fights, audit = validate_bfo_event(verified, candidates)

    pd.DataFrame(audit).to_csv(OUT / "bfo_event_identity_audit.csv", index=False)

    market_map = {pair_key(x["fighter_a"], x["fighter_b"]): x for x in market_fights}
    dm = dob_map()
    signals = []
    card_rows = []

    title = event_title or f"DWCS Season 10 Week {week}"
    for _, vrow in verified.iterrows():
        a, b = str(vrow.fighter_a), str(vrow.fighter_b)
        key = pair_key(a, b)
        market = market_map.get(key)

        ao = bo = None
        matchup_id = "verified-" + hashlib.sha1("|".join(key).encode()).hexdigest()[:12]
        if market is not None:
            matchup_id = str(market["matchup_id"])
            if norm(market["fighter_a"]) == norm(a):
                ao, bo = market["a_snapshot_odds"], market["b_snapshot_odds"]
            else:
                ao, bo = market["b_snapshot_odds"], market["a_snapshot_odds"]

        da, db = dm.get(norm(a)), dm.get(norm(b))
        gap = age_gap_years(da, db) if da is not None and db is not None else None
        younger = older = None
        younger_odds = None
        if gap is not None:
            if da > db:
                younger, older, younger_odds = a, b, ao
            elif db > da:
                younger, older, younger_odds = b, a, bo

        base = {
            "snapshot_utc": now.isoformat(),
            "season": 10,
            "week": week,
            "event_date": event_date,
            "event_title": title,
            "event_url": event_url,
            "matchup_id": matchup_id,
            "rules_hash": status["rules_hash"],
        }
        card_rows.append({
            **base,
            "fighter_a": a,
            "fighter_b": b,
            "a_snapshot_consensus_odds": ao,
            "b_snapshot_consensus_odds": bo,
            "age_gap_years": gap,
            "younger_fighter": younger or "",
            "verified_card_source_url": vrow.card_source_url,
            "bfo_market_identity_validated": bool(event_url),
        })

        if younger is None:
            continue

        # Global risk gate: if a price exists, reject four-digit favorites.
        price_allowed = younger_odds is None or younger_odds >= -999
        if gap >= 5 and price_allowed:
            emit_signal(
                signals, base, "DW-A01", "DWCS Age Edge",
                "strong" if gap >= 6 else "base",
                younger, older, younger_odds, gap
            )

        # Price-sensitive value rule cannot qualify until a verified current snapshot exists.
        if gap >= 3 and younger_odds is not None and 100 <= younger_odds <= 250:
            emit_signal(
                signals, base, "DW-V01", "DWCS Young Value Underdog",
                "provisional_market", younger, older, younger_odds, gap
            )

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
            ["snapshot_utc", "method_key", "event_date", "matchup_id", "pick"],
            keep="first",
        )
    led.to_csv(ledger_path, index=False)

    status.update({
        "status": "snapshot_complete",
        "verified_event_date": event_date,
        "verified_matchups": len(verified),
        "bfo_event_url": event_url,
        "bfo_event_title": event_title,
        "bfo_event_identity_validated": bool(event_url),
        "market_matchups_matched": len(market_map),
        "signals_found": len(signals),
        "age_edge_signals": sum(x["method_key"] == "DW-A01" for x in signals),
        "young_value_dog_signals": sum(x["method_key"] == "DW-V01" for x in signals),
        "snapshot_market_is_not_retroactive_closing_odds": True,
        "stale_cross_season_event_pages_rejected_by_card_overlap": True,
    })
    (OUT / "latest_status.json").write_text(json.dumps(status, indent=2) + "\n")
    print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
