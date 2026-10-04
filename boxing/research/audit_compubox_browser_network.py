#!/usr/bin/env python3
"""Browser-level audit of known CompuBox pages and their JSON/network data.

Uses Chromium because CompuBox round routes are client-rendered/redirected for
plain HTTP clients. Known report IDs only; no broad crawl.
"""
from __future__ import annotations
import asyncio,json,re
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlparse
from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/"punch_supplements"
SRC=P/"boxing_data_external_verifications.json"
OUT=P/"official_compubox_browser_network_audit.json"

async def main():
    src=json.loads(SRC.read_text())
    ids=[];urls=[]
    for x in src.get("fights",[]):
        u=x.get("source_url") or ""
        m=re.search(r"/round-stats/(\d+)",u)
        if m and m.group(1) not in ids:
            ids.append(m.group(1));urls.append(u)
    recs=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        page=await browser.new_page(viewport={"width":1440,"height":1200})
        network=[]
        async def on_response(resp):
            u=resp.url
            typ=resp.request.resource_type
            ct=(resp.headers or {}).get("content-type","")
            if typ in ("xhr","fetch") or "json" in ct.lower() or re.search(r"(round|stat|fight|report)",u,re.I):
                item={"url":u,"status":resp.status,"resource_type":typ,"content_type":ct}
                try:
                    if "json" in ct.lower():
                        txt=await resp.text()
                        item["body_prefix"]=txt[:12000]
                except Exception:
                    pass
                network.append(item)
        page.on("response",on_response)

        # First load homepage so app bundles initialize.
        await page.goto("https://beta.compuboxdata.com/",wait_until="networkidle",timeout=90000)
        links=await page.locator("a").evaluate_all("""els => els.map(a=>({href:a.href,text:(a.innerText||'').trim()})).filter(x=>x.href.includes('round-stats'))""")
        recs.append({"stage":"homepage","url":page.url,"round_links":links[:100],"network":list(network)})
        network.clear()

        for rid,orig in zip(ids,urls):
            attempts=[]
            for host in ("https://beta.compuboxdata.com","https://app2.compuboxdata.com","https://api2.compuboxdata.com"):
                target=f"{host}/round-stats/{rid}"
                try:
                    await page.goto(target,wait_until="domcontentloaded",timeout=60000)
                    await page.wait_for_timeout(3500)
                    final=page.url
                    text=(await page.locator("body").inner_text())[:30000]
                    tables=[]
                    for ti in range(await page.locator("table").count()):
                        t=page.locator("table").nth(ti)
                        rows=await t.locator("tr").evaluate_all("""trs=>trs.map(tr=>Array.from(tr.querySelectorAll('th,td')).map(x=>(x.innerText||'').trim()))""")
                        if rows: tables.append({"table_index":ti,"rows":rows[:80]})
                    attempts.append({"target":target,"final_url":final,"body_prefix":text,"tables":tables,"network":list(network)})
                    # If route really rendered a round report, no need to try other hosts.
                    if re.search(r"(ROUND|TOTAL PUNCH|JAB|POWER)",text,re.I) and (tables or str(rid) in text):
                        break
                except Exception as e:
                    attempts.append({"target":target,"error":type(e).__name__+": "+str(e)[:500],"network":list(network)})
                finally:
                    network.clear()
            recs.append({"report_id":rid,"original_url":orig,"attempts":attempts})
        await browser.close()
    report={"generated_at":datetime.now(timezone.utc).isoformat(),"known_report_ids":ids,"records":recs,
            "policy":"Known report IDs only; browser audit for client-rendered public page structure/network calls. No site-wide crawl."}
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    # Compact stdout
    print(json.dumps({
      "known_report_ids":ids,
      "homepage_round_links":len(recs[0].get("round_links") or []),
      "network_urls":sorted({n["url"] for r in recs for a in (r.get("attempts") or []) for n in a.get("network",[])})[:80]
    },indent=2))

if __name__=="__main__":asyncio.run(main())
