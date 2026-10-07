#!/usr/bin/env python3
from pathlib import Path

pages=[
    Path("/srv/appwiza-sports/public/index.html"),
    Path("/srv/appwiza-sports/public/ufc/index.html"),
    Path("/srv/appwiza-sports/public/nfl/index.html"),
    Path("/srv/appwiza-sports/public/boxing/index.html"),
]

for p in pages:
    if not p.exists():
        continue
    s=p.read_text()
    if "/sports/nba/" not in s:
        if '<a href="/sports/nfl/"' in s:
            pos=s.find('<a href="/sports/nfl/"')
            end=s.find("</a>",pos)+4
            s=s[:end]+'<a href="/sports/nba/">NBA</a>'+s[end:]
        elif '<a href="/sports/ufc/"' in s:
            pos=s.find('<a href="/sports/ufc/"')
            end=s.find("</a>",pos)+4
            s=s[:end]+'<a href="/sports/nba/">NBA</a>'+s[end:]
    if p == Path("/srv/appwiza-sports/public/index.html") and "NBA picks" not in s and '<div class="sportlinks">' in s:
        s=s.replace('<div class="sportlinks">','<div class="sportlinks"><a href="/sports/nba/">NBA picks</a>',1)
    p.write_text(s)
