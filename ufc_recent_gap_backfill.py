from __future__ import annotations

import io
import json
import math
import re
import statistics
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from rapidfuzz.fuzz import WRatio, ratio

OUT = Path("ufc_recent_gap_backfill")
OUT.mkdir(exist_ok=True)
BFO = "https://www.bestfightodds.com"
KAGGLE = "https://www.kaggle.com/api/v1/datasets/download/leandroiber/mmastats"
STAKE = 50.0
MARKET_MIN = 0.70
GAP_MIN = 3.0
STRONG_GAP = 4.0
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"


def norm_name(s):
    s = str(s or "").lower().replace("’", "'").replace("-", " ")
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def clean_event_name(s):
    s = " ".join(str(s or "").split())
    s = re.sub(r"^Ultimate Fighting Championship\s*-\s*", "", s, flags=re.I)
    s = re.sub(r"^UFC\s*-\s*", "UFC ", s, flags=re.I)
    return s.strip()


def american(cell):
    if cell is None:
        return None
    m = re.search(r"(?<!\d)([+-]\d{2,5})(?!\d)", str(cell).replace(",", ""))
    if not m:
        return None
    try:
        v = int(m.group(1))
        return v if v else None
    except Exception:
        return None


def implied(o):
    o = float(o)
    return (-o) / ((-o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def american_profit(stake, o):
    o = float(o)
    return stake * (100.0 / -o) if o < 0 else stake * (o / 100.0)


def exact_age(dob, event_date):
    return (pd.Timestamp(event_date) - pd.Timestamp(dob)).days / 365.2425


def robust_get(session, url, *, params=None, timeout=60, tries=4):
    last = None
    for attempt in range(tries):
        try:
            r = session.get(url, params=params, timeout=timeout)
            last = r
            if r.status_code == 200:
                return r
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(1.5 * (attempt + 1))
                continue
            return r
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    if last is not None:
        return last
    raise RuntimeError(f"GET failed: {url}")


def fetch_db(session):
    for i in range(4):
        r = robust_get(session, KAGGLE, timeout=180, tries=2)
        if r.status_code == 200 and r.content[:2] == b"PK":
            z = zipfile.ZipFile(io.BytesIO(r.content))
            dbs = [n for n in z.namelist() if n.lower().endswith(".duckdb")]
            if not dbs:
                raise RuntimeError("Kaggle archive contained no DuckDB")
            p = Path("/tmp/ufc_recent.duckdb")
            with z.open(dbs[0]) as src, open(p, "wb") as dst:
                dst.write(src.read())
            return p
        time.sleep(2 * (i + 1))
    raise RuntimeError("Could not download Kaggle MMA DuckDB")


def load_ufc(db_path):
    con = duckdb.connect(str(db_path), read_only=True)
    fights = con.execute("""
        select *
        from fights_career_longitudinal
        where lower(organization) = 'ufc'
          and event_date >= DATE '2024-01-01'
          and event_date <= CURRENT_DATE
          and fighter_1 is not null and fighter_2 is not null
          and winner_side is not null
    """).fetchdf()
    fighters = con.execute("select fighter_name, dob from fighters_master where dob is not null").fetchdf()
    con.close()

    fights["event_date"] = pd.to_datetime(fights["event_date"], errors="coerce")
    fighters["dob"] = pd.to_datetime(fighters["dob"], errors="coerce")
    dob = {}
    for _, r in fighters.iterrows():
        dob.setdefault(norm_name(r["fighter_name"]), pd.Timestamp(r["dob"]))

    fights["f1_norm"] = fights["fighter_1"].map(norm_name)
    fights["f2_norm"] = fights["fighter_2"].map(norm_name)
    fights["pair"] = fights.apply(lambda r: tuple(sorted((r["f1_norm"], r["f2_norm"]))), axis=1)
    return fights, dob


def search_event(session, event_name, first_pair):
    clean = clean_event_name(event_name)
    queries = [clean]
    # Search a shorter title if Sherdog prepended extra organization text.
    if ":" in clean:
        queries.append(clean.split(":", 1)[0])
    if first_pair:
        queries.append(f"{first_pair[0]} {first_pair[1]}")

    candidates = {}
    for q in queries:
        r = robust_get(session, BFO + "/search", params={"query": q}, timeout=45)
        if r.status_code != 200:
            continue
        soup = BeautifulSoup(r.text, "lxml")
        for a in soup.select('a[href*="/events/"]'):
            href = a.get("href") or ""
            txt = " ".join(a.get_text(" ", strip=True).split())
            if not href.startswith("/events/"):
                continue
            low = txt.lower()
            if "ufc" not in low and not href.startswith("/events/ufc-"):
                continue
            score = max(WRatio(clean.lower(), txt.lower()), WRatio(norm_name(clean), norm_name(txt)))
            candidates[href] = max(candidates.get(href, (0, txt))[0], score), txt

    if not candidates:
        return None, None, 0
    href, (score, txt) = max(candidates.items(), key=lambda kv: kv[1][0])
    return href, txt, score


def parse_event_page(session, href, label):
    r = robust_get(session, BFO + href, timeout=60)
    if r.status_code != 200:
        return []
    soup = BeautifulSoup(r.text, "lxml")
    tables = soup.find_all("table")
    target = None
    header = None
    for t in tables:
        for tr in t.find_all("tr"):
            cells = [" ".join(x.get_text(" ", strip=True).split()) for x in tr.find_all(["th", "td"])]
            if any(c in {"DraftKings", "FanDuel", "BetMGM", "BetRivers", "Caesars", "Bovada", "BetOnline.ag"} for c in cells):
                target = t
                header = cells
                break
        if target is not None:
            break
    if target is None or not header:
        return []

    blacklist = {"", "Props", "Polymarket $20 Bonus i", "Kalshi Up to $500 i"}
    book_idx = {i:h for i,h in enumerate(header) if h not in blacklist and i > 0}

    fighter_rows = []
    for tr in target.find_all("tr"):
        cells = [" ".join(x.get_text(" ", strip=True).split()) for x in tr.find_all(["th", "td"])]
        if not cells:
            continue
        name = re.sub(r"^\d+\s+", "", cells[0]).strip()
        nn = norm_name(name)
        if len(nn) < 3:
            continue
        if any(x in nn for x in ["over ", "under ", "fight goes", "fight doesn", "wins by", "any other", "round ", "draw"]):
            continue
        odds = {}
        for idx, book in book_idx.items():
            if idx < len(cells):
                o = american(cells[idx])
                if o is not None:
                    odds[book] = o
        if odds:
            fighter_rows.append((name, odds))

    bouts = []
    i = 0
    while i + 1 < len(fighter_rows):
        a, oa = fighter_rows[i]
        b, ob = fighter_rows[i+1]
        common = sorted(set(oa) & set(ob))
        quotes = []
        for book in common:
            ia, ib = implied(oa[book]), implied(ob[book])
            s = ia + ib
            if s > 0:
                quotes.append({
                    "book": book,
                    "odds_a": oa[book],
                    "odds_b": ob[book],
                    "no_vig_a": ia/s,
                    "no_vig_b": ib/s,
                })
        if quotes and norm_name(a) != norm_name(b):
            bouts.append({"fighter_a":a,"fighter_b":b,"quotes":quotes,"source_href":href,"source_event":label})
            i += 2
        else:
            i += 1
    return bouts


def match_bout(bout, event_fights):
    a, b = norm_name(bout["fighter_a"]), norm_name(bout["fighter_b"])
    pair = tuple(sorted((a,b)))
    ex = event_fights[event_fights["pair"] == pair]
    if len(ex) == 1:
        return ex.iloc[0], 100.0
    best = None
    best_score = 0.0
    for _, r in event_fights.iterrows():
        d = (ratio(a, r["f1_norm"]) + ratio(b, r["f2_norm"])) / 2
        rev = (ratio(a, r["f2_norm"]) + ratio(b, r["f1_norm"])) / 2
        score = max(d, rev)
        if score > best_score:
            best_score = score
            best = r
    if best is not None and best_score >= 88:
        return best, best_score
    return None, best_score


def best_line(quotes, side):
    key = "odds_a" if side == "a" else "odds_b"
    q = max(quotes, key=lambda x: x[key])
    return q[key], q["book"]


def bootstrap_ci(profits, n_boot=5000, seed=42):
    arr = np.asarray(profits, dtype=float)
    if len(arr) < 10:
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    vals = []
    n = len(arr)
    for _ in range(n_boot):
        s = rng.choice(arr, size=n, replace=True)
        vals.append(s.sum()/(n*STAKE))
    return float(np.quantile(vals,.025)), float(np.quantile(vals,.975))


def metrics(df):
    if df.empty:
        return {"bets":0,"wins":0,"win_rate":np.nan,"profit":0.0,"roi":np.nan,"ci_low":np.nan,"ci_high":np.nan}
    profit = float(df["profit"].sum())
    lo, hi = bootstrap_ci(df["profit"].to_numpy())
    return {
        "bets": int(len(df)),
        "wins": int(df["won"].sum()),
        "win_rate": float(df["won"].mean()),
        "profit": profit,
        "roi": profit/(len(df)*STAKE),
        "ci_low": lo,
        "ci_high": hi,
    }


def summarize(df):
    rows = []
    for yr in sorted(df["year"].unique()):
        y = df[df["year"] == yr]
        rows.append({"segment":str(int(yr)), **metrics(y)})
    rows.append({"segment":"2024-current", **metrics(df)})
    return pd.DataFrame(rows)


def main():
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Language":"en-US,en;q=0.9"})

    db = fetch_db(session)
    fights, dob_map = load_ufc(db)
    events = fights[["event_name","event_date"]].drop_duplicates().sort_values("event_date")
    print("COMPLETED_UFC_EVENTS", len(events), "FIGHTS", len(fights), flush=True)

    matched = []
    event_log = []

    for idx, ev in events.reset_index(drop=True).iterrows():
        name = ev["event_name"]
        ef = fights[fights["event_name"] == name].copy()
        first_pair = None
        if not ef.empty:
            first_pair = (str(ef.iloc[0]["fighter_1"]), str(ef.iloc[0]["fighter_2"]))
        href, bfo_label, score = search_event(session, name, first_pair)
        if not href:
            event_log.append({"event_name":name,"event_date":str(ev["event_date"].date()),"status":"no_bfo_event","score":score})
            continue
        bouts = parse_event_page(session, href, bfo_label)
        if not bouts:
            event_log.append({"event_name":name,"event_date":str(ev["event_date"].date()),"status":"no_odds_rows","score":score,"href":href,"bfo_label":bfo_label})
            continue

        event_matches = 0
        for b in bouts:
            r, mscore = match_bout(b, ef)
            if r is None:
                continue
            d1 = dob_map.get(r["f1_norm"])
            d2 = dob_map.get(r["f2_norm"])
            if d1 is None or d2 is None or pd.isna(d1) or pd.isna(d2):
                continue

            age1 = exact_age(d1, r["event_date"])
            age2 = exact_age(d2, r["event_date"])
            pa = statistics.median(q["no_vig_a"] for q in b["quotes"])
            pb = 1.0 - pa

            ba, bb = norm_name(b["fighter_a"]), norm_name(b["fighter_b"])
            direct = ratio(ba, r["f1_norm"]) + ratio(bb, r["f2_norm"])
            rev = ratio(ba, r["f2_norm"]) + ratio(bb, r["f1_norm"])
            bfo_a_is_f1 = direct >= rev

            if pa >= pb:
                fav_bfo_side = "a"; market_prob = pa
            else:
                fav_bfo_side = "b"; market_prob = pb

            fav_is_f1 = (fav_bfo_side == "a") if bfo_a_is_f1 else (fav_bfo_side == "b")
            fav_age = age1 if fav_is_f1 else age2
            dog_age = age2 if fav_is_f1 else age1
            younger_adv = dog_age - fav_age
            won = (int(r["winner_side"]) == 1) if fav_is_f1 else (int(r["winner_side"]) == 2)

            best_odds, best_book = best_line(b["quotes"], fav_bfo_side)
            profit = american_profit(STAKE, best_odds) if won else -STAKE

            matched.append({
                "event_date": pd.Timestamp(r["event_date"]),
                "year": int(pd.Timestamp(r["event_date"]).year),
                "event_name": r["event_name"],
                "fighter_1": r["fighter_1"],
                "fighter_2": r["fighter_2"],
                "favorite": r["fighter_1"] if fav_is_f1 else r["fighter_2"],
                "underdog": r["fighter_2"] if fav_is_f1 else r["fighter_1"],
                "favorite_age": fav_age,
                "underdog_age": dog_age,
                "younger_advantage": younger_adv,
                "market_prob": market_prob,
                "best_odds": best_odds,
                "best_book": best_book,
                "book_count": len(b["quotes"]),
                "won": bool(won),
                "profit": profit,
                "event_match_score": score,
                "fight_match_score": mscore,
                "bfo_event": bfo_label,
                "bfo_href": href,
            })
            event_matches += 1

        event_log.append({"event_name":name,"event_date":str(ev["event_date"].date()),"status":"ok","score":score,"href":href,"bfo_label":bfo_label,"matched_fights":event_matches,"bfo_bouts":len(bouts)})
        print(f"EVENT {idx+1}/{len(events)} {ev['event_date'].date()} {name}: {event_matches}/{len(ef)} matched", flush=True)

    df = pd.DataFrame(matched)
    if df.empty:
        raise RuntimeError("No recent UFC fights matched to BestFightOdds")
    df = df.drop_duplicates(subset=["event_date","fighter_1","fighter_2"]).sort_values("event_date").reset_index(drop=True)
    df.to_csv(OUT/"matched_recent_ufc_fights.csv", index=False)
    pd.DataFrame(event_log).to_csv(OUT/"event_match_log.csv", index=False)

    hybrid = df[(df["market_prob"] >= MARKET_MIN) & (df["younger_advantage"] >= GAP_MIN)].copy()
    strong = df[(df["market_prob"] >= MARKET_MIN) & (df["younger_advantage"] >= STRONG_GAP)].copy()
    hybrid.to_csv(OUT/"recent_hybrid_3yr.csv", index=False)
    strong.to_csv(OUT/"recent_hybrid_4yr.csv", index=False)

    summary = summarize(hybrid)
    summary.to_csv(OUT/"hybrid_3yr_summary.csv", index=False)
    strong_summary = summarize(strong)
    strong_summary.to_csv(OUT/"hybrid_4yr_summary.csv", index=False)

    # Market probability bands for the 3-year rule.
    bands = []
    h = hybrid.copy()
    h["market_band"] = pd.cut(h["market_prob"],[.70,.75,.80,.85,.90,.95,1.001],right=False,labels=["70-74.9%","75-79.9%","80-84.9%","85-89.9%","90-94.9%","95%+"])
    for band,g in h.groupby("market_band", observed=True):
        bands.append({"market_band":str(band), **metrics(g)})
    pd.DataFrame(bands).to_csv(OUT/"recent_market_bands.csv", index=False)

    lines = [
        "UFC 2024-CURRENT HYBRID GAP BACKFILL",
        "="*72,
        "",
        "Rule carried forward unchanged from the historical UFC analysis:",
        "  consensus no-vig favorite >=70%",
        "  AND favorite >=3 years younger",
        "  >=4 years younger = strong tier",
        "",
        f"Completed UFC fights in source: {len(fights)}",
        f"Recent fights successfully matched to BestFightOdds + both DOBs: {len(df)}",
        f"Date range matched: {df['event_date'].min().date()} to {df['event_date'].max().date()}",
        "",
        "3+ YEAR HYBRID RULE",
        "-"*72,
    ]
    for _,r in summary.iterrows():
        wr = "n/a" if pd.isna(r["win_rate"]) else f"{r['win_rate']*100:.2f}%"
        roi = "n/a" if pd.isna(r["roi"]) else f"{r['roi']*100:+.2f}%"
        ci = "n/a" if pd.isna(r["ci_low"]) else f"[{r['ci_low']*100:+.2f}%, {r['ci_high']*100:+.2f}%]"
        lines.append(f"{r['segment']:<14} bets={int(r['bets']):4d} win={wr:>8} ROI={roi:>9} P/L=${r['profit']:+,.2f} 95%CI={ci}")

    lines += ["", "4+ YEAR STRONG TIER", "-"*72]
    for _,r in strong_summary.iterrows():
        wr = "n/a" if pd.isna(r["win_rate"]) else f"{r['win_rate']*100:.2f}%"
        roi = "n/a" if pd.isna(r["roi"]) else f"{r['roi']*100:+.2f}%"
        lines.append(f"{r['segment']:<14} bets={int(r['bets']):4d} win={wr:>8} ROI={roi:>9} P/L=${r['profit']:+,.2f}")

    lines += [
        "",
        "DATA NOTE",
        "-"*72,
        "BestFightOdds archived event pages are used for sportsbook lines.",
        "No-vig probability uses the median across displayed sportsbook pairs;",
        "ROI uses the best displayed archived moneyline for the selected favorite.",
        "This makes the 2024-current validation closer to the live watcher than",
        "the older one-pair archival dataset, so keep the two periods conceptually separate.",
    ]

    report = OUT/"report.txt"
    report.write_text("\n".join(lines)+"\n", encoding="utf-8")
    print(report.read_text(), flush=True)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "matched_recent_fights": len(df),
        "date_start": str(df["event_date"].min().date()),
        "date_end": str(df["event_date"].max().date()),
        "hybrid_3yr": summary.to_dict(orient="records"),
        "hybrid_4yr": strong_summary.to_dict(orient="records"),
    }
    (OUT/"summary.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
