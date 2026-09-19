from pathlib import Path
import sys

p=Path(sys.argv[1])
s=p.read_text()
marker='UFC_PUBLIC_PAGE_CONTRACT_GUARD_V1'
if marker in s:
    print('UFC page contract guard already installed')
    raise SystemExit(0)

old="""    canonical_now = canonical_ufc_all_picks(events,preds_by_event)
    sports_publish.safe_call(sports_publish.publish_ufc,events,preds_by_event,globals())
    # UFC_PUBLIC_DASHBOARD_HOOK
    try:
        import ufc_public_page
        ufc_public_page.safe_enhance(sports_publish)
    except Exception as exc:
        print("UFC_PUBLIC_DASHBOARD_HOOK_ERROR", type(exc).__name__, str(exc))

    validate_ufc_public_canonical(canonical_now)
"""
new="""    canonical_now = canonical_ufc_all_picks(events,preds_by_event)
    # UFC_PUBLIC_PAGE_CONTRACT_GUARD_V1
    import ufc_public_page
    required_page_contract='UFC_PUBLIC_CANONICAL_V2_20260919'
    actual_page_contract=getattr(ufc_public_page,'PAGE_CONTRACT',None)
    if actual_page_contract!=required_page_contract:
        raise RuntimeError(f'UFC public page contract mismatch before publish: {actual_page_contract!r} != {required_page_contract!r}')
    sports_publish.publish_ufc(events,preds_by_event,globals())
    ufc_public_page.safe_enhance(sports_publish)
    page_path=Path('/srv/appwiza-sports/public/ufc/index.html')
    page_text=page_path.read_text(errors='ignore') if page_path.exists() else ''
    required_page_markers=['Overall record','Current UFC audit','Available selections','Completed selections','Method Key · U1–U12']
    missing_page_markers=[x for x in required_page_markers if x not in page_text]
    if missing_page_markers:
        raise RuntimeError('UFC canonical page failed post-publish marker check: '+repr(missing_page_markers))

    validate_ufc_public_canonical(canonical_now)
"""
if old not in s:
    raise SystemExit('UFC publish/hook anchor missing')
s=s.replace(old,new,1)
p.write_text(s)
print('UFC page contract guard installed')
