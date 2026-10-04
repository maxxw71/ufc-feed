#!/usr/bin/env python3
"""Collect currently linked public CompuBox round-report downloads with Chromium.

Research-only supplemental capture. Reads only the finite round-report links
displayed on the CompuBox homepage at run time. Does not enumerate IDs.
"""
from __future__ import annotations
import asyncio,csv,io,json,re,zipfile
from datetime import datetime,timezone
from pathlib import Path

import openpyxl
import xlrd
from pypdf import PdfReader
from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"punch_supplements"/"compubox_current_round_reports_browser.json"
BASE="https://beta.compuboxdata.com/"

def clean(x):return re.sub(r"\s+"," ",str(x or "")).strip()

def parse_download(data:bytes, filename:str):
    low=(filename or "").lower()
    out={"suggested_filename":filename,"bytes":len(data)}
    if data.startswith(b"%PDF") or low.endswith(".pdf"):
        out["file_type"]="pdf"
        try:
            reader=PdfReader(io.BytesIO(data))
            pages=[]
            for p in reader.pages:
                pages.append(p.extract_text() or "")
            out["pages"]=len(reader.pages)
            out["text"]="\n".join(pages)[:120000]
        except Exception as e:
            out["parse_error"]=type(e).__name__+": "+str(e)[:400]
        return out
    if data[:4]==b"PK\x03\x04" or low.endswith(".xlsx"):
        out["file_type"]="xlsx"
        try:
            wb=openpyxl.load_workbook(io.BytesIO(data),data_only=True,read_only=True)
            sheets=[]
            for ws in wb.worksheets:
                rows=[]
                for row in ws.iter_rows(values_only=True):
                    vals=["" if v is None else str(v) for v in row]
                    if any(v.strip() for v in vals):rows.append(vals)
                    if len(rows)>=300:break
                sheets.append({"title":ws.title,"rows":rows})
            out["sheets"]=sheets
        except Exception as e:
            out["parse_error"]=type(e).__name__+": "+str(e)[:400]
        return out
    if data[:8]==bytes.fromhex("D0CF11E0A1B11AE1") or low.endswith(".xls"):
        out["file_type"]="xls"
        try:
            wb=xlrd.open_workbook(file_contents=data)
            sheets=[]
            for ws in wb.sheets():
                rows=[]
                for ri in range(min(ws.nrows,300)):
                    vals=[str(ws.cell_value(ri,ci)) for ci in range(ws.ncols)]
                    if any(v.strip() for v in vals):rows.append(vals)
                sheets.append({"title":ws.name,"rows":rows})
            out["sheets"]=sheets
        except Exception as e:
            out["parse_error"]=type(e).__name__+": "+str(e)[:400]
        return out
    # Try plain-text/CSV/TSV last.
    out["file_type"]="text"
    txt=data.decode("utf-8","replace")
    out["text"]=txt[:120000]
    delim="\t" if txt.count("\t")>txt.count(",") else ","
    try:
        rows=list(csv.reader(io.StringIO(txt),delimiter=delim))[:300]
        out["rows"]=rows
    except Exception:
        pass
    return out

async def main():
    records=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,accept_downloads=True)
        page=await browser.new_page(viewport={"width":1440,"height":1400})
        await page.goto(BASE,wait_until="networkidle",timeout=90000)
        links=await page.locator("a").evaluate_all(
          """els => els.map(a=>({
               href:a.href,
               text:(a.innerText||'').trim(),
               row_text:(a.closest('tr')?.innerText || a.parentElement?.parentElement?.innerText || '').trim()
             })).filter(x=>x.href.includes('/round-stats/'))"""
        )
        seen=set();links=[x for x in links if not (x["href"] in seen or seen.add(x["href"]))]
        for item in links[:20]:
            href=item["href"]
            rid=href.rstrip("/").split("/")[-1]
            rec={"source_url":href,"link_text":item.get("text"),"homepage_row_text":item.get("row_text"),"report_id":rid}
            try:
                # Keep navigation on the homepage and click the exact finite link
                # so Playwright receives the attachment/download event.
                loc=page.locator(f'a[href$="/round-stats/{rid}"]').first
                if await loc.count()==0:
                    raise RuntimeError("homepage round-stat link disappeared")
                async with page.expect_download(timeout=60000) as info:
                    await loc.click()
                dl=await info.value
                rec["download_url"]=dl.url
                rec["suggested_filename"]=dl.suggested_filename
                pth=await dl.path()
                if not pth:
                    raise RuntimeError("download path unavailable")
                data=Path(pth).read_bytes()
                rec["download"]=parse_download(data,dl.suggested_filename)
                d=rec["download"]
                text_blob=d.get("text","")
                if not text_blob and d.get("sheets"):
                    text_blob=" ".join(" ".join(" ".join(r) for r in s["rows"]) for s in d["sheets"])
                rec["has_total"]=bool(re.search(r"\btotal\b",text_blob,re.I))
                rec["has_jab"]=bool(re.search(r"\bjabs?\b",text_blob,re.I))
                rec["has_power"]=bool(re.search(r"\bpower\b",text_blob,re.I))
                rec["has_round"]=bool(re.search(r"\b(?:round|rd\.?)[ ]*\d+\b",text_blob,re.I))
                rec["usable_full_chart_candidate"]=bool(rec["has_total"] and rec["has_jab"] and rec["has_power"])
            except Exception as e:
                rec["error"]=type(e).__name__+": "+str(e)[:500]
            records.append(rec)
            if page.url!=BASE:
                await page.goto(BASE,wait_until="domcontentloaded",timeout=60000)
            await page.wait_for_timeout(300)
        await browser.close()
    report={
      "generated_at":datetime.now(timezone.utc).isoformat(),
      "homepage_links":len(links),
      "reports_attempted":len(records),
      "downloads_captured":sum("download" in x for x in records),
      "file_types":{t:sum((x.get("download") or {}).get("file_type")==t for x in records) for t in ("pdf","xlsx","xls","text")},
      "full_chart_candidates":sum(bool(x.get("usable_full_chart_candidate")) for x in records),
      "records":records,
      "status":"RESEARCH_SUPPLEMENT_ONLY",
      "policy":"Finite current homepage-linked CompuBox downloads only. No ID enumeration. Preserve as research supplement pending intended-use/licensing review."
    }
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps({k:report[k] for k in ("homepage_links","reports_attempted","downloads_captured","file_types","full_chart_candidates")},indent=2))

if __name__=="__main__":asyncio.run(main())
