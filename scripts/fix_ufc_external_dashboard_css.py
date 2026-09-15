#!/usr/bin/env python3
from pathlib import Path

p=Path('ufc/ufc_public_page.py')
s=p.read_text()

old='<style>{DASH_CSS}</style>'
new='<link rel="stylesheet" href="/sports/ufc/dashboard.css?v={now.astimezone(NY).strftime(\"%Y%m%d%H%M\")}">'
if old in s:
    s=s.replace(old,new,1)
elif '/sports/ufc/dashboard.css?v=' not in s:
    raise SystemExit('UFC inline dashboard CSS anchor missing')

old_write="atomic(PUBLIC/'index.html',page);print("
new_write="atomic(PUBLIC/'dashboard.css',DASH_CSS);atomic(PUBLIC/'index.html',page);print("
if old_write in s:
    s=s.replace(old_write,new_write,1)
elif "atomic(PUBLIC/'dashboard.css',DASH_CSS)" not in s:
    raise SystemExit('UFC dashboard CSS write anchor missing')

p.write_text(s)
print('UFC external dashboard CSS delivery enabled')
