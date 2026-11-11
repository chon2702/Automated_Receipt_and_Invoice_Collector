from dataclasses import dataclass
from email.utils import getaddresses
from email import policy
from email.parser import BytesParser
from pathlib import Path
from ..models import DocumentCandidate,SourceRef,InvoiceMetadata,MAX_BYTES
from .gmail import decode_data


@dataclass(frozen=True)
class MailMessage:
    message_id: str
    received_epoch: float
    senders: tuple[str,...]
    recipients: tuple[str,...]
    subject: str
    attachments: tuple[dict,...]


def normalize_message(raw):
    headers={}
    payload=raw.get('payload',{})
    for header in payload.get('headers',[]):
        headers.setdefault(header['name'].casefold(),[]).append(header['value'])
    addresses=lambda names: tuple(address.casefold() for _,address in getaddresses([v for name in names for v in headers.get(name,[])]))
    attachments=[]
    def walk(part):
        if part.get('filename'):
            attachments.append(part)
        for child in part.get('parts',[]): walk(child)
    walk(payload)
    try: received=int(raw['internalDate'])/1000
    except (KeyError,ValueError,TypeError): raise ValueError('missing_received_timestamp') from None
    return MailMessage(raw['id'],received,addresses(['from']),addresses(['to','cc','delivered-to']),
        ' '.join(headers.get('subject',[])),tuple(attachments))


def extract_candidates(message,decision,fetch_attachment,accounts,mailbox_id,maximum=MAX_BYTES):
    candidates=[]
    if not decision.rules: return candidates
    for part in message.attachments:
        extension=Path(part['filename']).suffix.lower()
        if part.get('mimeType','').startswith('image/'): continue
        rules=[r for r in decision.rules if extension in r['attachment_extensions']]
        if not rules: continue
        if len({r['document_type'] for r in rules})>1: raise ValueError('ambiguous_document_type')
        rule=rules[0];account=accounts[rule['account_id']];body=part.get('body',{})
        size=body.get('size')
        if type(size) is not int or size<0 or size>maximum: raise ValueError('invalid_document_size')
        attachment_id=body.get('attachmentId')
        content=fetch_attachment(message.message_id,attachment_id) if attachment_id else decode_data(body.get('data'),maximum)
        if len(content)>maximum: raise ValueError('invalid_document_size')
        source_id=attachment_id or part.get('partId')
        if not source_id: raise ValueError('missing_attachment_identity')
        candidates.append(DocumentCandidate(account['id'],account['vendor_id'],rule['document_type'],part['filename'],content,
            SourceRef('gmail',f'{mailbox_id}:{message.message_id}:{source_id}',mailbox_id=mailbox_id,message_id=message.message_id,attachment_id=source_id),
            InvoiceMetadata(provenance='rule'),rule_id=rule['id'],owner_id=account['owner_id']))
    return candidates


def parse_eml_fixture(content,message_id,received_epoch):
    """Normalize saved MIME messages for offline parser checks; never infers receipt time."""
    msg=BytesParser(policy=policy.default).parsebytes(content)
    import base64
    def part(node,ident):
        result={'mimeType':node.get_content_type(),'partId':ident,'headers':[{'name':k,'value':str(v)} for k,v in node.items()]}
        if node.get_filename(): result['filename']=node.get_filename()
        if node.is_multipart(): result['parts']=[part(p,f'{ident}.{i}') for i,p in enumerate(node.iter_parts())]
        else:
            body=node.get_payload(decode=True) or b''
            result['body']={'size':len(body),'data':base64.urlsafe_b64encode(body).decode()}
        return result
    return normalize_message({'id':message_id,'internalDate':str(int(received_epoch*1000)),'payload':part(msg,'0')})
