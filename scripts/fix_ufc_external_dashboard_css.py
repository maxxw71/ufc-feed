#!/usr/bin/env python3
from pathlib import Path

p=Path('ufc/ufc_public_page.py')
s=p.read_text()

old_head='''<title>UFC watchlist | Appwiza</title><link rel="stylesheet" href="/sports/style.css"><style>{DASH_CSS}</style></head>'''
new_head='''<title>UFC watchlist | Appwiza</title><link rel="stylesheet" href="/sports/style.css"><link rel="stylesheet" href="/sports/ufc/dashboard.css?v={css_version}"></head>'''
if old_head in s:
    s=s.replace(old_head,new_head,1)
elif new_head not in s:
    raise SystemExit('UFC head CSS anchor missing')

needle="        withdrawn_html=''\n"
insert="        css_version=now.astimezone(NY).strftime('%Y%m%d%H%M')\n        withdrawn_html=''\n"
if 'css_version=now.astimezone(NY)' not in s:
    if needle not in s: raise SystemExit('css version insertion anchor missing')
    s=s.replace(needle,insert,1)

needle2="        atomic(PUBLIC/'index.html',page)\n"
insert2="        atomic(PUBLIC/'dashboard.css',DASH_CSS)\n        atomic(PUBLIC/'index.html',page)\n"
if "atomic(PUBLIC/'dashboard.css',DASH_CSS)" not in s:
    if needle2 not in s: raise SystemExit('dashboard css write anchor missing')
    s=s.replace(needle2,insert2,1)

p.write_text(s)
print('UFC external dashboard CSS delivery enabled')
