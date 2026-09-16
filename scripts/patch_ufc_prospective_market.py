#!/usr/bin/env python3
from pathlib import Path
p=Path.home()/"ufc-predictor-v1"/"ufc_prospective_pipeline.py"
s=p.read_text()
s=s.replace("first(['best_decimal_a','decimal_a','price_a','best_price_a','odds_a','american_a'])","first(['best_decimal_a','decimal_a','best_odds_a','best_price_a','odds_a','american_a'])")
s=s.replace("first(['best_decimal_b','decimal_b','price_b','best_price_b','odds_b','american_b'])","first(['best_decimal_b','decimal_b','best_odds_b','best_price_b','odds_b','american_b'])")
s=s.replace("book=str(first(['book','sportsbook','best_book']) or '')","book=str(first(['best_book_a','book','sportsbook','best_book']) or '')")
s=s.replace("qa=str(first(['quote_at','updated_at','timestamp']) or '')","qa=str(first(['quote_at','updated_at','timestamp']) or '')\n    if not qa and isinstance(m.get('quotes'),list):\n        vals=[str(x.get('last_update') or '') for x in m['quotes'] if isinstance(x,dict) and x.get('last_update')]\n        if vals: qa=max(vals)")
s=s.replace("m=b.get('market') or {};pa,pb,da,db,book,qa=market_values(m)","m=b.get('market') or {};pa,pb,da,db,book,qa=market_values(m);bst=estart or str(m.get('commence_time') or '')")
s=s.replace("INSERT OR IGNORE INTO bout_quotes", "INSERT OR REPLACE INTO bout_quotes")
s=s.replace("(ed,estart,a,bb,pa,pb,da,db,book,qa,cap,sha", "(ed,bst,a,bb,pa,pb,da,db,book,qa,cap,sha")
s=s.replace("'prospective_book':q.book", "'prospective_book':(json.loads(q.market_json).get('best_book_a' if fav1 else 'best_book_b') or q.book)")
p.write_text(s)
print('prospective market parser patched')
