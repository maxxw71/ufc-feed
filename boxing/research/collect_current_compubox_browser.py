#!/usr/bin/env python3
"""Collect currently linked public CompuBox round reports with Chromium.

Research-only supplemental capture. Reads only the finite round-report links
displayed on the CompuBox homepage at run time. Does not enumerate IDs.
"""
from __future__ import annotations
import asyncio,json,re
from datetime import datetime,timezone
from pathlib import Path
from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"punch_supplements"/"compubox_current_round_reports_browser.json"
BASE="https://beta.compuboxdata.com/"

def clean(x):return re.sub(r"\s+"," ",str(x or "")).strip()

async def main():
    records=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        page=await browser.new_page(viewport={"width":1440,"height":1400})
        await page.goto(BASE,wait_until="networkidle",timeout=90000)
        links=await page.locator("a").evaluate_all("""els => els.map(a=>({href:a.href,text:(a.innerText||'').trim()})).filter(x=>x.href.includes('/round-stats/'))""")
        seen=set();links=[x for x in links if not (x["href"] in seen or seen.add(x["href"]))]
        for item in links[:20]:
            href=item["href"]
            rec={"source_url":href,"link_text":item.get("text")}
            try:
                await page.goto(href,wait_until="domcontentloaded",timeout=60000)
                await page.wait_for_timeout(2500)
                rec["final_url"]=page.url
                rec["title"]=clean(await page.title())
                rec["body_prefix"]=clean(await page.locator("body").inner_text())[:6000]
                tables=[]
                n=await page.locator("table").count()
                for ti in range(n):
                    rows=await page.locator("table").nth(ti).locator("tr").evaluate_all(
                      """trs=>trs.map(tr=>Array.from(tr.querySelectorAll('th,td')).map(x=>(x.innerText||'').trim())).filter(r=>r.length)"""
                    )
                    if rows:
                        blob=" ".join(" ".join(r) for r in rows)
                        if re.search(r"(round|total|jab|power|landed|thrown)",blob,re.I):
                            tables.append({"table_index":ti,"rows":rows[:120]})
                rec["tables"]=tables
                rec["table_count"]=len(tables)
                rec["rendered_round_report"]=bool(tables and re.search(r"(round|total|jab|power)",rec["body_prefix"],re.I))
            except Exception as e:
                rec["error"]=type(e).__name__+": "+str(e)[:500]
            records.append(rec)
            await page.goto(BASE,wait_until="domcontentloaded",timeout=60000)
            await page.wait_for_timeout(500)
        await browser.close()
    report={
      "generated_at":datetime.now(timezone.utc).isoformat(),
      "homepage_links":len(links),
      "reports_attempted":len(records),
      "reports_rendered":sum(bool(x.get("rendered_round_report")) for x in records),
      "reports_with_tables":sum(bool(x.get("table_count")) for x in records),
      "records":records,
      "status":"RESEARCH_SUPPLEMENT_ONLY",
      "policy":"Finite current homepage-linked CompuBox round reports only. No ID enumeration. Preserve as research supplement pending intended-use/licensing review."
    }
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps({k:report[k] for k in ("homepage_links","reports_attempted","reports_rendered","reports_with_tables")},indent=2))

if __name__=="__main__":asyncio.run(main())
