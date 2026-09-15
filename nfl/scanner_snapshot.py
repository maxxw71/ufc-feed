"""Server-only home-opener research digest; frozen rules, no bet execution."""
from pathlib import Path
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from urllib.request import Request,urlopen
from concurrent.futures import ThreadPoolExecutor
import json,io,os,shlex,hashlib,fcntl,argparse,subprocess
import pandas as pd
import requests
import polars as pl
import late_season_method as late
import nfl_email_design
import production_match_guard as match_guard
import sys
sys.path.insert(0,'/opt/sports-publisher')
import sports_publish
sys.path.insert(0,str(Path.home()/'betting-ledger'/'code'))
import bet_tracker
import public_feed_ipv6
public_feed_ipv6.enable()
R=Path(__file__).resolve().parent;S=R/'home_opener_email_state';NY=ZoneInfo('America/New_York')
ALIASES={'SD':'LAC','OAK':'LV','STL':'LA','LAR':'LA'}
NAMES={'LAC':'Chargers','ARI':'Cardinals','TB':'Buccaneers','CLE':'Browns','BAL':'Ravens','NO':'Saints','LV':'Raiders','PHI':'Eagles','WAS':'Commanders','MIN':'Vikings','GB':'Packers'}
VERSION='nfl-v3-late-road-protection-rest';BOOKS={'ESPN BET':0,'DraftKings':1,'Caesars Sportsbook':2,'Unibet':3,'FanDuel':4,'BetMGM':5,'bet365':6}
RULE_NAMES={'original':'Run defense + better record','stricter':'Run defense + record advantage >12.5pp','pass_defense_offense24':'Week 1 pass defense + pass rush + offense rank <=24'}
RULE_NAMES[late.RULE]='Late-season away favorite + protection/rest exclusion'
def atomic(path,data):
 tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,indent=2,allow_nan=False));tmp.chmod(0o600);tmp.replace(path)
def cfg():
 d={}
 for ln in Path('/home/anestishkurti92/.config/ufc-watcher.env').read_text().splitlines():
  k,sep,v=ln.removeprefix('export ').partition('=')
  if sep and k.strip() in ['RESEND_API_KEY','UFC_ALERT_EMAIL','UFC_ALERT_FROM']:
   z=shlex.split(v,comments=True);d[k.strip()]=z[0] if z else ''
 if not d.get('RESEND_API_KEY') or not d.get('UFC_ALERT_EMAIL'):raise RuntimeError('Missing existing email credentials')
 return d
def get(url,timeout=20):
 with urlopen(Request(url,headers={'User-Agent':'NFLHomeOpenerResearch/1.0'}),timeout=timeout) as f:return f.read()
def ranks_records(G,season):
 p=G[(G.season==season-1)&(G.game_type=='REG')].copy()
 if len(p)!=272 or p.home_score.isna().any() or p.away_score.isna().any():raise ValueError('Prior regular season incomplete; no signals sent')
 sides=pd.concat([p[['season','week','game_id','home_team','away_team','home_score','away_score']].rename(columns={'home_team':'team','away_team':'opponent','home_score':'points','away_score':'against'}),p[['season','week','game_id','home_team','away_team','home_score','away_score']].rename(columns={'away_team':'team','home_team':'opponent','away_score':'points','home_score':'against'})])
 records={}
 for t,z in sides.groupby('team'):
  w=int((z.points>z.against).sum());ties=int((z.points==z.against).sum());n=len(z)
  if n!=17:raise ValueError('Unexpected prior-season team count')
  records[t]={'wins':w,'losses':n-w-ties,'ties':ties,'pct':(w+.5*ties)/n}
 path=R/f'data/stats_team/stats_team_week_{season-1}.parquet'
 if not path.exists():
  path=S/f'stats_team_week_{season-1}.parquet'
  if not path.exists():path.write_bytes(get(f'https://github.com/nflverse/nflverse-data/releases/download/stats_team/stats_team_week_{season-1}.parquet',30))
 t=pd.read_parquet(path,columns=['season','week','season_type','team','rushing_epa','carries']);t=t[(t.season==season-1)&(t.season_type=='REG')].copy();t.team=t.team.replace(ALIASES)
 j=sides.merge(t,on=['season','week','team'],validate='one_to_one',how='left')
 if j.rushing_epa.isna().any() or j.carries.isna().any() or (j.carries<=0).any():raise ValueError('Incomplete prior rushing statistics')
 j['rate']=j.rushing_epa/j.carries;r=j.groupby('opponent').rate.mean().rank(method='average').to_dict()
 if len(r)!=32:raise ValueError('Cannot rank 32 teams')
 return records,r
def qualifies(rank,gap):return ['original','stricter'] if rank<=10 and gap>.125 else ['original'] if rank<=10 and gap>0 else []
def qualifies_pass_defense(week,pass_rank,hit_rank,offense_rank):
 return week==1 and pass_rank<=10 and hit_rank<=16 and offense_rank<=24
def pass_defense_profiles(G,season):
 prior=season-1;p=G[(G.season==prior)&G.game_type.eq('REG')]
 sides=pd.concat([p[['game_id','season','week','home_team','away_team']].rename(columns={'home_team':'team','away_team':'opponent'}),p[['game_id','season','week','away_team','home_team']].rename(columns={'away_team':'team','home_team':'opponent'})])
 path=R/f'data/stats_team/stats_team_week_{prior}.parquet'
 if not path.exists():path=S/f'stats_team_week_{prior}.parquet'
 t=pd.read_parquet(path,columns=['season','week','season_type','team','passing_epa','rushing_epa','attempts','sacks_suffered','carries']);t=t[t.season.eq(prior)&t.season_type.eq('REG')].copy();t.team=t.team.replace(ALIASES)
 j=sides.merge(t,on=['season','week','team'],how='left',validate='one_to_one')
 cols=['passing_epa','rushing_epa','attempts','sacks_suffered','carries']
 if len(j)!=544 or j[cols].isna().any().any():raise ValueError('Incomplete prior offense/pass-defense statistics')
 db=j.attempts+j.sacks_suffered
 if (db<=0).any() or (db+j.carries<=0).any():raise ValueError('Invalid play denominators')
 j['pass_rate']=j.passing_epa/db;j['offense_rate']=(j.passing_epa+j.rushing_epa)/(db+j.carries)
 paths=list((R/'data/pbp').glob(f'*{prior}*.parquet'))
 if len(paths)>1:raise ValueError('Ambiguous prior play-by-play file')
 pbpath=paths[0] if paths else S/f'play_by_play_{prior}.parquet'
 if not pbpath.exists():
  raw=get(f'https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{prior}.parquet',90)
  tmp=pbpath.with_suffix('.tmp');tmp.write_bytes(raw);tmp.replace(pbpath)
 q=pl.scan_parquet(pbpath).select(['game_id','posteam','epa','pass_attempt','sack','qb_hit']).filter(pl.col('posteam').is_not_null()&pl.col('epa').is_not_null()&(pl.col('pass_attempt')==1)).group_by(['game_id','posteam']).agg(((pl.col('sack')==1)|(pl.col('qb_hit')==1)).mean().alias('hit_rate')).collect().to_pandas().rename(columns={'posteam':'team'})
 q.team=q.team.replace(ALIASES);j=j.merge(q,on=['game_id','team'],how='left',validate='one_to_one')
 if j.hit_rate.isna().any() or not j.groupby('team').size().eq(17).all():raise ValueError('Incomplete prior sack/hit data')
 ranks=pd.DataFrame({'pass_rank':j.groupby('opponent').pass_rate.mean().rank(method='average'),'hit_rank':j.groupby('opponent').hit_rate.mean().rank(ascending=False,method='average'),'offense_rank':j.groupby('team').offense_rate.mean().rank(ascending=False,method='average')})
 if len(ranks)!=32 or ranks.isna().any().any():raise ValueError('Incomplete 32-team ranks')
 return ranks.to_dict('index')
def current_odds(rec):
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

def render(records,now):
 return nfl_email_design.render(records,now)[0]
def settlement_result(home,away,side='home'):
 margin=(away-home) if side=='away' else (home-away)
 return 'win' if margin>0 else 'loss' if margin<0 else 'push'
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--send',action='store_true');ap.add_argument('--manual',action='store_true');args=ap.parse_args();S.mkdir(exist_ok=True);os.chmod(S,0o700)
 lock=(S/'run.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);now=datetime.now(timezone.utc);bet_tracker.settle(now=now);season=now.astimezone(NY).year
 raw=get('https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv');G=pd.read_csv(io.BytesIO(raw))
 for c in ['home_team','away_team']:G[c]=G[c].replace(ALIASES)
 g=G[(G.season==season)&(G.game_type=='REG')&(G.location=='Home')].sort_values(['gameday','gametime','game_id']).drop_duplicates('home_team')
 records=[]
 if len(g):
  if len(g)!=32:raise ValueError('Incomplete current home-opener schedule')
  profiles,ranks=ranks_records(G,season);pass_profiles=pass_defense_profiles(G,season)
  for _,x in g.iterrows():
   if pd.notna(x.home_score) or pd.notna(x.away_score) or pd.isna(x.gametime):continue
   k=datetime.fromisoformat(x.gameday+'T'+x.gametime).replace(tzinfo=NY).astimezone(timezone.utc)
   if not now<k<=now+timedelta(days=21):continue
   h=profiles[x.home_team];a=profiles[x.away_team];gap=h['pct']-a['pct'];rules=qualifies(ranks[x.home_team],gap)
   f=pass_profiles[x.home_team]
   if qualifies_pass_defense(int(x.week),f['pass_rank'],f['hit_rank'],f['offense_rank']):rules.append('pass_defense_offense24')
   if rules:records.append(dict(game_id=x.game_id,season=season,home=x.home_team,away=x.away_team,selection_side='home',selected_team=x.home_team,home_record=h,away_record=a,rank=ranks[x.home_team],record_gap_pp=gap*100,rules=rules,pass_defense_profile=f,kickoff=k.isoformat(),week=int(x.week),espn=str(int(x.espn)),observed_at=now.isoformat(),rule_version=VERSION))
 with ThreadPoolExecutor(max_workers=4) as pool:
  for rec,o in zip(records,pool.map(current_odds,records)):rec['odds']=o
 late_records,late_status=late.scan(G,R,now,current_odds);records.extend(late_records)
 # Production boundary: clear the outbound list before validation so an exception
 # can never leak unvalidated candidates to Appwiza, email, or bet tracking.
 raw_production_records=list(records);records=[];production_rejections=[]
 try:
  records,production_rejections=match_guard.filter_records(raw_production_records,G,now)
 except Exception as e:
  production_rejections=[{'game_id':None,'reasons':['match_guard_exception:'+type(e).__name__]}]
 atomic(S/'production_match_rejections.json',{'scanned_at':now.isoformat(),'candidate_count':len(raw_production_records),'approved_count':len(records),'rejections':production_rejections})

 stamp=now.strftime('%Y%m%dT%H%M%S%fZ');atomic(S/(stamp+'.json'),{'observed_at':now.isoformat(),'rule_version':VERSION,'records':records,'schedule_sha256':hashlib.sha256(raw).hexdigest(),'late_method':late_status})
 ledgerpath=S/'ledger.json';ledger=json.loads(ledgerpath.read_text()) if ledgerpath.exists() else {}
 for rec in records:
  entry=ledger.setdefault(rec['game_id'],dict(rec,bet_placed=None,actual_stake=None,actual_odds=None))
  observations=entry.setdefault('method_first_observations',{})
  for rule in rec['rules']:observations.setdefault(rule,{'observed_at':now.isoformat(),'rule_version':VERSION,'pass_defense_profile':rec.get('pass_defense_profile'),'late_profile':rec.get('late_profile')})
 byid=G.set_index('game_id')
 for key,entry in ledger.items():
  if key in byid.index:
   x=byid.loc[key]
   if pd.notna(x.home_score) and pd.notna(x.away_score):entry['settlement']={'home_score':float(x.home_score),'away_score':float(x.away_score),'result':settlement_result(x.home_score,x.away_score,entry.get('selection_side','home')),'actual_profit':None}
 sports_publish.publish_nfl(records,now,nfl_email_design)
 atomic(ledgerpath,ledger);body,html_body=nfl_email_design.render(records,now)
 footer_text,footer_html=bet_tracker.footer();body+='\n\n'+footer_text;html_body=html_body.replace('</body>','<div style="max-width:640px;margin:0 auto;padding:0 10px;">'+footer_html+'</div></body>');(S/'preview.txt').write_text(body);(S/'preview.html').write_text(html_body)
 subject=f"NFL signals: {len(records)} qualifying games — {now.astimezone(NY).strftime('%b %d')}"
 status={'scanned_at':now.isoformat(),'matches':len(records),'mode':'manual' if args.manual else 'automatic','sent':False,'late_method':late_status}
 if args.send and records:
  c=cfg();slot=now.strftime('%Y%m%d')+'-'+str(now.hour//6);key='manual-'+now.strftime('%Y%m%dT%H%M') if args.manual else 'auto-'+slot
  ident=hashlib.sha256((VERSION+':'+key).encode()).hexdigest();sentpath=S/'sent.json';sent=json.loads(sentpath.read_text()) if sentpath.exists() else {}
  if ident not in sent:
   pending=S/('pending-'+ident+'.json')
   if pending.exists():pending.unlink() # invalidate any pre-validation cached envelope
   if pending.exists():mail=json.loads(pending.read_text())
   else:
    mail={'from':c.get('UFC_ALERT_FROM') or 'onboarding@resend.dev','to':[c['UFC_ALERT_EMAIL']],'subject':subject,'text':body,'html':html_body}
    bet_tracker.stage('NFL:'+ident,'NFL',bet_tracker.nfl_picks(records));atomic(pending,mail)
   bet_tracker.stage('NFL:'+ident,'NFL',[])
   req=Request('https://api.resend.com/emails',data=json.dumps(mail).encode(),headers={'Authorization':'Bearer '+c['RESEND_API_KEY'],'Content-Type':'application/json','Idempotency-Key':ident},method='POST')
   response=requests.post(req.full_url,data=req.data,headers=dict(req.header_items()),timeout=25)
   if response.status_code>=300:raise RuntimeError(f'Email provider {response.status_code}: {response.text[:600]}')
   res=response.json()
   if not res.get('id'):raise ValueError('Email provider did not return a message ID')
   bet_tracker.confirm('NFL:'+ident,res['id'])
   sent[ident]={'sent_at':now.isoformat(),'provider_id':res['id'],'mode':status['mode'],'games':[r['game_id'] for r in records]};atomic(sentpath,sent);status.update(sent=True,provider_id=res['id'])
  else:status['already_sent_for_scan_slot']=True
 atomic(S/'status.json',status);print(json.dumps(status))
if __name__=='__main__':main()
