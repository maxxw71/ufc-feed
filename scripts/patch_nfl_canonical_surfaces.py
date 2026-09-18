from pathlib import Path
import sys
p=Path(sys.argv[1])
s=p.read_text()
if 'CANONICAL_SELECTIONS_V1' in s:
    print('canonical selections patch already installed')
    raise SystemExit(0)

old=""" atomic(S/'production_match_rejections.json',{'scanned_at':now.isoformat(),'candidate_count':len(raw_production_records),'approved_count':len(records),'rejections':production_rejections})

 stamp=now.strftime('%Y%m%dT%H%M%S%fZ');atomic(S/(stamp+'.json'),{'observed_at':now.isoformat(),'rule_version':VERSION,'records':records,'schedule_sha256':hashlib.sha256(raw).hexdigest(),'late_method':late_status})
"""
new=""" atomic(S/'production_match_rejections.json',{'scanned_at':now.isoformat(),'candidate_count':len(raw_production_records),'approved_count':len(records),'rejections':production_rejections})

 # CANONICAL_SELECTIONS_V1
 # One approved list powers website, email, tracker, and the public machine-readable snapshot.
 next7=[r for r in records if datetime.fromisoformat(r['kickoff']).astimezone(timezone.utc) <= now+timedelta(days=7)]
 canonical={
   'updated_at':now.isoformat(),
   'rule_version':VERSION,
   'upcoming_total':len(records),
   'next_7_days':len(next7),
   'selections':[{
     'game_id':r['game_id'],'week':r['week'],'kickoff':r['kickoff'],
     'selection_code':r['selected_team'],
     'selection':nfl_email_design.name(r['selected_team']),
     'opponent_code':r['away'] if r['selection_side']=='home' else r['home'],
     'opponent':nfl_email_design.name(r['away'] if r['selection_side']=='home' else r['home']),
     'rules':r['rules'],'price':(r.get('odds') or {}).get('moneyline'),
     'book':(r.get('odds') or {}).get('bookmaker'),
     'quote_at':(r.get('odds') or {}).get('retrieved_at')
   } for r in records]
 }
 atomic(S/'canonical_selections.json',canonical)
 try:
  pub=Path('/srv/appwiza-sports/public/nfl/current.json')
  tmp=pub.with_suffix('.tmp');tmp.write_text(json.dumps(canonical,indent=2));tmp.chmod(0o644);tmp.replace(pub)
 except Exception:
  pass

 stamp=now.strftime('%Y%m%dT%H%M%S%fZ');atomic(S/(stamp+'.json'),{'observed_at':now.isoformat(),'rule_version':VERSION,'records':records,'schedule_sha256':hashlib.sha256(raw).hexdigest(),'late_method':late_status})
"""
if old not in s:
    raise SystemExit('canonical insertion anchor not found')
s=s.replace(old,new,1)

old2=""" sports_publish.publish_nfl(records,now,nfl_email_design)
 nfl_public_page.safe_enhance(now,ledger,sports_publish)
 atomic(ledgerpath,ledger);body,html_body=nfl_email_design.render(records,now)
 footer_text,footer_html=bet_tracker.footer();body+='\n\n'+footer_text;html_body=html_body.replace('</body>','<div style="max-width:640px;margin:0 auto;padding:0 10px;">'+footer_html+'</div></body>');(S/'preview.txt').write_text(body);(S/'preview.html').write_text(html_body)
 subject=f"NFL signals: {len(records)} qualifying games — {now.astimezone(NY).strftime('%b %d')}"
"""
new2=""" sports_publish.publish_nfl(records,now,nfl_email_design)
 nfl_public_page.safe_enhance(now,ledger,sports_publish)
 atomic(ledgerpath,ledger);body,html_body=nfl_email_design.render(records,now)
 old_header=f"NFL PICKS | {len(records)} qualifying games"
 new_header=f"NFL PICKS | {len(records)} upcoming qualifying games · {len(next7)} in next 7 days"
 body=body.replace(old_header,new_header,1)
 html_body=html_body.replace(f"{len(records)} qualifying games",f"{len(records)} upcoming qualifying games · {len(next7)} in next 7 days",1)
 footer_text,footer_html=bet_tracker.footer();body+='\n\n'+footer_text;html_body=html_body.replace('</body>','<div style="max-width:640px;margin:0 auto;padding:0 10px;">'+footer_html+'</div></body>');(S/'preview.txt').write_text(body);(S/'preview.html').write_text(html_body)
 subject=f"NFL signals: {len(records)} upcoming · {len(next7)} next 7 days — {now.astimezone(NY).strftime('%b %d')}"
"""
if old2 not in s:
    raise SystemExit('email header anchor not found')
s=s.replace(old2,new2,1)
p.write_text(s)
print('canonical selections + email count patch installed')
