from pathlib import Path
import sys

p=Path(sys.argv[1])
s=p.read_text()
marker="NFL_PUBLIC_PAGE_CONTRACT_GUARD_V1"
if marker in s:
    print('contract guard already installed')
    raise SystemExit(0)

anchor=" sports_publish.publish_nfl(records,now,nfl_email_design)\n nfl_public_page.safe_enhance(now,ledger,sports_publish)"
if anchor not in s:
    raise SystemExit('publish/enhance anchor missing')

guard=""" # NFL_PUBLIC_PAGE_CONTRACT_GUARD_V1
 required_page_contract='NFL_PUBLIC_CANONICAL_V2_20260919'
 actual_page_contract=getattr(nfl_public_page,'PAGE_CONTRACT',None)
 if actual_page_contract!=required_page_contract:
  raise RuntimeError(f'NFL public page contract mismatch before publish: {actual_page_contract!r} != {required_page_contract!r}')
 sports_publish.publish_nfl(records,now,nfl_email_design)
 nfl_public_page.safe_enhance(now,ledger,sports_publish)
 page_path=Path('/srv/appwiza-sports/public/nfl/index.html')
 page_text=page_path.read_text(errors='ignore') if page_path.exists() else ''
 required_page_markers=['Actionable now','2026 deep audit','Method Key · NFL','H-series research / shadow methods']
 missing_page_markers=[x for x in required_page_markers if x not in page_text]
 if missing_page_markers:
  raise RuntimeError('NFL canonical page failed post-publish marker check: '+repr(missing_page_markers))
"""
s=s.replace(anchor,guard,1)
p.write_text(s)
print('NFL page contract guard installed')
