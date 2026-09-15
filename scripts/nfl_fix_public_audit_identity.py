#!/usr/bin/env python3
from pathlib import Path
import sys

p = Path(sys.argv[1] if len(sys.argv) > 1 else 'nfl/nfl_public_page.py')
s = p.read_text()
old = '''def _row_for_card(card, amap):
    candidates=[str(card.get('selection','')).strip().lower()]
    fixture=str(card.get('fixture','')).lower()
    for k,r in amap.items():
        if k and (k in candidates or (len(k)>=3 and re.search(r'(?<![a-z])'+re.escape(k)+r'(?![a-z])',fixture))):
            return r
    return amap.get(candidates[0]) if candidates else None
'''
new = '''def _row_for_card(card, amap):
    selected=str(card.get('selection') or '').strip().lower()
    if not selected:return None
    if selected in amap:return amap[selected]
    seen=set()
    for r in amap.values():
        marker=id(r)
        if marker in seen:continue
        seen.add(marker)
        labels=[str(r.get('selection') or '').strip().lower(),str(r.get('team') or '').strip().lower()]
        for label in labels:
            if label and (selected==label or (len(label)>=3 and re.search(r'(?<![a-z])'+re.escape(label)+r'(?![a-z])',selected))):
                return r
    return None
'''
if old in s:
    p.write_text(s.replace(old, new, 1))
    print('NFL_AUDIT_IDENTITY patched')
elif new in s:
    print('NFL_AUDIT_IDENTITY already-patched')
else:
    raise SystemExit('NFL audit identity matcher anchor missing')
