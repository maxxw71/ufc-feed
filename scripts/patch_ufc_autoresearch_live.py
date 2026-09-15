from pathlib import Path
p=Path('/home/anestishkurti92/ufc-predictor-v1/ufc_autoresearch.py')
s=p.read_text()
s=s.replace("'favorite_odds','fav_odds','american_odds'","'fav_decimal','favorite_decimal_odds','favorite_odds','fav_odds','american_odds'")
old="""    payload=json.dumps({'from':sender,'to':[to],'subject':f\"UFC Research Daily — {datetime.now().strftime('%Y-%m-%d')}\",'html':body}).encode()\n    req=Request('https://api.resend.com/emails',data=payload,headers={'Authorization':f'Bearer {key}','Content-Type':'application/json'},method='POST')\n    try:\n        with urlopen(req,timeout=30) as r: ok=200<=r.status<300\n        log(f\"Daily research email {'sent' if ok else 'failed'}\"); return ok\n    except Exception as e:\n        log(f\"Daily research email error: {e}\"); return False\n"""
new="""    try:\n        import requests\n        r=requests.post('https://api.resend.com/emails',headers={'Authorization':f'Bearer {key}','Content-Type':'application/json'},json={'from':sender,'to':[to],'subject':f\"UFC Research Daily — {datetime.now().strftime('%Y-%m-%d')}\",'html':body},timeout=30)\n        ok=r.status_code < 300\n        if not ok: log(f\"Daily research email failed: Resend {r.status_code} {r.text[:240]}\")\n        else: log('Daily research email sent')\n        return ok\n    except Exception as e:\n        log(f\"Daily research email error: {e}\"); return False\n"""
if old not in s:
    raise SystemExit('mail patch target not found')
p.write_text(s.replace(old,new))
print('patched')
