"""Find explicitly professional boxing tables without requiring first-row headers."""
import re

def clean(node):
    return re.sub(r'\[[^]]*\]', '', node.get_text(' ', strip=True)).strip().casefold() if node else ''

def record_tables(soup):
    for table in soup.find_all('table'):
        heading = table.find_previous(['h2','h3'])
        label = clean(heading)
        professional = label in ('professional boxing record','professional record')
        if label == 'professional' and heading.name == 'h3':
            professional = clean(heading.find_previous('h2')) in ('boxing record','boxing records')
        if not professional:
            continue
        rows = [r for r in table.find_all('tr') if r.find_parent('table') is table]
        for i, row in enumerate(rows[:8]):
            aliases = {'res.':'result', 'rd.,time':'round, time'}
            headers = [aliases.get(clean(c),clean(c)) for c in row.find_all(['th','td'],recursive=False)]
            if all(k in headers for k in ('result','opponent','date')):
                yield headers, rows[i+1:]
                break


def record_date(raw):
    """Require a complete date; normalize typography but never repair bad digits."""
    from datetime import datetime
    from dateutil.parser import parse
    value = re.sub(r'\[[^]]*\]', '', raw).strip()
    value = value.translate(str.maketrans({c:'-' for c in '\u2010\u2011\u2012\u2013\u2014\u2212'}))
    if not re.search(r'\b(?:18|19|20)\d{2}\b',value):
        raise ValueError('Missing explicit year')
    a=parse(value,default=datetime(1800,1,1)).date()
    b=parse(value,default=datetime(1801,2,2)).date()
    if a!=b:raise ValueError('Incomplete date')
    return a.isoformat()
