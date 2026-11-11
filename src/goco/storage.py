from dataclasses import asdict
from pathlib import Path
from copy import deepcopy
import hashlib
import json
import os
import re
import uuid
from .models import StoredDocument, CollectionOutcome, MAX_BYTES, utcnow
from .validation import validate_content, metadata_valid, confined
from .config import slug


def atomic_json(path, data):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as stream:
            json.dump(data,stream,ensure_ascii=True,indent=2,allow_nan=False)
            stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


def publish_directory(stage, final):
    os.rename(stage,final)


def read_documents(root):
    root=Path(root).resolve()
    result=[]
    seen=set()
    for sidecar in sorted((root/'documents').rglob('metadata.json')):
        if any(part.startswith('.stage-') for part in sidecar.relative_to(root).parts): continue
        if not sidecar.resolve().is_relative_to(root): raise ValueError('unsafe_sidecar_path')
        try:
            raw=json.loads(sidecar.read_text(encoding='utf-8'))
            document=StoredDocument(**raw)
            if document.schema_version != 1: raise ValueError()
            uuid.UUID(document.document_id)
            if document.document_id in seen: raise ValueError()
            slug(document.account_id,'account_id');slug(document.vendor_id,'vendor_id')
            original=confined(root,document.original_path)
            if original.parent != sidecar.parent.resolve(): raise ValueError()
            if document.byte_count>MAX_BYTES or original.stat().st_size != document.byte_count: raise ValueError()
            content=original.read_bytes()
            if hashlib.sha256(content).hexdigest()!=document.sha256: raise ValueError()
            metadata_valid(document.metadata)
            validate_content(content,original.suffix,document.document_type)
            if not isinstance(document.sources,list) or not document.sources: raise ValueError()
            if not isinstance(document.history,list) or not isinstance(document.expectation_ids,list): raise ValueError()
        except (OSError,ValueError,TypeError,KeyError):
            raise ValueError('document_integrity_failed') from None
        seen.add(document.document_id);result.append(document)
    return result


def read_runs(root):
    result=[]
    for file in sorted((Path(root)/'runs').glob('*.json')):
        if not file.resolve().is_relative_to(Path(root).resolve()): raise ValueError('unsafe_run_path')
        try:
            run=json.loads(file.read_text(encoding='utf-8'))
            if run['schema_version']!=1 or str(uuid.UUID(run['run_id']))!=file.stem: raise ValueError()
            if not isinstance(run['outcomes'],list) or not isinstance(run['coverage'],list): raise ValueError()
            result.append(run)
        except (OSError,ValueError,TypeError,KeyError): raise ValueError('run_integrity_failed') from None
    return result


class DocumentStore:
    def __init__(self, root, index, maximum=MAX_BYTES):
        self.root=Path(root).resolve();self.index=index;self.maximum=maximum

    def documents(self):
        # Sidecars are authoritative, including after an interrupted index update.
        return read_documents(self.root)

    def ingest(self, candidate):
        slug(candidate.account_id,'account_id');slug(candidate.vendor_id,'vendor_id')
        extension=Path(candidate.original_filename).suffix.lower()
        validate_content(candidate.content,extension,candidate.document_type,self.maximum)
        metadata=asdict(candidate.metadata);metadata_valid(metadata)
        digest=hashlib.sha256(candidate.content).hexdigest()
        existing=self.documents()
        for doc in existing:
            if doc.account_id==candidate.account_id and doc.document_type==candidate.document_type and doc.sha256==digest:
                source=asdict(candidate.source)
                key=lambda s: tuple(s.get(k) for k in ('method','origin_id','mailbox_id','message_id','attachment_id'))
                if key(source) not in [key(s) for s in doc.sources]:
                    doc.sources.append(source)
                    atomic_json(confined(self.root,doc.original_path).parent/'metadata.json',asdict(doc))
                self.index.upsert(doc)
                return CollectionOutcome(candidate.account_id,'duplicate','already_present',doc.document_id)
        invoice_id=metadata.get('invoice_id')
        conflict=bool(invoice_id and any(d.account_id==candidate.account_id and d.document_type==candidate.document_type and d.metadata.get('invoice_id')==invoice_id for d in existing))
        ident=str(uuid.uuid4())
        month=metadata['issue_date'][:7] if metadata.get('issue_date') else 'unknown'
        parent=self.root/'documents'/candidate.vendor_id/candidate.account_id/month
        if not parent.resolve().is_relative_to(self.root): raise ValueError('unsafe_document_path')
        parent.mkdir(parents=True,exist_ok=True)
        final=parent/ident;stage=parent/('.stage-'+ident);stage.mkdir()
        original_path=(final/('original'+extension)).relative_to(self.root).as_posix()
        doc=StoredDocument(ident,candidate.account_id,candidate.vendor_id,candidate.document_type,digest,len(candidate.content),original_path,
            Path(candidate.original_filename.replace('\\','/')).name,metadata,[asdict(candidate.source)],candidate.owner_id,
            [candidate.expectation_id] if candidate.expectation_id else [],conflict=conflict)
        with (stage/('original'+extension)).open('xb') as stream:
            stream.write(candidate.content);stream.flush();os.fsync(stream.fileno())
        atomic_json(stage/'metadata.json',asdict(doc))
        publish_directory(stage,final)
        self.index.upsert(doc)
        needs_review=conflict or not metadata.get('verified') or candidate.document_type=='unknown'
        return CollectionOutcome(candidate.account_id,'review_required' if needs_review else 'saved','business_key_conflict' if conflict else ('metadata_unverified' if needs_review else 'original_saved'),ident)

    def verify(self, document_id, update, expectation=None):
        docs=self.documents()
        doc=next((d for d in docs if d.document_id==document_id),None)
        if doc is None: raise ValueError('unknown_document_id')
        if Path(doc.original_path).suffix=='.eml': raise ValueError('source_message_cannot_satisfy_invoice')
        reviewer=update.get('reviewed_by')
        if not isinstance(reviewer,str) or not reviewer.strip(): raise ValueError('reviewed_by_required')
        kind=update.get('document_type')
        if kind=='unknown': raise ValueError('verified_type_required')
        before=deepcopy(asdict(doc));metadata=dict(doc.metadata)
        metadata.update({k:v for k,v in update.items() if k in metadata})
        metadata.update(provenance='manual',verified=True)
        metadata_valid(metadata)
        if not metadata.get('period_start'): raise ValueError('verified_period_required')
        original=confined(self.root,doc.original_path)
        validate_content(original.read_bytes(),original.suffix,kind,self.maximum)
        for other in docs:
            if other.document_id==doc.document_id or other.account_id!=doc.account_id or other.document_type!=kind: continue
            if other.sha256==doc.sha256: return CollectionOutcome(doc.account_id,'review_required','type_change_duplicate',other.document_id)
            if metadata.get('invoice_id') and other.metadata.get('invoice_id')==metadata['invoice_id']:
                return CollectionOutcome(doc.account_id,'review_required','business_key_conflict',doc.document_id)
        if expectation:
            if not expectation_matches(expectation,doc.account_id,kind,metadata): raise ValueError('expectation_metadata_mismatch')
        doc.document_type=kind;doc.metadata=metadata
        if expectation and expectation['id'] not in doc.expectation_ids: doc.expectation_ids.append(expectation['id'])
        doc.history.append(dict(at=utcnow(),reviewed_by=reviewer,before={'document_type':before['document_type'],'metadata':before['metadata'],'expectation_ids':before['expectation_ids']},after={'document_type':kind,'metadata':metadata,'expectation_ids':list(doc.expectation_ids)}))
        atomic_json(original.parent/'metadata.json',asdict(doc));self.index.upsert(doc)
        return CollectionOutcome(doc.account_id,'saved','metadata_verified',doc.document_id)


def expectation_matches(e,account,kind,metadata):
    return e['account_id']==account and e['required_type']==kind and metadata.get('verified') and metadata.get('period_start')==e['period_start'] and metadata.get('period_end')==e['period_end']
