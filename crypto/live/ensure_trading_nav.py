#!/usr/bin/env python3
from pathlib import Path
import os,re

ROOT=Path("/srv/appwiza-sports/public/index.html")
if not ROOT.exists():
    raise SystemExit(0)

text=ROOT.read_text(encoding="utf-8",errors="ignore")
if 'href="/trading/' in text or "href='/trading/" in text:
    raise SystemExit(0)

anchor='<a href="/trading/">Trading</a>'
new=text
if "</nav>" in text:
    new=text.replace("</nav>",anchor+"</nav>",1)
elif "</header>" in text:
    new=text.replace("</header>",'<nav class="appwiza-trading-nav">'+anchor+"</nav></header>",1)
else:
    m=re.search(r"<body[^>]*>",text,re.I)
    if m:
        new=text[:m.end()]+'<div class="appwiza-trading-nav" style="padding:12px 20px"><a href="/trading/">Trading</a></div>'+text[m.end():]
if new!=text:
    tmp=ROOT.with_suffix(".html.trading.tmp")
    tmp.write_text(new,encoding="utf-8")
    os.chmod(tmp,ROOT.stat().st_mode & 0o777)
    os.replace(tmp,ROOT)
    print("trading_nav=inserted")
else:
    print("trading_nav=no_safe_anchor")
