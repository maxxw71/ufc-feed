from pathlib import Path
import sys

p=Path(sys.argv[1])
s=p.read_text()

if 'UFC_CANONICAL_SURFACE_V1' not in s:
    marker='if __name__ == "__main__":\n    main()'
    if marker not in s:
        raise SystemExit('final main marker not found')

    addon=r'''
# UFC_CANONICAL_SURFACE_V1
# Final production boundary for UFC selections.
# The exact method-card selections shown on Appwiza are also the selections
# staged in email/tracker. Internal compatibility labels never leave this gate.
_orig_ufc_picks_canonical=bet_tracker.ufc_picks
# UFC_EXTREME_PRICE_NO_BET_V1
UFC_NO_BET_AMERICAN_FAVORITE_CEILING=-2000
UFC_EXTREME_PRICE_NO_BET={}

def _ufc_method_sort(mid):
    import re as _re
    m=_re.match(r'^U(\d+)$',str(mid or ''))
    return (0,int(m.group(1))) if m else (1,str(mid or ''))

def canonical_ufc_picks(event,preds):
    base=list(_orig_ufc_picks_canonical(event,preds or []))
    by_selection={}
    for p0 in preds or []:
        cards=list(ufc_email_design.method_cards(p0,globals()))
        fav_groups={}
        for card in cards:
            fav=str(card.get('favorite') or '').strip()
            mid=str(card.get('id') or '').strip()
            if fav and mid:
                fav_groups.setdefault(fav,[]).append(card)
        if len(fav_groups)>1:
            raise RuntimeError('Conflicting canonical UFC favorites in one fight: '+repr(sorted(fav_groups)))
        for fav,cards0 in fav_groups.items():
            ids=[];titles={}
            for card in cards0:
                mid=str(card.get('id') or '').strip()
                if mid and mid not in ids:
                    ids.append(mid);titles[mid]=str(card.get('title') or mid)
            ids=sorted(ids,key=_ufc_method_sort)
            by_selection[fav]={'methods':ids,'method_titles':titles}

    base_by_selection={}
    for pick in base:
        sel=str(pick.get('selection') or '').strip()
        if not sel: continue
        if sel in base_by_selection:
            raise RuntimeError('Duplicate tracker pick for canonical UFC selection: '+sel)
        base_by_selection[sel]=pick

    out=[]
    missing=[]
    for sel,meta in by_selection.items():
        pick=base_by_selection.get(sel)
        if pick is None:
            missing.append(sel);continue
        q=dict(pick)
        q['methods']=list(meta['methods'])
        q['method_titles']=dict(meta['method_titles'])
        q['canonical_method_ids']=list(meta['methods'])
        try:
            _price=float(q.get('price'))
        except Exception:
            _price=None
        if _price is not None and _price<=UFC_NO_BET_AMERICAN_FAVORITE_CEILING:
            q['no_bet']=True
            q['no_bet_reason']='EXTREME_FAVORITE_PRICE'
            q['no_bet_threshold']=UFC_NO_BET_AMERICAN_FAVORITE_CEILING
            UFC_EXTREME_PRICE_NO_BET[str(q.get('key') or sel)]=q
            continue
        out.append(q)
    if missing:
        raise RuntimeError('Canonical UFC method-card selection missing tracker identity: '+', '.join(sorted(missing)))
    out.sort(key=lambda x:(str(x.get('start') or ''),str(x.get('key') or '')))
    return out

bet_tracker.ufc_picks=canonical_ufc_picks

def canonical_ufc_all_picks(events,preds_by_event):
    out=[]
    seen=set()
    for e in events or []:
        ek=event_key(e)
        if ek not in preds_by_event: continue
        for pick in bet_tracker.ufc_picks(e,preds_by_event[ek]):
            key=str(pick.get('key') or '')
            if not key: raise RuntimeError('Canonical UFC pick missing key')
            if key in seen: raise RuntimeError('Duplicate canonical UFC key: '+key)
            seen.add(key);out.append(pick)
    out.sort(key=lambda x:(str(x.get('start') or ''),str(x.get('key') or '')))
    return out

def _canonical_ufc_public_map(now=None):
    from pathlib import Path as _Path
    import json as _json
    now=(now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    path=_Path('/srv/appwiza-sports/state/ufc.json')
    if not path.exists(): raise RuntimeError('Missing UFC public state')
    data=_json.loads(path.read_text())
    out={}
    for card in data.get('cards',[]):
        try: start=datetime.fromisoformat(str(card.get('start')).replace('Z','+00:00')).astimezone(timezone.utc)
        except Exception: continue
        if start<=now or card.get('withdrawn'): continue
        key=str(card.get('id') or '')
        mids=sorted([str(m.get('id')) for m in card.get('methods',[]) if m.get('id')],key=_ufc_method_sort)
        out[key]={'selection':str(card.get('selection') or ''),'opponent':str(card.get('opponent') or ''),
                  'methods':mids,'price':card.get('price'),'book':card.get('book')}
    return out

def validate_ufc_public_canonical(picks,now=None):
    canon={}
    for q in picks:
        key=str(q.get('key') or '')
        canon[key]={'selection':str(q.get('selection') or ''),'opponent':str(q.get('opponent') or ''),
                    'methods':sorted([str(x) for x in q.get('methods',[])],key=_ufc_method_sort)}
    public=_canonical_ufc_public_map(now)
    if set(canon)!=set(public):
        raise RuntimeError('UFC canonical/public selection mismatch: canonical_only='+repr(sorted(set(canon)-set(public)))+
                           ' public_only='+repr(sorted(set(public)-set(canon))))
    bad=[]
    for key in sorted(canon):
        for field in ('selection','opponent','methods'):
            if canon[key][field]!=public[key][field]:
                bad.append((key,field,canon[key][field],public[key][field]))
    if bad: raise RuntimeError('UFC canonical/public method mismatch: '+repr(bad))
    return True

def write_ufc_canonical(picks,now=None):
    from pathlib import Path as _Path
    import json as _json
    now=(now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    payload={
      'updated_at':now.isoformat(),
      'selection_count':len(picks),
      'no_bet_extreme_price_count':len(UFC_EXTREME_PRICE_NO_BET),
      'no_bet_extreme_price':[{
        'id':q.get('key'),'event':q.get('event'),'event_date':q.get('event_date'),'start':q.get('start'),
        'selection':q.get('selection'),'opponent':q.get('opponent'),
        'methods':list(q.get('methods') or []),'price':q.get('price'),'book':q.get('book'),
        'reason':q.get('no_bet_reason'),'threshold':q.get('no_bet_threshold')
      } for q in UFC_EXTREME_PRICE_NO_BET.values()],
      'selections':[{
        'id':q.get('key'),'event':q.get('event'),'event_date':q.get('event_date'),'start':q.get('start'),
        'selection':q.get('selection'),'opponent':q.get('opponent'),
        'methods':list(q.get('methods') or []),'method_titles':q.get('method_titles') or {},
        'price':q.get('price'),'book':q.get('book'),
        'quote_observed_at':q.get('quote_observed_at'),'quote_source':q.get('quote_source'),
        'event_source':q.get('event_source')
      } for q in picks]
    }
    local=ROOT/'canonical_selections.json'
    tmp=local.with_suffix('.tmp');tmp.write_text(_json.dumps(payload,indent=2,default=str));tmp.chmod(0o600);tmp.replace(local)
    public=_Path('/srv/appwiza-sports/public/ufc/current.json')
    public.parent.mkdir(parents=True,exist_ok=True)
    tmp=public.with_suffix('.tmp');tmp.write_text(_json.dumps(payload,indent=2,default=str));tmp.chmod(0o644);tmp.replace(public)
    return payload
'''
    s=s.replace(marker,addon+'\n'+marker,1)

# Upgrade an already-installed canonical boundary with the extreme-price no-bet rule.
if 'UFC_CANONICAL_SURFACE_V1' in s and 'UFC_EXTREME_PRICE_NO_BET_V1' not in s:
    old="_orig_ufc_picks_canonical=bet_tracker.ufc_picks\n\ndef _ufc_method_sort(mid):"
    new="_orig_ufc_picks_canonical=bet_tracker.ufc_picks\n# UFC_EXTREME_PRICE_NO_BET_V1\nUFC_NO_BET_AMERICAN_FAVORITE_CEILING=-2000\nUFC_EXTREME_PRICE_NO_BET={}\n\ndef _ufc_method_sort(mid):"
    if old not in s:
        raise SystemExit('existing canonical boundary anchor missing for extreme-price upgrade')
    s=s.replace(old,new,1)

    old="""        q['methods']=list(meta['methods'])
        q['method_titles']=dict(meta['method_titles'])
        q['canonical_method_ids']=list(meta['methods'])
        out.append(q)"""
    new="""        q['methods']=list(meta['methods'])
        q['method_titles']=dict(meta['method_titles'])
        q['canonical_method_ids']=list(meta['methods'])
        try:
            _price=float(q.get('price'))
        except Exception:
            _price=None
        if _price is not None and _price<=UFC_NO_BET_AMERICAN_FAVORITE_CEILING:
            q['no_bet']=True
            q['no_bet_reason']='EXTREME_FAVORITE_PRICE'
            q['no_bet_threshold']=UFC_NO_BET_AMERICAN_FAVORITE_CEILING
            UFC_EXTREME_PRICE_NO_BET[str(q.get('key') or sel)]=q
            continue
        out.append(q)"""
    if old not in s:
        raise SystemExit('existing canonical pick append anchor missing for extreme-price upgrade')
    s=s.replace(old,new,1)

    old="      'selection_count':len(picks),\n      'selections':[{"
    new="""      'selection_count':len(picks),
      'no_bet_extreme_price_count':len(UFC_EXTREME_PRICE_NO_BET),
      'no_bet_extreme_price':[{
        'id':q.get('key'),'event':q.get('event'),'event_date':q.get('event_date'),'start':q.get('start'),
        'selection':q.get('selection'),'opponent':q.get('opponent'),
        'methods':list(q.get('methods') or []),'price':q.get('price'),'book':q.get('book'),
        'reason':q.get('no_bet_reason'),'threshold':q.get('no_bet_threshold')
      } for q in UFC_EXTREME_PRICE_NO_BET.values()],
      'selections':[{"""
    if old not in s:
        raise SystemExit('existing canonical snapshot anchor missing for extreme-price upgrade')
    s=s.replace(old,new,1)

# Build canonical selection list before publishing so conflicts fail closed.
anchor='''    sports_publish.safe_call(sports_publish.publish_ufc,events,preds_by_event,globals())
    # UFC_PUBLIC_DASHBOARD_HOOK'''
replacement='''    canonical_now = canonical_ufc_all_picks(events,preds_by_event)
    sports_publish.safe_call(sports_publish.publish_ufc,events,preds_by_event,globals())
    # UFC_PUBLIC_DASHBOARD_HOOK'''
if anchor in s:
    s=s.replace(anchor,replacement,1)
elif 'canonical_now = canonical_ufc_all_picks(events,preds_by_event)' not in s:
    raise SystemExit('publish anchor not found')

# Validate website and write a machine-readable canonical snapshot before any email.
anchor2='''    except Exception as exc:
        print("UFC_PUBLIC_DASHBOARD_HOOK_ERROR", type(exc).__name__, str(exc))

    if force_initial:'''
replacement2='''    except Exception as exc:
        print("UFC_PUBLIC_DASHBOARD_HOOK_ERROR", type(exc).__name__, str(exc))

    validate_ufc_public_canonical(canonical_now)
    write_ufc_canonical(canonical_now)
    canonical_event_keys={str(q.get('key') or '').split(':')[1] for q in canonical_now if len(str(q.get('key') or '').split(':'))>2}
    canonical_events=[e for e in events if event_key(e) in canonical_event_keys]
    canonical_blocks=[event_block(e,preds_by_event[event_key(e)]) for e in canonical_events if event_key(e) in preds_by_event]

    if force_initial:'''
if anchor2 in s:
    s=s.replace(anchor2,replacement2,1)
elif 'validate_ufc_public_canonical(canonical_now)' not in s:
    raise SystemExit('dashboard anchor not found')

# Force-initial digest uses the same canonical picks as site/tracker.
old='''            [event_block(e,preds_by_event[event_key(e)]) for e in events if event_key(e) in preds_by_event],
            feed,bundle,bet_picks=[p for e in events if event_key(e) in preds_by_event for p in bet_tracker.ufc_picks(e,preds_by_event[event_key(e)])]
        )
        send_email("UFC Model — Initial Upcoming Fight Digest", body)'''
new='''            canonical_blocks,
            feed,bundle,bet_picks=canonical_now
        )
        send_email(f"UFC Model — Initial Upcoming Fight Digest — {len(canonical_now)} qualifying selections", body)'''
if old in s:s=s.replace(old,new,1)

# Any changed-event email now carries the entire current qualifying slate.
old='''        if changed_events:
            title = "UFC Model — Fight / Odds / Hybrid Update"
            body = shell(title,[event_block(e,preds_by_event[event_key(e)]) for e in changed_events],feed,bundle,bet_picks=[p for e in changed_events for p in bet_tracker.ufc_picks(e,preds_by_event[event_key(e)])])
            send_email(title,body)'''
new='''        if changed_events:
            title = f"UFC Model — Current Qualifying Slate Update — {len(canonical_now)} selections"
            body = shell(title,canonical_blocks,feed,bundle,bet_picks=canonical_now)
            send_email(title,body)'''
if old in s:s=s.replace(old,new,1)
elif 'Current Qualifying Slate Update' not in s:raise SystemExit('changed-event email anchor missing')

# Day-before messages also include the full current canonical slate.
old='''        for e in day_before:
            body = shell("UFC Model — Tomorrow's Card",[event_block(e,preds_by_event[event_key(e)])],feed,bundle,bet_picks=bet_tracker.ufc_picks(e,preds_by_event[event_key(e)]))
            send_email(f"UFC Model — Tomorrow: {e.get('name','UFC Event')}",body)
            state["day_before_sent"][event_key(e)] = datetime.now(timezone.utc).isoformat()'''
new='''        for e in day_before:
            title=f"UFC Model — Tomorrow: {e.get('name','UFC Event')} + Current Qualifying Slate ({len(canonical_now)})"
            body = shell(title,canonical_blocks,feed,bundle,bet_picks=canonical_now)
            send_email(title,body)
            state["day_before_sent"][event_key(e)] = datetime.now(timezone.utc).isoformat()'''
if old in s:s=s.replace(old,new,1)
elif '+ Current Qualifying Slate' not in s:raise SystemExit('day-before email anchor missing')

p.write_text(s)
print('UFC canonical site/email/tracker boundary installed')
