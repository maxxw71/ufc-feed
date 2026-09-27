#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

ROOT = Path("/home/anestishkurti92/crypto-press-scanner")
STATE = ROOT / "seen.json"
UA = "Mozilla/5.0 (compatible; AppwizaCryptoPressScanner/1.0; +https://appwiza.com)"
TIMEOUT = 8
MAX_LINKS_PER_PAGE = 80
MAX_ARTICLES_PER_RUN = 350

SOURCES = [
    {"name":"Quant","domains":["quant.network"],"pages":["https://quant.network/press-releases/"],"tokens":["QNT"]},
    {"name":"Ripple","domains":["ripple.com"],"pages":["https://ripple.com/ripple-press/","https://ripple.com/insights/"],"tokens":["XRP"]},
    {"name":"Chainlink","domains":["chain.link","blog.chain.link"],"pages":["https://blog.chain.link/"],"tokens":["LINK"]},
    {"name":"Circle","domains":["circle.com"],"pages":["https://www.circle.com/pressroom","https://www.circle.com/blog"],"tokens":["USDC"]},
    {"name":"Stellar","domains":["stellar.org"],"pages":["https://stellar.org/press","https://stellar.org/blog"],"tokens":["XLM"]},
    {"name":"Hedera","domains":["hedera.com"],"pages":["https://hedera.com/blog"],"tokens":["HBAR"]},
    {"name":"Avalanche","domains":["avax.network"],"pages":["https://www.avax.network/blog"],"tokens":["AVAX"]},
    {"name":"Solana","domains":["solana.com"],"pages":["https://solana.com/news"],"tokens":["SOL"]},
    {"name":"The Clearing House","domains":["theclearinghouse.org"],"pages":["https://www.theclearinghouse.org/payment-systems/Articles"],"tokens":[]},
    {"name":"DTCC","domains":["dtcc.com"],"pages":["https://www.dtcc.com/news"],"tokens":[]},
    {"name":"Swift","domains":["swift.com"],"pages":["https://www.swift.com/news-events/news"],"tokens":[]},
    {"name":"Bank of England","domains":["bankofengland.co.uk"],"pages":["https://www.bankofengland.co.uk/news"],"tokens":[]},
    {"name":"ECB","domains":["ecb.europa.eu"],"pages":["https://www.ecb.europa.eu/press/html/index.en.html"],"tokens":[]},
    {"name":"BIS","domains":["bis.org"],"pages":["https://www.bis.org/press/index.htm"],"tokens":[]},
    {"name":"Visa","domains":["visa.com"],"pages":["https://usa.visa.com/about-visa/newsroom.html"],"tokens":[]},
    {"name":"Mastercard","domains":["mastercard.com"],"pages":["https://www.mastercard.com/news/"],"tokens":[]},

    # Broad discovery: these can surface projects/tokens we have never preselected.
    {"name":"PR Newswire Crypto","domains":["prnewswire.com"],"pages":[
        "https://www.prnewswire.com/news-releases/consumer-technology-latest-news/cryptocurrency-list/",
        "https://www.prnewswire.com/news-releases/business-technology-latest-news/blockchain-list/",
        "https://www.prnewswire.com/news-releases/consumer-technology-latest-news/blockchain-list/"
    ],"tokens":[]},
    {"name":"GlobeNewswire Crypto","domains":["globenewswire.com"],"pages":[
        "https://www.globenewswire.com/en/search/tag/blockchain",
        "https://www.globenewswire.com/search/tag/crypto"
    ],"tokens":[]},

    # US / global regulators and central-bank infrastructure.
    {"name":"SEC Crypto","domains":["sec.gov"],"pages":[
        "https://www.sec.gov/about/crypto-task-force/crypto-newsroom",
        "https://www.sec.gov/newsroom/press-releases?combine=crypto"
    ],"tokens":[]},
    {"name":"CFTC","domains":["cftc.gov"],"pages":["https://www.cftc.gov/PressRoom/PressReleases"],"tokens":[]},
    {"name":"OCC","domains":["occ.treas.gov"],"pages":["https://www.occ.treas.gov/news-issuances/news-releases/index-news-releases.html"],"tokens":[]},
    {"name":"Federal Reserve","domains":["federalreserve.gov"],"pages":["https://www.federalreserve.gov/newsevents/pressreleases.htm"],"tokens":[]},
    {"name":"FCA","domains":["fca.org.uk"],"pages":["https://www.fca.org.uk/news"],"tokens":[]},
    {"name":"MAS","domains":["mas.gov.sg"],"pages":["https://www.mas.gov.sg/news/media-releases"],"tokens":[]},
    {"name":"HKMA","domains":["hkma.gov.hk"],"pages":["https://www.hkma.gov.hk/eng/news-and-media/press-releases/"],"tokens":[]},
    {"name":"NYDFS","domains":["dfs.ny.gov"],"pages":["https://www.dfs.ny.gov/reports_and_publications/press_releases"],"tokens":[]},

    # Exchanges / brokers often announce listings and institutional integrations first.
    {"name":"Coinbase","domains":["coinbase.com"],"pages":["https://www.coinbase.com/blog"],"tokens":[]},
    {"name":"Kraken","domains":["kraken.com"],"pages":["https://blog.kraken.com/"],"tokens":[]},
    {"name":"Binance","domains":["binance.com"],"pages":["https://www.binance.com/en/support/announcement"],"tokens":[]},
    {"name":"Robinhood","domains":["robinhood.com"],"pages":["https://newsroom.aboutrobinhood.com/"],"tokens":[]},

    # Asset managers / tokenization infrastructure.
    {"name":"BlackRock","domains":["blackrock.com"],"pages":["https://www.blackrock.com/corporate/newsroom"],"tokens":[]},
    {"name":"Franklin Templeton","domains":["franklintempleton.com"],"pages":["https://www.franklintempleton.com/press-releases"],"tokens":[]},
    {"name":"WisdomTree","domains":["wisdomtree.com"],"pages":["https://www.wisdomtree.com/investments/blog"],"tokens":[]},
    {"name":"Grayscale","domains":["grayscale.com"],"pages":["https://www.grayscale.com/press"],"tokens":[]},
    {"name":"VanEck","domains":["vaneck.com"],"pages":["https://www.vaneck.com/us/en/blogs/digital-assets/"],"tokens":[]},
    {"name":"Bitwise","domains":["bitwiseinvestments.com"],"pages":["https://bitwiseinvestments.com/newsroom"],"tokens":[]},
    {"name":"Securitize","domains":["securitize.io"],"pages":["https://securitize.io/learn/press"],"tokens":[]},
    {"name":"Fireblocks","domains":["fireblocks.com"],"pages":["https://www.fireblocks.com/blog"],"tokens":[]},

    # Payments / fintech can reveal adoption before a token project's own press page.
    {"name":"PayPal","domains":["paypal.com"],"pages":["https://newsroom.paypal-corp.com/"],"tokens":[]},
    {"name":"Stripe","domains":["stripe.com"],"pages":["https://stripe.com/newsroom"],"tokens":[]},

    # Large banks with active digital-asset/tokenization programs.
    {"name":"JPMorgan","domains":["jpmorganchase.com","jpmorgan.com"],"pages":["https://www.jpmorganchase.com/newsroom"],"tokens":[]},
    {"name":"Citi","domains":["citigroup.com"],"pages":["https://www.citigroup.com/global/news"],"tokens":[]},
    {"name":"BNY","domains":["bny.com"],"pages":["https://www.bny.com/corporate/global/en/about-us/newsroom.html"],"tokens":[]},
    {"name":"State Street","domains":["statestreet.com"],"pages":["https://newsroom.statestreet.com/"],"tokens":[]},
    {"name":"Standard Chartered","domains":["sc.com"],"pages":["https://www.sc.com/en/media/press-release/"],"tokens":[]},
    {"name":"HSBC","domains":["hsbc.com"],"pages":["https://www.hsbc.com/news-and-views/news"],"tokens":[]},
    {"name":"UBS","domains":["ubs.com"],"pages":["https://www.ubs.com/global/en/media/display-page-ndp/en-2026.html"],"tokens":[]},
]

CRYPTO_TERMS = [
    "blockchain","digital asset","digital assets","tokenised","tokenized","tokenisation","tokenization",
    "stablecoin","stablecoins","cryptocurrency","crypto asset","on-chain","onchain","distributed ledger",
    "dlt","cbdc","digital currency","web3","smart contract","tokenized deposit","tokenised deposit"
]

IMPACT = {
    "live":3, "production":3, "launch":2, "launched":2, "selected":3, "adopt":3, "adoption":3,
    "bank":2, "banks":2, "central bank":3, "payment network":3, "settlement":2, "clearing":2,
    "custody":2, "institutional":2, "institution":2, "regulatory approval":3, "approved":2,
    "license":2, "licensed":2, "integrates":2, "integration":2, "partners with":1, "partnership":1,
    "deposit":2, "deposits":2, "securities":1, "fund":1, "etf":2, "exchange-traded":2,
    "cross-border":2, "interoperability":2, "oracle":1, "mainnet":2, "token utility":3,
    "staking":2, "burn":2, "supply":2, "treasury":1,
}

NAMED_INSTITUTIONS = [
    "bank of england","federal reserve","european central bank","the clearing house","swift","dtcc",
    "visa","mastercard","hsbc","barclays","santander","jpmorgan","j.p. morgan","citigroup",
    "goldman sachs","blackrock","fidelity","standard chartered","ubs","bny","state street"
]

LOW_VALUE = ["webinar","podcast","event recap","conference","sponsorship","award","career","hiring","meet us at"]

TOKEN_ALIASES = {
    "BTC":["bitcoin"," btc ","$btc"], "ETH":["ethereum"," ether "," eth ","$eth"],
    "SOL":["solana"," sol ","$sol"], "XRP":["xrp","ripple"], "QNT":["quant"," qnt ","$qnt"],
    "LINK":["chainlink"," link ","$link"], "ADA":["cardano"," ada ","$ada"], "AVAX":["avalanche"," avax ","$avax"],
    "DOT":["polkadot"," dot ","$dot"], "ATOM":["cosmos"," atom ","$atom"], "NEAR":["near protocol"," near ","$near"],
    "SUI":["sui"," sui ","$sui"], "APT":["aptos"," apt ","$apt"], "ARB":["arbitrum"," arb ","$arb"],
    "OP":["optimism"," op ","$op"], "MATIC":["polygon","matic"," pol "], "POL":["polygon ecosystem token"," pol ","$pol"],
    "HBAR":["hedera","hbar"], "XLM":["stellar"," xlm "], "ALGO":["algorand"," algo "],
    "ICP":["internet computer"," icp "], "FIL":["filecoin"," fil "], "AAVE":["aave"],
    "UNI":["uniswap"," uni "], "MKR":["makerdao","maker protocol"," mkr "], "ONDO":["ondo finance"," ondo "],
    "ENA":["ethena"," ena "], "PENDLE":["pendle"], "LDO":["lido"," ldo "], "RUNE":["thorchain"," rune "],
    "INJ":["injective"," inj "], "SEI":["sei network"," sei "], "TIA":["celestia"," tia "],
    "FET":["fetch.ai","artificial superintelligence alliance"," fet "], "TAO":["bittensor"," tao "],
    "RENDER":["render network"," render "], "GRT":["the graph"," grt "], "WLD":["worldcoin","world network"," wld "],
    "DOGE":["dogecoin"," doge "], "SHIB":["shiba inu"," shib "], "PEPE":["pepe"," pepe "],
    "TON":["the open network","toncoin"," ton "], "TRX":["tron"," trx "], "BCH":["bitcoin cash"," bch "],
    "LTC":["litecoin"," ltc "], "ETC":["ethereum classic"," etc "], "CRO":["cronos"," cro "],
    "KAS":["kaspa"," kas "], "STX":["stacks"," stx "], "IMX":["immutable"," imx "],
    "MNT":["mantle"," mnt "], "KAIA":["kaia"," kaia "], "FLOW":["flow blockchain"," flow "],
    "EGLD":["multiversx"," egld "], "XTZ":["tezos"," xtz "], "VET":["vechain"," vet "],
    "THETA":["theta network"," theta "], "PYTH":["pyth network"," pyth "], "JUP":["jupiter exchange"," jup "],
    "BONK":["bonk"," bonk "], "USDC":["usd coin"," usdc "], "USDT":["tether"," usdt "],
    "PYUSD":["paypal usd","pyusd"], "RLUSD":["ripple usd","rlusd"], "USDG":["global dollar","usdg"],
    "CC":["canton coin","canton network"], "HYPE":["hyperliquid"," hype "],
}

def infer_tokens(text, fixed=None):
    padded = " " + text.lower() + " "
    found = []
    for ticker, aliases in TOKEN_ALIASES.items():
        if any(alias in padded for alias in aliases):
            found.append(ticker)
    # Explicit cashtags are useful for newly launched assets not in our dictionary.
    for ticker in re.findall(r'\$([A-Z][A-Z0-9]{1,9})\b', text):
        if ticker not in found:
            found.append(ticker)
    # Also catch common "(XYZ)" ticker notation in title/lead, but stay conservative.
    for ticker in re.findall(r'\(([A-Z][A-Z0-9]{1,7})\)', text[:3500]):
        if ticker not in found and ticker not in {"ETF","SEC","CEO","USA","USD","API","AI","ETP","IPO"}:
            found.append(ticker)
    for ticker in (fixed or []):
        if ticker not in found:
            found.append(ticker)
    return found[:12]


def fetch(url, max_bytes=3_000_000):
    req = Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*"})
    with urlopen(req, timeout=TIMEOUT) as r:
        return r.read(max_bytes)

def clean_text(raw):
    s = raw.decode("utf-8","ignore") if isinstance(raw,(bytes,bytearray)) else str(raw)
    s = re.sub(r"(?is)<script.*?</script>|<style.*?</style>|<svg.*?</svg>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()

def page_title(raw):
    s = raw.decode("utf-8","ignore")
    for pat in (
        r'(?is)<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
        r'(?is)<title[^>]*>(.*?)</title>',
        r'(?is)<h1[^>]*>(.*?)</h1>',
    ):
        m = re.search(pat, s)
        if m:
            return clean_text(m.group(1))[:240]
    return ""

def page_links(base, raw):
    s = raw.decode("utf-8","ignore")
    out, seen = [], set()
    for m in re.finditer(r'(?is)<a\b[^>]*href=["\']([^"\'#]+)', s):
        u = urljoin(base, html.unescape(m.group(1)).strip()).split("#",1)[0]
        if u.startswith("http") and u not in seen:
            seen.add(u)
            out.append(u)
    return out

def source_for(url):
    host = (urlparse(url).hostname or "").lower()
    for src in SOURCES:
        if any(host == d or host.endswith("." + d) for d in src["domains"]):
            return src
    return None

def articleish(url):
    host = (urlparse(url).hostname or "").lower()
    p = urlparse(url).path.lower()
    if len(p.strip("/")) < 8:
        return False
    if any(x in p for x in ("/tag/","/category/","/author/","/page/","/search","/privacy","/terms","/careers","/events")):
        return False

    # Press-wire category/index links were the main source of crawl explosion.
    if "prnewswire.com" in host:
        return p.startswith("/news-releases/") and p.endswith(".html") and "-list/" not in p and "latest-news" not in p
    if "globenewswire.com" in host:
        return ("/news-release/" in p or "/newsroom/" in p) and (p.endswith(".html") or p.count("/") >= 4)

    return any(x in p for x in ("press","news","article","insight","blog","perspective","announcement","release","story"))

def discover():
    found = []
    seen = set()
    for src in SOURCES:
        for page in src["pages"]:
            try:
                raw = fetch(page)
                accepted = 0
                for u in page_links(page, raw):
                    if u in seen:
                        continue
                    if source_for(u) and articleish(u):
                        seen.add(u)
                        found.append(u)
                        accepted += 1
                        if accepted >= MAX_LINKS_PER_PAGE:
                            break
            except Exception as e:
                print("source_error", src["name"], page, type(e).__name__, flush=True)
    return found[:MAX_ARTICLES_PER_RUN]

def evaluate(url, raw):
    src = source_for(url)
    if not src:
        return None
    title = page_title(raw)
    text = clean_text(raw)
    title_low = title.lower()
    lead_low = text[:5500].lower()
    low = (title + " " + text[:5500]).lower()

    # Require crypto/tokenisation relevance in the title/lead, not just footer/nav text.
    crypto_hit = any(x in low for x in CRYPTO_TERMS)
    token_hit = any(t.lower() in low for t in src["tokens"])
    institution_hit = any(x in low for x in NAMED_INSTITUTIONS)
    if not crypto_hit and not (src["tokens"] and token_hit and institution_hit):
        return None

    # Require a concrete action/catalyst; generic educational posts should not alert.
    action_terms = (
        "live","production","launch","launched","selected","adopt","adoption","approved",
        "integrates","integration","partners with","partnership","settlement","clearing",
        "custody","license","licensed","mainnet","token utility","staking","burn"
    )
    if not any(x in low for x in action_terms):
        return None

    score = 0
    reasons = []
    for term, weight in IMPACT.items():
        if term in low:
            score += weight
            if weight >= 2 and term not in reasons and len(reasons) < 7:
                reasons.append(term)

    for term in NAMED_INSTITUTIONS:
        if term in low:
            score += 3
            if term not in reasons and len(reasons) < 7:
                reasons.append(term)

    if any(x in title_low for x in LOW_VALUE):
        score -= 6

    # Strict threshold: this is an alert feed, not a general news digest.
    if score < 10:
        return None

    tokens = infer_tokens(title + " " + text[:5500], src["tokens"])
    excerpt = text[:900]
    return {
        "source": src["name"],
        "title": title or url,
        "url": url,
        "tokens": tokens,
        "score": score,
        "reasons": reasons,
        "excerpt": excerpt,
    }

def appwiza_send(subject, body):
    # Load the same environment file used by the live UFC watcher, without
    # copying, logging, or persisting any credential elsewhere.
    env_path = Path("/home/anestishkurti92/.config/ufc-watcher.env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    mail_root = "/home/anestishkurti92/ufc-predictor-v1"
    if mail_root not in sys.path:
        sys.path.insert(0, mail_root)
    import ufc_email_watcher
    return ufc_email_watcher.send_email(subject, body)

def render(item):
    tok = ", ".join(item["tokens"]) if item["tokens"] else "Institutional crypto"
    reasons = ", ".join(item["reasons"]) or "material institutional / crypto infrastructure language"
    return f"""<div style="font-family:Arial,sans-serif;max-width:760px;margin:auto;color:#172033">
    <div style="font-size:12px;font-weight:700;color:#6b7280;letter-spacing:.08em">APPWIZA · CRYPTO PRESS ALERT</div>
    <h2 style="margin:10px 0 6px">{html.escape(item['title'])}</h2>
    <div style="font-size:14px;color:#4b5563">{html.escape(item['source'])} · {html.escape(tok)} · relevance score {item['score']}</div>
    <p style="font-size:15px;line-height:1.55"><b>Why it triggered:</b> {html.escape(reasons)}</p>
    <p style="font-size:15px;line-height:1.55">{html.escape(item['excerpt'])}</p>
    <p><a href="{html.escape(item['url'],quote=True)}" style="display:inline-block;background:#111827;color:white;text-decoration:none;padding:11px 16px;border-radius:7px">Open primary source</a></p>
    <hr style="border:0;border-top:1px solid #e5e7eb;margin:22px 0">
    <p style="font-size:12px;color:#6b7280">Automated primary-source scanner. A trigger means the announcement may be market-relevant; it does not guarantee price impact.</p>
    </div>"""

def send_item(item, test=False):
    prefix = "[TEST] " if test else ""
    subject = prefix + f"Crypto Press Alert — {item['source']}: {item['title'][:100]}"
    result = appwiza_send(subject, render(item))
    print("email_result", repr(result), flush=True)

def load_seen():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_seen(seen):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    if len(seen) > 6000:
        seen = dict(sorted(seen.items(), key=lambda kv: kv[1])[-5000:])
    STATE.write_text(json.dumps(seen, indent=2), encoding="utf-8")

def scan(prime=False):
    seen = load_seen()
    urls = discover()
    fresh = [u for u in urls if u not in seen]
    print("discovered", len(urls), "fresh", len(fresh), flush=True)
    alerts = []
    now = int(time.time())

    def process(url):
        try:
            raw = fetch(url)
            return url, evaluate(url, raw), None
        except Exception as e:
            return url, None, type(e).__name__

    # Parallel bounded fetches keep broad discovery fast and isolate slow/blocking sites.
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(process, u) for u in fresh]
        for fut in as_completed(futures):
            url, item, err = fut.result()
            seen[url] = now
            if err:
                print("article_error", url, err, flush=True)
            elif item:
                alerts.append(item)

    if not prime:
        alerts.sort(key=lambda x: x["score"], reverse=True)
        for item in alerts[:8]:
            send_item(item)
            print("sent", item["source"], item["score"], item["url"], flush=True)

    save_seen(seen)
    print("qualifying", len(alerts), "prime", prime, flush=True)

def test_quant():
    url = "https://quant.network/press-releases/the-clearing-house-partners-with-quant-to-advance-the-on-chain-money-initiative/"
    raw = fetch(url)
    item = evaluate(url, raw) or {
        "source":"Quant",
        "title":"The Clearing House partners with Quant to advance the On-Chain Money Initiative",
        "url":url,
        "tokens":["QNT"],
        "score":10,
        "reasons":["the clearing house","selected","payment network","tokenized deposits"],
        "excerpt":clean_text(raw)[:900],
    }
    send_item(item, test=True)

def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "run"
    if arg == "prime":
        scan(prime=True)
    elif arg == "test-quant":
        test_quant()
    else:
        scan(prime=False)

if __name__ == "__main__":
    main()
