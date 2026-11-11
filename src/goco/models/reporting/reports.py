from collections import Counter
from dataclasses import asdict
from goco.models.persistence.storage import atomic_json


def build_report(month, results, runs, documents=()):
    return dict(schema_version=1,month=month,obligations=results,counts=dict(Counter(r['status'] for r in results)),
        documents=[dict(document_id=d.document_id,account_id=d.account_id,document_type=d.document_type,original_path=d.original_path,sha256=d.sha256,metadata=d.metadata) for d in documents],
        runs=[dict(run_id=r['run_id'],started_at=r['started_at'],completed_at=r['completed_at'],outcomes=r['outcomes'],interrupted=r.get('interrupted',False)) for r in runs])


def write_report(report,path): atomic_json(path,report)
