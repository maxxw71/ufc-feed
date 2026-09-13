"""Export only explicitly scheduled future bouts; never infer schedules from results."""
import argparse,json,sqlite3,re,hashlib
from datetime import datetime,timezone,date
from pathlib import Path
def build(db,now):
 db.row_factory=sqlite3.Row;events={};excluded=0
 for r in db.execute('SELECT source,source_id,date,boxer_a,boxer_b,winner,status,division,venue,url FROM bouts'):
  try:day=date.fromisoformat(r['date'])
  except (ValueError,TypeError):continue
  if day<=now.date():continue # Date-only records cannot certify that today's bout is still upcoming.
  status=(r['status'] or '').strip().lower()
  if status not in {'scheduled','upcoming','announced'} or (r['winner'] or '').strip():excluded+=1;continue
  a,b=(r['boxer_a'] or '').strip(),(r['boxer_b'] or '').strip()
  if not a or not b or a.lower() in {'tba','tbd'} or b.lower() in {'tba','tbd'}:excluded+=1;continue
  key=day.isoformat()+':'+':'.join(sorted(re.sub(r'\W+','',n.lower()) for n in [a,b]))
  event=events.setdefault(key,{'id':hashlib.sha256(key.encode()).hexdigest()[:20],'date':day.isoformat(),'fighter_a':a,'fighter_b':b,'status':'announced','division':r['division'] or None,'venue':r['venue'] or None,'sources':[],'qualifying_methods':[]})
  event['sources'].append({'name':r['source'],'id':r['source_id'],'url':r['url']})
 rows=sorted(events.values(),key=lambda x:(x['date'],x['id']))
 return {'schema_version':1,'sport':'boxing','generated_at':now.isoformat(),'feed_type':'schedule_not_betting_picks','coverage_status':'partial' if rows else 'no_verified_future_bouts_in_current_archive','events':rows,'excluded_future_records':excluded,'notes':['Only explicit scheduled/announced future bouts are exported.','Empty events means the archive lacks verified future records, not that no fights are scheduled.','No boxing method has been promoted to live picks. Dates and source links do not imply verified odds.']}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--database',type=Path,default=Path(__file__).with_name('boxing.sqlite3'));ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
 with sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro',uri=True,timeout=120) as db:
  data=build(db,datetime.now(timezone.utc))
 args.output.parent.mkdir(parents=True,exist_ok=True);tmp=args.output.with_suffix('.tmp');tmp.write_text(json.dumps(data,indent=2));tmp.chmod(0o644);tmp.replace(args.output)
 print('Exported',len(data['events']),'future bouts; coverage:',data['coverage_status'])
if __name__=='__main__':main()
