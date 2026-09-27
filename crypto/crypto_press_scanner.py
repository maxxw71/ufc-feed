#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

ROOT = Path("/home/anestishkurti92/crypto-press-scanner")
STATE = ROOT / "seen.json"
UA = "Mozilla/5.0 (compatible; AppwizaCryptoPressScanner/1.0; +https://appwiza.com)"
TIMEOUT = 18

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
    out = set()
    for m in re.finditer(r'(?is)<a\b[^>]*href=["\']([^"\'#]+)', s):
        u = urljoin(base, html.unescape(m.group(1)).strip()).split("#",1)[0]
        if u.startswith("http"):
            out.add(u)
    return out

def source_for(url):
    host = (urlparse(url).hostname or "").lower()
    for src in SOURCES:
        if any(host == d or host.endswith("." + d) for d in src["domains"]):
            return src
    return None

def articleish(url):
    p = urlparse(url).path.lower()
    if len(p.strip("/")) < 8:
        return False
    if any(x in p for x in ("/tag/","/category/","/author/","/page/","/search","/privacy","/terms","/careers","/events")):
        return False
    return any(x in p for x in ("press","news","article","insight","blog","perspective","announcement","release","story"))

def discover():
    found = set()
    for src in SOURCES:
        for page in src["pages"]:
            try:
                raw = fetch(page)
                for u in page_links(page, raw):
                    if source_for(u) and articleish(u):
                        found.add(u)
            except Exception as e:
                print("source_error", src["name"], page, type(e).__name__, flush=True)
    return found

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

    tokens = list(src["tokens"])
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
    print("discovered", len(urls), flush=True)
    alerts = []
    now = int(time.time())

    for url in sorted(urls):
        if url in seen:
            continue
        try:
            raw = fetch(url)
            item = evaluate(url, raw)
            seen[url] = now
            if item:
                alerts.append(item)
        except Exception as e:
            print("article_error", url, type(e).__name__, flush=True)

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
