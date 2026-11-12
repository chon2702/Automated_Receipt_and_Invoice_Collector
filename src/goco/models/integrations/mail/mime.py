from dataclasses import dataclass
from email.utils import getaddresses
import html
import re
from email import policy
from email.parser import BytesParser
from pathlib import Path
from goco.models import DocumentCandidate,SourceRef,InvoiceMetadata,MAX_BYTES
from goco.models.integrations.mail.gmail import decode_data


@dataclass(frozen=True)
class MailMessage:
    message_id: str
    received_epoch: float
    senders: tuple[str,...]
    recipients: tuple[str,...]
    subject: str
    attachments: tuple[dict,...]
    sender_header: str = ''
    body_text: str = ''


MAX_BODY_TEXT = 256 * 1024


def _part_text(part):
    data = part.get('body', {}).get('data')
    if not isinstance(data, str):
        return ''
    try:
        raw = decode_data(data, MAX_BODY_TEXT)
    except ValueError:
        return ''
    text = raw.decode('utf-8', errors='replace')
    if part.get('mimeType', '').casefold() == 'text/html':
        text = html.unescape(re.sub(r'(?is)<(script|style)\b.*?</\1>|<[^>]+>', ' ', text))
    return text


def normalize_message(raw):
    headers={}
    payload=raw.get('payload',{})
    for header in payload.get('headers',[]):
        headers.setdefault(header['name'].casefold(),[]).append(header['value'])
    addresses=lambda names: tuple(address.casefold() for _,address in getaddresses([v for name in names for v in headers.get(name,[])]))
    attachments=[]
    texts={'text/plain':[],'text/html':[]}
    def walk(part):
        if part.get('filename'):
            attachments.append(part)
        elif part.get('mimeType','').casefold() in texts:
            texts[part['mimeType'].casefold()].append(part)
        for child in part.get('parts',[]): walk(child)
    walk(payload)
    # Prefer the plain-text alternative; only message text parts are decoded, never attachments.
    body=' '.join(_part_text(part) for part in (texts['text/plain'] or texts['text/html']))[:MAX_BODY_TEXT]
    try: received=int(raw['internalDate'])/1000
    except (KeyError,ValueError,TypeError): raise ValueError('missing_received_timestamp') from None
    return MailMessage(raw['id'],received,addresses(['from']),addresses(['to','cc','delivered-to']),
        ' '.join(headers.get('subject',[])),tuple(attachments),' '.join(headers.get('from',[])),body)


def attachment_content(message, part, fetch_attachment, maximum=MAX_BYTES):
    """Return the original attachment bytes and a stable per-message attachment identity."""
    body = part.get('body', {})
    size = body.get('size')
    if type(size) is not int or size < 0 or size > maximum: raise ValueError('invalid_document_size')
    attachment_id = body.get('attachmentId')
    content = fetch_attachment(message.message_id, attachment_id) if attachment_id else decode_data(body.get('data'), maximum)
    if len(content) > maximum: raise ValueError('invalid_document_size')
    source_id = attachment_id or part.get('partId')
    if not source_id: raise ValueError('missing_attachment_identity')
    return content, source_id


def attachment_source(mailbox_id, message, part):
    source_id = part.get('body', {}).get('attachmentId') or part.get('partId') or 'unknown'
    return SourceRef('gmail', f'{mailbox_id}:{message.message_id}:{source_id}',
                     mailbox_id=mailbox_id, message_id=message.message_id, attachment_id=source_id)


def extract_candidates(message,decision,fetch_attachment,accounts,mailbox_id,maximum=MAX_BYTES):
    candidates=[]
    if not decision.rules: return candidates
    for part in message.attachments:
        attachment_path = Path(part['filename'])
        extension = attachment_path.suffix.lower()
        filename = attachment_path.name.casefold()
        if part.get('mimeType','').startswith('image/'): continue
        rules = [
            rule for rule in decision.rules
            if extension in rule['attachment_extensions']
            and filename.startswith(rule.get('attachment_filename_prefix', '').casefold())
        ]
        if not rules: continue
        if len({r['document_type'] for r in rules})>1: raise ValueError('ambiguous_document_type')
        rule=rules[0];account=accounts[rule['account_id']]
        content,source_id=attachment_content(message,part,fetch_attachment,maximum)
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
