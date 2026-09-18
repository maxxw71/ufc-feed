from pathlib import Path
import sys

p=Path(sys.argv[1])
s=p.read_text()

if 'CANONICAL_SELECTIONS_V1' not in s:
    anchor=" atomic(S/'production_match_rejections.json',{'scanned_at':now.isoformat(),'candidate_count':len(raw_production_records),'approved_count':len(records),'rejections':production_rejections})"
    if anchor not in s:
        raise SystemExit('production rejection anchor not found')
    addon=r"""
 # CANONICAL_SELECTIONS_V1
 # The exact same approved records list feeds website, email, tracker and this snapshot.
 next7=[r for r in records if datetime.fromisoformat(r['kickoff']).astimezone(timezone.utc) <= now+timedelta(days=7)]
 canonical={
   'updated_at':now.isoformat(),
   'rule_version':VERSION,
   'upcoming_total':len(records),
   'next_7_days':len(next7),
   'selections':[{
     'game_id':r['game_id'],
     'week':r['week'],
     'kickoff':r['kickoff'],
     'selection_code':r['selected_team'],
     'selection':nfl_email_design.name(r['selected_team']),
     'opponent_code':r['away'] if r['selection_side']=='home' else r['home'],
     'opponent':nfl_email_design.name(r['away'] if r['selection_side']=='home' else r['home']),
     'rules':r['rules'],
     'price':(r.get('odds') or {}).get('moneyline'),
     'book':(r.get('odds') or {}).get('bookmaker'),
     'quote_at':(r.get('odds') or {}).get('retrieved_at')
   } for r in records]
 }
 atomic(S/'canonical_selections.json',canonical)
 try:
  pub=Path('/srv/appwiza-sports/public/nfl/current.json')
  tmp=pub.with_suffix('.tmp')
  tmp.write_text(json.dumps(canonical,indent=2))
  tmp.chmod(0o644)
  tmp.replace(pub)
 except Exception:
  pass
"""
    s=s.replace(anchor,anchor+"\n"+addon,1)

render_anchor=" atomic(ledgerpath,ledger);body,html_body=nfl_email_design.render(records,now)"
if 'upcoming qualifying games ·' not in s:
    if render_anchor not in s:
        raise SystemExit('render anchor not found')
    render_add=r"""
 old_header=f"NFL PICKS | {len(records)} qualifying games"
 new_header=f"NFL PICKS | {len(records)} upcoming qualifying games · {len(next7)} in next 7 days"
 body=body.replace(old_header,new_header,1)
 html_body=html_body.replace(f"{len(records)} qualifying games",f"{len(records)} upcoming qualifying games · {len(next7)} in next 7 days",1)
"""
    s=s.replace(render_anchor,render_anchor+"\n"+render_add,1)

subject_old=" subject=f\"NFL signals: {len(records)} qualifying games — {now.astimezone(NY).strftime('%b %d')}\""
subject_new=" subject=f\"NFL signals: {len(records)} upcoming · {len(next7)} next 7 days — {now.astimezone(NY).strftime('%b %d')}\""
if subject_old in s:
    s=s.replace(subject_old,subject_new,1)
elif 'next 7 days' not in s:
    raise SystemExit('subject anchor not found')

p.write_text(s)
print('canonical NFL surfaces patch installed')
