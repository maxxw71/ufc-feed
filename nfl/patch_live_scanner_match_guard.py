"""Patch the confirmed production NFL scanner to enforce production_match_guard.

This operates on a copy first; deployment workflows install the copy atomically only
after compilation/tests pass.
"""
from __future__ import annotations

from pathlib import Path
import sys


def patch(text: str) -> str:
    import_anchor = "import nfl_email_design\n"
    if "import production_match_guard as match_guard" not in text:
        if import_anchor not in text:
            raise RuntimeError("nfl_email_design import anchor missing")
        text = text.replace(import_anchor, import_anchor + "import production_match_guard as match_guard\n", 1)

    # Home-opener methods are intentionally home selections, but that identity must
    # be explicit. Never rely on a renderer/odds function defaulting to home.
    old = "home=x.home_team,away=x.away_team,home_record=h"
    new = "home=x.home_team,away=x.away_team,selection_side='home',selected_team=x.home_team,home_record=h"
    if old in text:
        text = text.replace(old, new, 1)
    elif new not in text:
        raise RuntimeError("home-opener record identity anchor missing")

    # Replace the odds function so it refuses ambiguous selection identity and tags
    # every quote with the exact side/team that was requested.
    start = text.find("def current_odds(rec):")
    end = text.find("\ndef render(records,now):", start)
    if start < 0 or end < 0:
        raise RuntimeError("current_odds function anchors missing")
    replacement = '''def current_odds(rec):
 side=rec.get('selection_side');selected=rec.get('selected_team');event=str(rec.get('espn') or '')
 url=f"https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/events/{event}/competitions/{event}/odds"
 if side not in ('home','away') or not selected or selected!=rec.get(side):
  return {'moneyline':None,'status':'Invalid selection identity','error_type':'SelectionIdentityError','source':url,'market_side':side,'market_team':selected}
 try:
  response=requests.get(url,timeout=(3,8));response.raise_for_status();raw=response.content;items=json.loads(raw).get('items',[]);options=[]
  key='awayTeamOdds' if side=='away' else 'homeTeamOdds'
  for item in items:
   name=item.get('provider',{}).get('name')
   if name not in BOOKS:continue
   q=((item.get(key) or {}).get('current') or {}).get('moneyLine') or {}
   try:price=float(q['american'])
   except (KeyError,ValueError,TypeError):continue
   if abs(price)>=100:options.append((BOOKS[name],name,price))
  if not options:raise ValueError('No explicit current bookmaker moneyline')
  _,name,price=sorted(options)[0]
  return {'bookmaker':name,'moneyline':price,'retrieved_at':datetime.now(timezone.utc).isoformat(),'source':url,'market_side':side,'market_team':selected,'raw':json.loads(raw)}
 except Exception as e:return {'moneyline':None,'status':'Live odds unavailable','error_type':type(e).__name__,'source':url,'market_side':side,'market_team':selected}
'''
    text = text[:start] + replacement + text[end:]

    marker = "late_records,late_status=late.scan(G,R,now,current_odds);records.extend(late_records)"
    gate_marker = "match_guard.filter_records(raw_production_records,G,now)"
    if gate_marker not in text:
        if marker not in text:
            raise RuntimeError("records finalization anchor missing")
        gate = """
 # Production boundary: clear the outbound list before validation so an exception
 # can never leak unvalidated candidates to Appwiza, email, or bet tracking.
 raw_production_records=list(records);records=[];production_rejections=[]
 try:
  records,production_rejections=match_guard.filter_records(raw_production_records,G,now)
 except Exception as e:
  production_rejections=[{'game_id':None,'reasons':['match_guard_exception:'+type(e).__name__]}]
 atomic(S/'production_match_rejections.json',{'scanned_at':now.isoformat(),'candidate_count':len(raw_production_records),'approved_count':len(records),'rejections':production_rejections})
"""
        text = text.replace(marker, marker + gate, 1)

    # Guard must be upstream of every persistent/public output and betting stage.
    gate_pos = text.index(gate_marker)
    required_after = [
        "atomic(S/(stamp+'.json')",
        "ledgerpath=S/'ledger.json'",
        "sports_publish.safe_call(sports_publish.publish_nfl,records,now,nfl_email_design)",
        "nfl_email_design.render(records,now)",
        "bet_tracker.nfl_picks(records)",
    ]
    for needle in required_after:
        pos = text.find(needle, gate_pos)
        if pos < 0:
            raise RuntimeError(f"guard ordering failed for {needle}")

    return text


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_live_scanner_match_guard.py SCANNER_COPY")
    path = Path(sys.argv[1])
    original = path.read_text()
    patched = patch(original)
    path.write_text(patched)
    print("NFL live scanner matchup integrity patch: PASS")


if __name__ == "__main__":
    main()
