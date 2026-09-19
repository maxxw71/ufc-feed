from pathlib import Path
import sys

mode=sys.argv[1].strip().lower()
p=Path(sys.argv[2])
s=p.read_text()
if mode=='nfl':
    marker='NFL_RENDER_ISOLATION_V1'
    anchor=" sports_publish.publish_nfl(records,now,nfl_email_design)"
    repl=" # NFL_RENDER_ISOLATION_V1\n sports_publish.render_all=lambda: None\n sports_publish.publish_nfl(records,now,nfl_email_design)"
elif mode=='ufc':
    marker='UFC_RENDER_ISOLATION_V1'
    anchor="    sports_publish.publish_ufc(events,preds_by_event,globals())"
    repl="    # UFC_RENDER_ISOLATION_V1\n    sports_publish.render_all=lambda: None\n    sports_publish.publish_ufc(events,preds_by_event,globals())"
else:
    raise SystemExit('mode must be nfl or ufc')

if marker in s:
    print(marker,'already installed')
    raise SystemExit(0)
if anchor not in s:
    raise SystemExit(f'{mode} publish anchor missing')
s=s.replace(anchor,repl,1)
p.write_text(s)
print(marker,'installed')
