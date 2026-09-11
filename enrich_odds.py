from __future__ import annotations

import json
import math
import os
import re
import unicodedata
from pathlib import Path

import requests

PATH = Path("upcoming.json")
ODDS_URL = "https://api.the-odds-api.com/v4/sports/mma_mixed_martial_arts/odds"


def norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().replace("’", "'")
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\.?\b", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def implied_american(odds: float) -> float:
    o = float(odds)
    return (-o) / ((-o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def fair_american(p: float) -> float:
    p = min(max(float(p), 1e-6), 1 - 1e-6)
    return -100 * p / (1 - p) if p >= 0.5 else 100 * (1 - p) / p


def median(vals):
    vals = sorted(float(x) for x in vals)
    if not vals:
        return None
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2


def pair_key(a: str, b: str):
    return tuple(sorted((norm(a), norm(b))))


def main():
    data = json.loads(PATH.read_text(encoding="utf-8"))
    api_key = os.environ.get("THE_ODDS_API_KEY", "").strip()

    if not api_key:
        data["odds"] = {
            "provider": "The Odds API",
            "status": "disabled_no_api_key",
        }
        PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("odds enrichment skipped: THE_ODDS_API_KEY is not configured")
        return

    r = requests.get(
        ODDS_URL,
        params={
            "apiKey": api_key,
            "regions": "us",
            "markets": "h2h",
            "oddsFormat": "american",
            "dateFormat": "iso",
        },
        timeout=30,
        headers={"User-Agent": "ufc-feed/odds-1.0"},
    )
    r.raise_for_status()
    odds_events = r.json()

    index = {}
    for oe in odds_events:
        home = oe.get("home_team") or ""
        away = oe.get("away_team") or ""
        if home and away:
            index.setdefault(pair_key(home, away), []).append(oe)

    matched = 0
    bookmaker_quotes = 0

    for event in data.get("events") or []:
        for bout in event.get("bouts") or []:
            a = bout.get("fighter_a") or ""
            b = bout.get("fighter_b") or ""
            candidates = index.get(pair_key(a, b), [])
            if not candidates:
                bout["market"] = {"status": "no_odds"}
                continue

            # Prefer the odds event with the most bookmaker quotes.
            oe = max(candidates, key=lambda x: len(x.get("bookmakers") or []))
            quotes = []

            for book in oe.get("bookmakers") or []:
                title = book.get("title") or book.get("key") or "Unknown"
                for market in book.get("markets") or []:
                    if market.get("key") != "h2h":
                        continue
                    outcomes = market.get("outcomes") or []
                    if len(outcomes) != 2:
                        continue
                    by_name = {norm(x.get("name")): x for x in outcomes}
                    qa = by_name.get(norm(a))
                    qb = by_name.get(norm(b))
                    if not qa or not qb:
                        continue
                    oa = float(qa["price"])
                    ob = float(qb["price"])
                    ia, ib = implied_american(oa), implied_american(ob)
                    total = ia + ib
                    if total <= 0:
                        continue
                    nva, nvb = ia / total, ib / total
                    quotes.append({
                        "bookmaker": title,
                        "odds_a": oa,
                        "odds_b": ob,
                        "no_vig_a": nva,
                        "no_vig_b": nvb,
                        "last_update": market.get("last_update") or book.get("last_update"),
                    })

            if not quotes:
                bout["market"] = {"status": "no_usable_quotes"}
                continue

            matched += 1
            bookmaker_quotes += len(quotes)
            median_a = median([q["no_vig_a"] for q in quotes])
            median_b = 1.0 - median_a

            # Best listed line from the user's perspective = highest payout.
            # For negative American odds, -120 is better than -150; for positive, +150 is better than +120.
            best_a = max(quotes, key=lambda q: q["odds_a"])
            best_b = max(quotes, key=lambda q: q["odds_b"])

            bout["market"] = {
                "status": "ok",
                "provider": "The Odds API",
                "commence_time": oe.get("commence_time"),
                "bookmakers": len(quotes),
                "consensus_no_vig_a": median_a,
                "consensus_no_vig_b": median_b,
                "consensus_fair_odds_a": fair_american(median_a),
                "consensus_fair_odds_b": fair_american(median_b),
                "best_odds_a": best_a["odds_a"],
                "best_book_a": best_a["bookmaker"],
                "best_odds_b": best_b["odds_b"],
                "best_book_b": best_b["bookmaker"],
                "quotes": quotes,
            }

    data["odds"] = {
        "provider": "The Odds API",
        "status": "ok",
        "region": "us",
        "market": "h2h",
        "mma_events_returned": len(odds_events),
        "ufc_bouts_matched": matched,
        "bookmaker_quotes": bookmaker_quotes,
        "requests_remaining": r.headers.get("x-requests-remaining"),
        "requests_used": r.headers.get("x-requests-used"),
    }

    PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"odds enrichment: {matched} UFC bouts matched / {bookmaker_quotes} bookmaker quotes")
    print("requests remaining:", r.headers.get("x-requests-remaining"))


if __name__ == "__main__":
    main()
