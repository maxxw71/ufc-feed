#!/usr/bin/env python3
from pathlib import Path
import re

NFL=Path('nfl/nfl_public_page.py')
UFC=Path('ufc/ufc_public_page.py')

nfl=NFL.read_text()
ufc=UFC.read_text()

m=re.search(r"DASH_CSS\s*=\s*r'''(.*?)'''", nfl, re.S)
if not m:
    raise SystemExit('NFL DASH_CSS not found')
css=m.group(1)
ufc,new_count=re.subn(r"DASH_CSS\s*=\s*r'''.*?'''", "DASH_CSS=r'''"+css+"'''", ufc, count=1, flags=re.S)
if new_count!=1:
    raise SystemExit('UFC DASH_CSS replacement failed')

# Use the exact same shared card renderer NFL uses, so the DOM and base CSS match.
new_group='''def group_upcoming(upcoming,sp):
    if not upcoming:return '<div class="empty-compact">No current method-qualified UFC selections.</div>'
    groups={}
    for c in sorted(upcoming,key=lambda x:dt(x['start'])):groups.setdefault(dt(c['start']).astimezone(NY).date(),[]).append(c)
    out=[]
    for day,cards in groups.items():
        d=datetime.combine(day,datetime.min.time(),tzinfo=NY)
        out.append('<section class="date-block"><div class="date-heading"><h2>'+esc(d.strftime('%A, %B %d'))+'</h2><span>'+str(len(cards))+' selection'+('s' if len(cards)!=1 else '')+'</span></div><div class="grid upcoming-grid">'+''.join(sp.card_html(c) for c in cards)+'</div></section>')
    return ''.join(out)

'''
ufc,count=re.subn(r'def group_upcoming\(upcoming\):.*?(?=def results\(cards\):)',new_group,ufc,count=1,flags=re.S)
if count!=1:
    raise SystemExit('group_upcoming replacement failed')

ufc=ufc.replace('{group_upcoming(upcoming)}','{group_upcoming(upcoming,sp)}')
ufc=ufc.replace("''.join(card_html(c) for c in withdrawn)","''.join(sp.card_html(c) for c in withdrawn)")
ufc=ufc.replace('class="audit-badge">QUALIFIED','class="audit-badge keep">QUALIFIED')
ufc=ufc.replace('class="audit-badge">'+"'+str(len(upcoming))+' active",'class="audit-badge keep">'+"'+str(len(upcoming))+' active")

UFC.write_text(ufc)
print('synced UFC DASH_CSS and cards to NFL presentation layer')
