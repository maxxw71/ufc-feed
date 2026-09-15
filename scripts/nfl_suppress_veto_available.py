from __future__ import annotations

from pathlib import Path
import csv, os, re

PAGE = Path(os.environ.get('NFL_PUBLIC_PAGE','/srv/appwiza-sports/public/nfl/index.html'))
CONTEXT = Path(os.environ.get('NFL_BOARD_CONTEXT','nfl/live_2026_current_snapshot/current_board_fresh_context.csv'))


def as_bool(v):
    s=str(v).strip().lower()
    if s in {'true','1','yes','y'}: return True
    if s in {'false','0','no','n'}: return False
    return None


def blocked_rows(rows):
    out=[]
    for r in rows:
        status=str(r.get('target_status','')).upper()
        if status != 'UPCOMING' or as_bool(r.get('preseason_policy_pass')) is False:
            out.append(r)
    return out


def remove_card(segment: str, selection: str) -> tuple[str,int]:
    name=re.escape(selection.strip())
    # Current Appwiza publisher cards are article.card. Match the whole card only.
    patterns=[
        rf'(?is)<article\b[^>]*class=["\'][^"\']*\bcard\b[^"\']*["\'][^>]*>(?:(?!</article>).)*?{name}(?:\s+to\s+win)?(?:(?!</article>).)*?</article>',
        rf'(?is)<div\b[^>]*class=["\'][^"\']*\baudited-card\b[^"\']*["\'][^>]*>\s*<article\b[^>]*>(?:(?!</article>).)*?{name}(?:\s+to\s+win)?(?:(?!</article>).)*?</article>\s*</div>',
    ]
    total=0
    for pat in patterns:
        segment,n=re.subn(pat,'',segment)
        total += n
    return segment,total


def main():
    if not PAGE.exists(): raise SystemExit(f'missing page: {PAGE}')
    if not CONTEXT.exists(): raise SystemExit(f'missing context: {CONTEXT}')
    with CONTEXT.open(newline='',encoding='utf-8') as f:
        rows=list(csv.DictReader(f))
    blocked=blocked_rows(rows)
    html=PAGE.read_text()
    start_marker='<h2>Available selections</h2>'
    end_markers=['<div class="section-kicker">Tracked results</div>','<h2>Completed selections</h2>','<details class="downloads-block"']
    if start_marker not in html: raise SystemExit('Available selections anchor missing')
    start=html.index(start_marker)+len(start_marker)
    ends=[html.find(x,start) for x in end_markers if html.find(x,start)>=0]
    if not ends: raise SystemExit('Could not find end of available section')
    end=min(ends)
    before,segment,after=html[:start],html[start:end],html[end:]
    removed={}
    for r in blocked:
        sel=(r.get('selection') or r.get('team') or '').strip()
        if not sel: continue
        segment,n=remove_card(segment,sel)
        removed[sel]=n
    # Remove date groups left empty after suppressing their only card.
    segment=re.sub(r'(?is)<section\b[^>]*class=["\'][^"\']*\bdate-block\b[^"\']*["\'][^>]*>.*?<div\b[^>]*class=["\'][^"\']*\bupcoming-grid\b[^"\']*["\'][^>]*>\s*</div>\s*</section>','',segment)
    # Hard safety check: a veto/stale selection must not remain as a "to win" card in Available.
    leftovers=[]
    for r in blocked:
        sel=(r.get('selection') or r.get('team') or '').strip()
        if sel and re.search(re.escape(sel)+r'\s+to\s+win',segment,re.I): leftovers.append(sel)
    if leftovers:
        raise SystemExit('Blocked selections still present in Available: '+', '.join(leftovers))
    PAGE.write_text(before+segment+after)
    print('NFL_AVAILABLE_SUPPRESSION',{'blocked':[r.get('selection') or r.get('team') for r in blocked],'removed':removed,'page_bytes':PAGE.stat().st_size})

if __name__=='__main__':
    main()
