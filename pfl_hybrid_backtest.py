from __future__ import annotations

import io
import json
import math
import re
import statistics
import zipfile
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from rapidfuzz.fuzz import ratio

UA = "mma-hybrid-research/2.0"
KAGGLE = "https://www.kaggle.com/api/v1/datasets/download/leandroiber/mmastats"
BFO = "https://www.bestfightodds.com"
OUT = Path("pfl_hybrid_backtest")
OUT.mkdir(exist_ok=True)
STAKE = 50.0

# Exact same live hybrid thresholds discovered in UFC.
MARKET_MIN = 0.70
QUALIFY_AGE_GAP = 3.0
STRONG_AGE_GAP = 4.0

BOOK_BLACKLIST = {
    "", "props", "polymarket $20 bonus i", "kalshi up to $500 i",
}


def norm_name(s: str) -> str:
    s = str(s or "").lower()
    s = s.replace("’", "'").replace("-", " ")
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def american(s):
    if s is None:
        return None
    m = re.search(r"(?<!\d)([+-]\d{2,5})(?!\d)", str(s).replace(",", ""))
    if not m:
        return None
    try:
        v = int(m.group(1))
        return v if v != 0 else None
    except Exception:
        return None


def implied(o):
    o = float(o)
    return (-o) / ((-o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def american_profit(stake, o):
    o = float(o)
    return stake * (100.0 / -o) if o < 0 else stake * (o / 100.0)


def strip_fighter_cell(s):
    s = " ".join(str(s or "").split())
    s = re.sub(r"^\d+\s+", "", s)
    return s.strip()


def valid_fighter_name(s):
    n = norm_name(s)
    if len(n) < 3:
        return False
    bad = ["over ", "under ", "fight goes", "fight doesn", "wins by", "any other", "round ", "draw"]
    return not any(x in n for x in bad)


def fetch_global_db(session):
    r = session.get(KAGGLE, timeout=180)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    dbs = [n for n in z.namelist() if n.lower().endswith(".duckdb")]
    if not dbs:
        raise RuntimeError("No DuckDB in global MMA dataset")
    p = Path("/tmp/mma_global.duckdb")
    with z.open(dbs[0]) as src, open(p, "wb") as dst:
        dst.write(src.read())
    return p


def load_fights_and_fighters(db_path):
    con = duckdb.connect(str(db_path), read_only=True)
    fights = con.execute("""
        select *
        from fights_career_longitudinal
        where lower(organization) in ('pfl','wsof')
          and event_date is not null
          and fighter_1 is not null and fighter_2 is not null
    """).fetchdf()
    fighters = con.execute("select fighter_name, dob from fighters_master").fetchdf()
    con.close()

    fights["event_date"] = pd.to_datetime(fights["event_date"], errors="coerce")
    fighters["dob"] = pd.to_datetime(fighters["dob"], errors="coerce")
    dob_map = {}
    for _, r in fighters.dropna(subset=["dob"]).iterrows():
        dob_map.setdefault(norm_name(r["fighter_name"]), pd.Timestamp(r["dob"]))

    # 'pfl' has a few pre-PFL-name records in the source. Restrict PFL-era
    # primary analysis to events that actually identify as PFL / Professional Fighters League.
    en = fights["event_name"].astype(str).str.lower()
    is_real_pfl = (
        (fights["organization"].str.lower() == "pfl")
        & (en.str.contains("professional fighters league") | en.str.contains(r"\bpfl\b", regex=True))
        & (fights["event_date"] >= pd.Timestamp("2017-01-01"))
    )
    is_wsof = fights["organization"].str.lower().eq("wsof")
    fights = fights[is_real_pfl | is_wsof].copy()

    fights["f1_norm"] = fights["fighter_1"].map(norm_name)
    fights["f2_norm"] = fights["fighter_2"].map(norm_name)
    fights["pair"] = fights.apply(lambda r: tuple(sorted((r["f1_norm"], r["f2_norm"]))), axis=1)
    fights["org_group"] = np.where(fights["organization"].str.lower().eq("wsof"), "WSOF", "PFL")
    return fights, dob_map


def discover_event_links(session, prefix):
    links = {}
    if prefix == "PFL":
        queries = ["PFL"] + [f"PFL {y}" for y in range(2017, 2027)] + [
            "PFL Playoffs", "PFL Championship", "PFL MENA", "PFL Europe",
            "PFL Super Fights", "PFL Challenger", "PFL Africa",
        ]
        wanted = lambda txt, href: href.startswith("/events/pfl-") or txt.lower().startswith("pfl")
    else:
        queries = ["WSOF"] + [f"WSOF {y}" for y in range(2012, 2018)] + ["World Series of Fighting"]
        wanted = lambda txt, href: href.startswith("/events/wsof-") or "wsof" in txt.lower() or "world series of fighting" in txt.lower()

    for q in queries:
        try:
            r = session.get(BFO + "/search", params={"query": q}, timeout=45)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "lxml")
            for a in soup.select('a[href*="/events/"]'):
                href = a.get("href") or ""
                txt = " ".join(a.get_text(" ", strip=True).split())
                if wanted(txt, href):
                    links[href] = txt
        except Exception:
            continue
    return links


def parse_bfo_event(session, href, label):
    r = session.get(BFO + href, timeout=60)
    if r.status_code != 200:
        return []
    soup = BeautifulSoup(r.text, "lxml")
    tables = soup.find_all("table")
    if not tables:
        return []

    # Pick table with sportsbook columns and moneyline fighter rows.
    target = None
    for t in tables:
        headers = [" ".join(x.get_text(" ", strip=True).split()) for x in t.find_all("th")]
        if any(h in {"DraftKings", "FanDuel", "BetMGM", "BetRivers", "Caesars"} for h in headers):
            target = t
            break
    if target is None:
        return []

    # Header row with bookmaker column positions.
    header_row = None
    for tr in target.find_all("tr"):
        cells = [" ".join(x.get_text(" ", strip=True).split()) for x in tr.find_all(["th", "td"])]
        if any(c in {"DraftKings", "FanDuel", "BetMGM", "BetRivers", "Caesars"} for c in cells):
            header_row = cells
            break
    if not header_row:
        return []

    book_idx = {}
    for i, h in enumerate(header_row):
        if h.lower() in BOOK_BLACKLIST or not h:
            continue
        if h.lower() == "props":
            continue
        if h in {"FanDuel", "Caesars", "BetRivers", "BetWay", "Unibet", "BetMGM", "DraftKings", "BetOnline.ag", "Bovada", "BetUS"}:
            book_idx[i] = h

    rows = []
    for tr in target.find_all("tr"):
        cells = [" ".join(x.get_text(" ", strip=True).split()) for x in tr.find_all(["th", "td"])]
        if not cells:
            continue
        name = strip_fighter_cell(cells[0])
        if not valid_fighter_name(name):
            continue
        odds = {}
        for idx, book in book_idx.items():
            if idx < len(cells):
                o = american(cells[idx])
                if o is not None:
                    odds[book] = o
        if odds:
            rows.append((name, odds))

    # Fighter moneyline rows occur in pairs before prop rows.
    bouts = []
    i = 0
    while i + 1 < len(rows):
        a, oa = rows[i]
        b, ob = rows[i+1]
        common = sorted(set(oa) & set(ob))
        if common and norm_name(a) != norm_name(b):
            quotes = []
            for book in common:
                ia, ib = implied(oa[book]), implied(ob[book])
                s = ia + ib
                if s <= 0:
                    continue
                quotes.append({
                    "book": book,
                    "odds_a": oa[book],
                    "odds_b": ob[book],
                    "no_vig_a": ia/s,
                    "no_vig_b": ib/s,
                })
            if quotes:
                bouts.append({
                    "source_event": label,
                    "source_href": href,
                    "fighter_a": a,
                    "fighter_b": b,
                    "quotes": quotes,
                })
                i += 2
                continue
        i += 1
    return bouts


def match_bout_to_result(bout, candidates):
    a, b = norm_name(bout["fighter_a"]), norm_name(bout["fighter_b"])
    pair = tuple(sorted((a,b)))
    exact = candidates[candidates["pair"] == pair]
    if len(exact) == 1:
        return exact.iloc[0]
    if len(exact) > 1:
        # Event title similarity breaks rare rematches.
        label = norm_name(bout["source_event"])
        exact = exact.copy()
        exact["score"] = exact["event_name"].astype(str).map(lambda x: ratio(label, norm_name(x)))
        return exact.sort_values("score", ascending=False).iloc[0]

    # Fuzzy pair match, conservative threshold.
    best = None
    best_score = 0
    for _, r in candidates.iterrows():
        n1, n2 = r["f1_norm"], r["f2_norm"]
        score1 = (ratio(a,n1)+ratio(b,n2))/2
        score2 = (ratio(a,n2)+ratio(b,n1))/2
        score = max(score1,score2)
        if score > best_score:
            best_score, best = score, r
    return best if best is not None and best_score >= 88 else None


def exact_age(dob, event_date):
    return (pd.Timestamp(event_date) - pd.Timestamp(dob)).days / 365.2425


def market_for_bout(bout):
    quotes = bout["quotes"]
    pa = statistics.median(q["no_vig_a"] for q in quotes)
    pb = 1-pa
    if pa >= pb:
        fav = "a"; fav_prob = pa
        best_quote = max(quotes, key=lambda q: q["odds_a"] if q["odds_a"] > 0 else -10000 + q["odds_a"])
        # Better American line: larger numeric value is always better (-200 > -250; +150 > +130).
        best_quote = max(quotes, key=lambda q: q["odds_a"])
        best_odds = best_quote["odds_a"]; best_book = best_quote["book"]
    else:
        fav = "b"; fav_prob = pb
        best_quote = max(quotes, key=lambda q: q["odds_b"])
        best_odds = best_quote["odds_b"]; best_book = best_quote["book"]
    return fav, fav_prob, best_odds, best_book


def bootstrap_roi(profits, n_boot=5000, seed=42):
    arr = np.asarray(profits, dtype=float)
    if len(arr) < 10:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    vals = []
    n = len(arr)
    for _ in range(n_boot):
        samp = rng.choice(arr, size=n, replace=True)
        vals.append(samp.sum()/(n*STAKE))
    return float(np.quantile(vals,.025)), float(np.quantile(vals,.975))


def metrics(df):
    if df.empty:
        return {"bets":0,"wins":0,"win_rate":np.nan,"profit":0.0,"roi":np.nan,"ci_low":np.nan,"ci_high":np.nan}
    profit = df["profit"].sum()
    lo, hi = bootstrap_roi(df["profit"].to_numpy())
    return {
        "bets":len(df), "wins":int(df["won"].sum()), "win_rate":float(df["won"].mean()),
        "profit":float(profit), "roi":float(profit/(len(df)*STAKE)), "ci_low":lo, "ci_high":hi,
    }


def summarize(df, group_name):
    rows=[]
    pool = df[df["market_prob"] >= MARKET_MIN].copy()
    rules = [
        ("70%+ market favorite", pool),
        ("70%+ favorite younger", pool[pool["younger_advantage"] > 0]),
        ("70%+ favorite >=2y younger", pool[pool["younger_advantage"] >= 2]),
        ("70%+ favorite >=3y younger", pool[pool["younger_advantage"] >= 3]),
        ("70%+ favorite >=4y younger", pool[pool["younger_advantage"] >= 4]),
        ("70%+ favorite >=5y younger", pool[pool["younger_advantage"] >= 5]),
        ("70%+ favorite >=7y younger", pool[pool["younger_advantage"] >= 7]),
        ("70%+ favorite >=10y younger", pool[pool["younger_advantage"] >= 10]),
        ("70%+ favorite older", pool[pool["younger_advantage"] < 0]),
    ]
    for label, d in rules:
        rows.append({"group":group_name,"rule":label,**metrics(d)})
    return rows


def main():
    session = requests.Session()
    session.headers.update({"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"})

    db = fetch_global_db(session)
    fights, dob_map = load_fights_and_fighters(db)
    print("RESULT_FIGHTS", fights.groupby("org_group").size().to_dict(), flush=True)

    all_bfo=[]
    event_counts={}
    for grp in ["PFL","WSOF"]:
        links=discover_event_links(session,grp)
        event_counts[grp]=len(links)
        print("DISCOVERED_EVENTS",grp,len(links),flush=True)
        for j,(href,label) in enumerate(sorted(links.items()),1):
            try:
                bouts=parse_bfo_event(session,href,label)
                if bouts:
                    print("SCRAPED",grp,j,"/",len(links),label,len(bouts),flush=True)
                    for b in bouts: b["org_group"]=grp
                    all_bfo.extend(bouts)
            except Exception as exc:
                print("WARN",href,str(exc)[:200],flush=True)

    print("BFO_BOUTS",len(all_bfo),flush=True)
    records=[]
    for b in all_bfo:
        cand=fights[fights["org_group"]==b["org_group"]]
        r=match_bout_to_result(b,cand)
        if r is None:
            continue
        d1=dob_map.get(r["f1_norm"]); d2=dob_map.get(r["f2_norm"])
        if d1 is None or d2 is None or pd.isna(d1) or pd.isna(d2):
            continue
        if pd.isna(r["winner_side"]):
            continue
        age1=exact_age(d1,r["event_date"]); age2=exact_age(d2,r["event_date"])
        fav, mp, best_odds, best_book=market_for_bout(b)

        # Orient BFO A/B to result fighter1/fighter2 using name similarity.
        ba,bb=norm_name(b["fighter_a"]),norm_name(b["fighter_b"])
        direct=ratio(ba,r["f1_norm"])+ratio(bb,r["f2_norm"])
        rev=ratio(ba,r["f2_norm"])+ratio(bb,r["f1_norm"])
        if direct >= rev:
            fav_is_f1=(fav=="a")
        else:
            fav_is_f1=(fav=="b")

        fav_age=age1 if fav_is_f1 else age2
        dog_age=age2 if fav_is_f1 else age1
        younger_adv=dog_age-fav_age
        won=(int(r["winner_side"])==1) if fav_is_f1 else (int(r["winner_side"])==2)
        profit=american_profit(STAKE,best_odds) if won else -STAKE

        records.append({
            "org_group":b["org_group"],"event_date":pd.Timestamp(r["event_date"]),
            "event_name":r["event_name"],"fighter_1":r["fighter_1"],"fighter_2":r["fighter_2"],
            "favorite":r["fighter_1"] if fav_is_f1 else r["fighter_2"],
            "underdog":r["fighter_2"] if fav_is_f1 else r["fighter_1"],
            "favorite_age":fav_age,"underdog_age":dog_age,"younger_advantage":younger_adv,
            "market_prob":mp,"best_odds":best_odds,"best_book":best_book,
            "books":len(b["quotes"]),"won":bool(won),"profit":profit,
            "bfo_event":b["source_event"],"bfo_href":b["source_href"],
        })

    df=pd.DataFrame(records).drop_duplicates(subset=["org_group","event_date","fighter_1","fighter_2"])
    if df.empty:
        raise RuntimeError("No PFL/WSOF historical fights matched to odds+DOBs")
    df=df.sort_values("event_date").reset_index(drop=True)
    df.to_csv(OUT/"matched_fights.csv",index=False)

    summaries=[]
    for grp in ["PFL","WSOF"]:
        g=df[df["org_group"]==grp]
        if not g.empty:
            summaries += summarize(g,grp)
    summaries += summarize(df,"PFL+WSOF lineage")
    s=pd.DataFrame(summaries)
    s.to_csv(OUT/"rule_summary.csv",index=False)

    # Probability bands for primary >=3-year rule.
    bands=[]
    d=df[(df["market_prob"]>=.70)&(df["younger_advantage"]>=3)].copy()
    d["band"]=pd.cut(d["market_prob"],[.70,.75,.80,.85,.90,.95,1.001],right=False,
                     labels=["70-74.9","75-79.9","80-84.9","85-89.9","90-94.9","95+"])
    for (grp,band),g in d.groupby(["org_group","band"],observed=True):
        bands.append({"group":grp,"band":str(band),**metrics(g)})
    pd.DataFrame(bands).to_csv(OUT/"probability_bands_3yr.csv",index=False)

    # Year-by-year robustness for PFL primary rule.
    yrs=[]
    p=df[(df["org_group"]=="PFL")&(df["market_prob"]>=.70)&(df["younger_advantage"]>=3)].copy()
    p["year"]=p["event_date"].dt.year
    for yr,g in p.groupby("year"):
        yrs.append({"year":int(yr),**metrics(g)})
    pd.DataFrame(yrs).to_csv(OUT/"pfl_yearly_3yr.csv",index=False)

    lines=[
        "PFL / WSOF HYBRID HISTORICAL BACKTEST",
        "="*72,
        "",
        "Same rule as the UFC live hybrid:",
        "  sportsbook consensus no-vig favorite >=70%",
        "  AND favorite >=3.0 years younger (qualifies)",
        "  >=4.0 years younger = strong tier",
        f"  $50 flat stake; ROI uses best archived line shown across books on BestFightOdds.",
        "",
        f"Discovered BFO event pages: PFL={event_counts.get('PFL',0)}, WSOF={event_counts.get('WSOF',0)}",
        f"Matched fights with odds + result + both DOBs: {len(df)}",
        f"Matched PFL: {len(df[df['org_group']=='PFL'])}",
        f"Matched WSOF: {len(df[df['org_group']=='WSOF'])}",
        f"Date range: {df['event_date'].min().date()} to {df['event_date'].max().date()}",
        "",
        "RULE RESULTS",
        "-"*72,
    ]
    for _,r in s.iterrows():
        roi="n/a" if pd.isna(r["roi"]) else f"{r['roi']*100:+.2f}%"
        wr="n/a" if pd.isna(r["win_rate"]) else f"{r['win_rate']*100:.2f}%"
        ci="n/a" if pd.isna(r["ci_low"]) else f"[{r['ci_low']*100:+.2f}%, {r['ci_high']*100:+.2f}%]"
        lines.append(f"{r['group']:<18} {r['rule']:<34} bets={int(r['bets']):4d} win={wr:>8} ROI={roi:>9} P/L=${r['profit']:+,.2f} 95%CI={ci}")

    lines += [
        "",
        "NOTES",
        "-"*72,
        "Historical odds come from BestFightOdds archived event pages and are",
        "combined across displayed sportsbooks to compute median no-vig probability.",
        "ROI uses the best displayed archived moneyline for the selected favorite.",
        "This is closer to the current live UFC method than our earlier UFC archival",
        "dataset backtest, but exact historical line timing/closing status can vary.",
        "",
        "PFL-era results are the primary decision set. WSOF is reported separately",
        "as the predecessor organization and is not silently pooled into PFL.",
    ]
    report=OUT/"report.txt"
    report.write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(report.read_text(),flush=True)

    summary={
        "generated_at":datetime.now(timezone.utc).isoformat(),
        "events_discovered":event_counts,
        "matched_fights":len(df),
        "pfl_matched":len(df[df["org_group"]=="PFL"]),
        "wsof_matched":len(df[df["org_group"]=="WSOF"]),
        "date_start":str(df["event_date"].min().date()),
        "date_end":str(df["event_date"].max().date()),
        "rules":s.to_dict(orient="records"),
    }
    (OUT/"summary.json").write_text(json.dumps(summary,indent=2,default=str),encoding="utf-8")

if __name__=="__main__":
    main()
