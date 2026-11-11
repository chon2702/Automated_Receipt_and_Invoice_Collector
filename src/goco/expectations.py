from datetime import date, timedelta
from .storage import expectation_matches


def covered(expectation, coverage):
    start=expectation.get('discovery_start');end=expectation.get('discovery_end')
    if not start or not end: return False
    rows=[r for r in coverage if r['account_id']==expectation['account_id'] and r['start']<end and r['end']>start]
    points=sorted({start,end}|{max(start,r['start']) for r in rows}|{min(end,r['end']) for r in rows})
    for left,right in zip(points,points[1:]):
        applicable=[r for r in rows if r['start']<=left and r['end']>=right]
        if not applicable or not max(enumerate(applicable),key=lambda pair:(pair[1]['completed_at'],pair[0]))[1]['success']: return False
    return True


def evaluate(expectations, documents, today, coverage):
    result=[]
    for e in expectations:
        matches=[d.document_id for d in documents if not d.conflict and expectation_matches(e,d.account_id,d.document_type,d.metadata)]
        unresolved=any(d.account_id==e['account_id'] and (d.conflict or (d.document_type in (e['required_type'],'unknown') and (not d.metadata.get('verified') or not d.metadata.get('period_start')))) for d in documents)
        complete=covered(e,coverage) and not unresolved
        if len(matches)>=e['minimum_count']: status='satisfied'
        elif today<=date.fromisoformat(e['due_date'])+timedelta(days=e['grace_days']): status='pending'
        elif complete: status='missing'
        else: status='unknown'
        result.append(dict(expectation_id=e['id'],account_id=e['account_id'],status=status,coverage_incomplete=not complete,document_ids=matches,discovery_start=e.get('discovery_start'),discovery_end=e.get('discovery_end')))
    return result


def select_expectations(expectations,month):
    from calendar import monthrange
    year,number=map(int,month.split('-'))
    first=f'{month}-01';last=f'{month}-{monthrange(year,number)[1]:02}'
    return [e for e in expectations if e['period_start']<=last and e['period_end']>=first]
