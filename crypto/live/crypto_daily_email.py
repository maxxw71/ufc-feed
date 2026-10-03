#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import importlib.util
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
import sys

SIGNALS=Path("/srv/appwiza-sports/public/trading/crypto/signals.json")
UFC_WATCHER=Path("/home/anestishkurti92/ufc-predictor-v1/ufc_email_watcher.py")
RAW_BASE="https://raw.githubusercontent.com/maxxw71/ufc-feed/main/crypto/research"
ET=ZoneInfo("America/New_York")

def jload(path):
    try:return json.loads(Path(path).read_text())
    except Exception:return {}

def remote_json(name):
    try:
        r=requests.get(f"{RAW_BASE}/{name}",timeout=20)
        if r.ok:return r.json()
    except Exception:pass
    return {}

def money(v):
    try:
        x=float(v)
        return ("\x24"+f"{x:.6f}").rstrip("0").rstrip(".")
    except Exception:return "—"

def pct(v):
    try:return f"{100*float(v):.1f}%"
    except Exception:return "—"

def dt(v):
    try:return datetime.fromisoformat(str(v).replace("Z","+00:00")).astimezone(ET).strftime("%b %d · %I:%M %p ET")
    except Exception:return "—"

def signal_rows(signals):
    rows=[]
    for s in signals:
        status=str(s.get("status") or "—")
        tone={"SUCCESS":"#0a7a4f","STOPPED":"#b42318","ACTIVE":"#126f91","RECENT":"#765b12"}.get(status,"#53657b")
        rows.append("<tr>"
            f"<td>{html.escape(dt(s.get('trigger_time')))}</td>"
            f"<td><strong>{html.escape(str(s.get('coin') or '—'))}</strong></td>"
            f"<td>{html.escape(str(s.get('method_id') or '—'))}</td>"
            f"<td>{html.escape(money(s.get('entry_reference')))}</td>"
            f"<td>{html.escape(money(s.get('target_5pct')))}</td>"
            f"<td style='font-weight:800;color:{tone}'>{html.escape(status)}</td>"
            "</tr>")
    return "".join(rows) or "<tr><td colspan='6' style='color:#66758a'>No current or recently settled signals.</td></tr>"

def research_html():
    status=remote_json("auto_discovery/status.json")
    registry=remote_json("auto_discovery/candidate_registry.json")
    cands=registry.get("candidates") or []
    cands=sorted(cands,key=lambda c:(c.get("status")=="CROSS_VALIDATED_SHADOW",(c.get("validation") or {}).get("hold_hit5") or 0,(c.get("validation") or {}).get("hold_n") or 0),reverse=True)
    rows=[]
    for c in cands[:8]:
        v=c.get("validation") or {}
        cv=c.get("cross_venue_validation") or {}
        rows.append("<tr>"
            f"<td><strong>{html.escape(str(c.get('candidate_id') or '—'))}</strong><br><span style='color:#66758a'>{html.escape(str(c.get('status') or 'SHADOW_ONLY'))}</span></td>"
            f"<td>{html.escape(str(c.get('segment') or '—'))}</td>"
            f"<td>{html.escape(str(c.get('rule') or '—'))}</td>"
            f"<td>{pct(v.get('hold_hit5'))}<br><span style='color:#66758a'>n={v.get('hold_n','—')}</span></td>"
            f"<td>{pct(cv.get('hit5')) if cv else '—'}<br><span style='color:#66758a'>n={cv.get('n','—') if cv else '—'}</span></td>"
            "</tr>")
    if not rows:
        rows=["<tr><td colspan='5' style='color:#66758a'>No new candidate passed the current research gates yet.</td></tr>"]
    head=(f"{status.get('candidate_count','—')} shadow candidates · {status.get('cross_validated_shadow_count','—')} cross-validated"
          if status else "Automatic research cycle is still building its first registry.")
    c3hl=remote_json("results_c3_search/best_candidate.json")
    c3bn=remote_json("results_c3_binance_validation/REPORT.json")
    featured=""
    if c3hl and c3bn:
        featured=(
            "<div style='background:#eef8f3;border:1px solid #cfe7d9;border-radius:12px;padding:14px;margin:10px 0 16px'>"
            "<strong>C3 shadow candidate · Volume Capitulation Flush</strong><br>"
            f"<span style='color:#53657b'>Hyperliquid holdout: {pct(c3hl.get('hold_hit5'))} +5% (n={c3hl.get('hold_n','—')}); "
            f"Binance untouched holdout: {pct(c3bn.get('hold_hit5'))} +5% (n={c3bn.get('hold_n','—')}); "
            f"Binance +5% before -7.5%: {pct(c3bn.get('hold_t5_s7p5'))}. Forward shadow only.</span></div>"
        )
    return head,featured+"".join(rows)

def build():
    data=jload(SIGNALS)
    sigs=data.get("signals") or []
    shadow_sigs=data.get("shadow_signals") or []
    methods={m.get("id"):m for m in data.get("methods") or []}
    context=jload("/srv/appwiza-sports/public/trading/crypto/context.json")
    ctx=context.get("summary") or {}
    research_head,research_rows=research_html()
    now=datetime.now(ET)
    subject=f"Crypto Trading — Daily Research & Signals · {now.strftime('%b %d')}"
    body=f"""<!doctype html><html><body style="margin:0;background:#f2f5f9;font-family:Arial,sans-serif;color:#172033">
    <div style="max-width:920px;margin:auto;padding:28px 18px">
      <div style="background:#0d1728;color:white;border-radius:16px;padding:24px">
        <div style="font-size:12px;letter-spacing:.1em;color:#69d5ff;font-weight:800">APPWIZA · TRADING · CRYPTO</div>
        <h1 style="margin:8px 0 6px;font-size:30px">Daily research & signal brief</h1>
        <div style="color:#a9b8cf">{html.escape(now.strftime('%A, %B %d, %Y · %I:%M %p ET'))}</div>
      </div>
      <h2 style="margin:28px 0 8px">Live methods</h2>
      <p><strong>C1 · Blow-Off First Flush</strong> — holdout +5% {pct((methods.get('C1') or {}).get('validation',{}).get('hit5'))}<br>
      <strong>C2 · Lower-High Second Dump</strong> — holdout +5% {pct((methods.get('C2') or {}).get('validation',{}).get('hit5'))}</p>
      <h2 style="margin:28px 0 8px">Market context</h2>
      <p style="color:#53657b">BTC 24h {pct(ctx.get('btc_ret24'))} · ETH 24h {pct(ctx.get('eth_ret24'))} ·
      Hyperliquid breadth positive {pct(ctx.get('breadth_positive_24h'))} ·
      ≥+5% {pct(ctx.get('breadth_up5_24h'))} · ≤-5% {pct(ctx.get('breadth_down5_24h'))}</p>
      <h2 style="margin:28px 0 8px">Active / recent / settled signals</h2>
      <table style="width:100%;border-collapse:collapse;background:white;border:1px solid #dbe3ec">
        <thead><tr style="background:#edf2f7"><th align="left" style="padding:10px">Trigger</th><th align="left" style="padding:10px">Pair</th><th align="left" style="padding:10px">Method</th><th align="left" style="padding:10px">Entry</th><th align="left" style="padding:10px">+5% target</th><th align="left" style="padding:10px">Status</th></tr></thead>
        <tbody>{signal_rows(sigs)}</tbody>
      </table>
      <h2 style="margin:28px 0 8px">Forward shadow signals</h2>
      <table style="width:100%;border-collapse:collapse;background:white;border:1px solid #dbe3ec">
        <thead><tr style="background:#edf2f7"><th align="left" style="padding:10px">Trigger</th><th align="left" style="padding:10px">Pair</th><th align="left" style="padding:10px">Method</th><th align="left" style="padding:10px">Entry</th><th align="left" style="padding:10px">+5% target</th><th align="left" style="padding:10px">Status</th></tr></thead>
        <tbody>{signal_rows(shadow_sigs)}</tbody>
      </table>
      <h2 style="margin:28px 0 8px">Automatic method research</h2>
      <p style="color:#53657b">{html.escape(research_head)}</p>
      <table style="width:100%;border-collapse:collapse;background:white;border:1px solid #dbe3ec">
        <thead><tr style="background:#edf2f7"><th align="left" style="padding:10px">Candidate</th><th align="left" style="padding:10px">Market</th><th align="left" style="padding:10px">Rule</th><th align="left" style="padding:10px">HL +5%</th><th align="left" style="padding:10px">Binance +5%</th></tr></thead>
        <tbody>{research_rows}</tbody>
      </table>
      <p style="margin-top:24px;color:#73849b;font-size:12px">Research candidates remain shadow-only until cross-venue validation, forward shadow performance and final review. Historical results do not guarantee future performance.</p>
    </div></body></html>"""
    return subject,body

def send(subject,body):
    watcher_dir=str(UFC_WATCHER.parent)
    if watcher_dir not in sys.path:
        sys.path.insert(0,watcher_dir)
    spec=importlib.util.spec_from_file_location("ufc_email_watcher",UFC_WATCHER)
    if not spec or not spec.loader:raise RuntimeError("UFC mailer module unavailable")
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    fn=getattr(mod,"send_email",None)
    if not callable(fn):raise RuntimeError("send_email interface unavailable")
    result=fn(subject,body)
    print("crypto_daily_email_sent",bool(result) if result is not None else "called")

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--dry-run",action="store_true")
    ap.add_argument("--preview",default="/home/anestishkurti92/crypto-trading/crypto_daily_email_preview.html")
    args=ap.parse_args()
    subject,body=build()
    if args.dry_run:
        Path(args.preview).write_text(body)
        print("subject",subject)
        print("preview",args.preview)
    else:
        send(subject,body)
