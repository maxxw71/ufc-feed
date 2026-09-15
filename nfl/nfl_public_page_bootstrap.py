"""One-time formatter for the current public NFL HTML.

Used only to bridge the page immediately. The daily scanner now calls
nfl_public_page.safe_enhance() persistently after each future publish.
"""
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from html import escape as esc
from urllib.request import Request, urlopen
import io, re, sys
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parent))
from nfl_public_page import DASH_CSS

PUBLIC=Path('/srv/appwiza-sports/public/nfl')
PAGE=PUBLIC/'index.html'
NY=ZoneInfo('America/New_York')
ALIASES={'SD':'LAC','OAK':'LV','STL':'LA','LAR':'LA'}
DISPLAY_TO_TEAM={
 'Cardinals':'ARI','Falcons':'ATL','Ravens':'BAL','Bills':'BUF','Panthers':'CAR','Bears':'CHI','Bengals':'CIN','Browns':'CLE','Cowboys':'DAL','Broncos':'DEN','Lions':'DET','Packers':'GB','Texans':'HOU','Colts':'IND','Jaguars':'JAX','Chiefs':'KC','Raiders':'LV','Chargers':'LAC','Rams':'LA','Dolphins':'MIA','Vikings':'MIN','Patriots':'NE','Saints':'NO','Giants':'NYG','Jets':'NYJ','Eagles':'PHI','Steelers':'PIT','49ers':'SF','Seahawks':'SEA','Buccaneers':'TB','Titans':'TEN','Commanders':'WAS',
}

def atomic(path,text):
 tmp=path.with_name(path.name+'.tmp');tmp.write_text(text);tmp.chmod(0o644);tmp.replace(path)

def extract_cards(block):return re.findall(r'<article class="card">.*?</article>',block,re.S)
def txt(pat,s,default=''):
 m=re.search(pat,s,re.S);return re.sub('<.*?>','',m.group(1)).strip() if m else default

def card_meta(card):
 selected=txt(r'<h2 class="pick">(.*?) to win</h2>',card)
 week=txt(r'NFL · Week\s*(\d+)',card)
 date_text=txt(r'<div class="muted">NFL · Week\s*\d+<br>(.*?) · \d{2}:\d{2} [AP]M ET</div>',card)
 price=txt(r'<div class="quote"><strong>([+\-]?\d+(?:\.\d+)?)</strong>',card)
 fixture=txt(r'</h2><div>(.*?)</div><div class="muted">NFL · Week',card)
 methods=', '.join(re.findall(r'<span class="eyebrow">([^<·]+) · Qualifying method</span>',card)) or '—'
 try:week_i=int(week)
 except:week_i=None
 try:price_f=float(price)
 except:price_f=None
 return dict(selected=selected,team=DISPLAY_TO_TEAM.get(selected,selected),week=week_i,date_text=date_text,price=price_f,fixture=fixture,methods=methods)

def schedule():
 raw=urlopen(Request('https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv',headers={'User-Agent':'AppwizaNFLPage/1.0'}),timeout=30).read()
 g=pd.read_csv(io.BytesIO(raw));
 for c in ['home_team','away_team']:g[c]=g[c].replace(ALIASES)
 return g[(g.season==datetime.now(NY).year)&(g.game_type=='REG')].copy()

def settle(meta,g):
 if not meta['team'] or meta['week'] is None:return ('pending',None)
 z=g[(g.week==meta['week'])&((g.home_team==meta['team'])|(g.away_team==meta['team']))]
 if len(z)!=1:return ('pending',None)
 r=z.iloc[0]
 if pd.isna(r.home_score) or pd.isna(r.away_score):return ('pending',None)
 won=(r.home_team==meta['team'] and r.home_score>r.away_score) or (r.away_team==meta['team'] and r.away_score>r.home_score)
 push=r.home_score==r.away_score
 result='push' if push else 'win' if won else 'loss'
 p=meta['price'];u=None
 if p is not None:
  u=0.0 if result=='push' else (-1.0 if result=='loss' else (p/100 if p>0 else 100/abs(p)))
 return result,u

def downloads_from(s):
 m=re.search(r'<section id="downloads">.*?</section>',s,re.S)
 if not m:return ''
 inner=m.group(0)
 inner=re.sub(r'^<section id="downloads"><h2>Research downloads</h2>','',inner)
 inner=re.sub(r'</section>$','',inner)
 return '<details class="downloads-block" id="downloads"><summary>Research downloads</summary><div class="downloads-inner">'+inner+'</div></details>'

def group_upcoming(cards):
 groups={}
 for c in cards:
  m=card_meta(c);key=m['date_text'].split(' · ')[0] if m['date_text'] else 'Upcoming'
  groups.setdefault(key,[]).append(c)
 out=[]
 for key,vals in groups.items():
  out.append(f'<section class="date-block"><div class="date-heading"><h2>{esc(key)}</h2><span>{len(vals)} selection'+('s' if len(vals)!=1 else '')+'</span></div><div class="grid upcoming-grid">'+''.join(vals)+'</div></section>')
 return ''.join(out) if out else '<div class="empty-compact">No current method-qualified selections.</div>'

def history(cards,g):
 rows=[];settled=[]
 for c in cards:
  m=card_meta(c);result,u=settle(m,g);settled.append((result,u))
  label={'win':'WIN','loss':'LOSS','push':'PUSH'}.get(result,'PENDING');cls=result if result in {'win','loss','push'} else 'pending'
  pl='—' if u is None else f'{u:+.2f}u';price='—' if m['price'] is None else f'{m["price"]:+.0f}'
  rows.append(f'<tr><td>{esc(m["date_text"].split(" · ")[0])}</td><td><strong>{esc(m["selected"])}</strong></td><td>{esc(m["fixture"])}</td><td>{esc(m["methods"])}</td><td>{price}</td><td class="{cls}">{label}</td><td class="profit">{pl}</td></tr>')
 w=sum(r=='win' for r,_ in settled);l=sum(r=='loss' for r,_ in settled);p=sum(r=='push' for r,_ in settled);priced=[u for _,u in settled if u is not None];units=sum(priced) if priced else 0;roi=units/len(priced) if priced else None;wp=w/(w+l) if w+l else None
 table='<div class="history-table-wrap"><table class="history-table"><thead><tr><th>Date</th><th>Pick</th><th>Matchup</th><th>Method</th><th>Price</th><th>Result</th><th>P/L</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table></div>' if rows else '<div class="empty-compact">No completed tracked selections yet.</div>'
 return table,dict(w=w,l=l,p=p,units=units,roi=roi,wp=wp)

def main():
 s=PAGE.read_text(errors='replace')
 up_start=s.find('<h2>Upcoming selections</h2>');hist_start=s.find('<details open><summary>Selection history and results')
 if up_start<0:raise SystemExit('Upcoming section not found')
 up_block=s[up_start:hist_start if hist_start>=0 else len(s)]
 upcoming=extract_cards(up_block)
 hist_block=s[hist_start:] if hist_start>=0 else ''
 # stop before downloads if present so download cards are never mistaken for selections
 dpos=hist_block.find('<section id="downloads">')
 if dpos>=0:hist_block=hist_block[:dpos]
 past=extract_cards(hist_block)
 g=schedule();table,rec=history(past,g)
 wp='—' if rec['wp'] is None else f"{rec['wp']:.1%}";roi='—' if rec['roi'] is None else f"{rec['roi']:+.1%}"
 record=f'''<div class="record-strip"><div class="record-box"><span>Overall record</span><b>{rec['w']}-{rec['l']}{('-'+str(rec['p'])) if rec['p'] else ''}</b></div><div class="record-box"><span>Win rate</span><b>{wp}</b></div><div class="record-box"><span>Tracked ROI</span><b class="{'good' if rec['roi'] is not None and rec['roi']>=0 else 'bad'}">{roi}</b></div><div class="record-box"><span>Net units</span><b class="{'good' if rec['units']>=0 else 'bad'}">{rec['units']:+.2f}u</b></div><div class="record-box"><span>Upcoming</span><b>{len(upcoming)}</b></div></div>'''
 nav='<header><nav><a class="brand" href="/">appwiza.com</a><a href="/sports/">Sports</a><a href="/sports/ufc/">UFC</a><a href="/sports/nfl/" aria-current="page">NFL</a><a href="/sports/nfl/research/">Research Lab</a><a href="/sports/boxing/">Boxing</a><a href="#downloads">Downloads</a></nav></header>'
 research='<div class="research-cta"><div><strong>NFL Research Lab</strong><p>Shadow methods, holdout performance, veto research and consensus signals stay separate until promotion.</p></div><a href="/sports/nfl/research/">Open Research Lab →</a></div>'
 updated=txt(r'<span>Updated:\s*(.*?)</span>',s,'Current scan')
 out=f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="description" content="Appwiza NFL method watchlist, tracked record and research."><title>NFL watchlist | Appwiza</title><link rel="stylesheet" href="/sports/style.css"><link rel="stylesheet" href="/sports/nfl/dashboard.css?v=20260915b"></head><body>{nav}<main><span class="eyebrow">Appwiza · NFL</span><h1>NFL qualifying matchups</h1><p class="sub">Method-qualified selections used by the NFL scanner, grouped by game date, with recorded prices and historical evidence.</p><div class="bar"><span>Updated: {esc(updated)}</span><span>{len(upcoming)} upcoming · nearest games first</span></div>{record}{research}<div class="section-kicker">Current board</div><h2>Available selections</h2>{group_upcoming(upcoming)}<div class="section-kicker">Tracked results</div><h2>Completed selections</h2><p class="muted">One unit per tracked selection at the recorded moneyline. Pending results are excluded from ROI.</p>{table}{downloads_from(s)}<footer>Historical method performance does not guarantee future results. Research methods remain separate until explicitly promoted.</footer></main></body></html>'''
 atomic(PUBLIC/'dashboard.css',DASH_CSS);atomic(PAGE,out)
 print({'upcoming':len(upcoming),'past':len(past),'record':f"{rec['w']}-{rec['l']}",'roi':rec['roi']})
if __name__=='__main__':main()
