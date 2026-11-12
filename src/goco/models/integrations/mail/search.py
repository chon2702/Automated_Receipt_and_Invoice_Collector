"""Find vendor invoice PDFs in Gmail by product names and search aliases.

Gmail search only preselects messages. Every hit is checked again locally, PDF by
PDF: a vendor name and an invoice feature are both required before a document is
stored automatically. Anything ambiguous goes to the existing review flow.
"""

from dataclasses import dataclass
from pathlib import Path
import re

from goco.models import DocumentCandidate, InvoiceMetadata, MAX_BYTES
from .mime import attachment_content, attachment_source

# Lowercase stems; they may be followed by more letters (invoices, Rechnungsnummer).
DOCUMENT_FEATURES = {
    'invoice': ('invoice', 'rechnung'),
    'receipt': ('receipt', 'zahlungsbeleg', 'zahlungsbestätigung', 'zahlungsbestaetigung', 'quittung'),
    'credit_note': ('credit note', 'credit memo', 'gutschrift'),
}
MAX_QUERY_TERMS_LENGTH = 1000
_WORD = r'[^\W_]+'
_SEPARATOR = r'[\W_]+'
_START = r'(?<![^\W_])'
_END = r'(?![^\W_])'


def _phrase(text, whole_word=True):
    words = re.findall(_WORD, text.casefold())
    return re.compile(_START + _SEPARATOR.join(map(re.escape, words)) + (_END if whole_word else ''))


_FEATURES = {kind: tuple(_phrase(stem, whole_word=False) for stem in stems)
             for kind, stems in DOCUMENT_FEATURES.items()}


def search_names(account):
    """Product name first, then the optional aliases."""
    return (account['product'], *account.get('search_aliases', ()))


def document_types(text):
    """Document types whose feature words appear in the text."""
    folded = text.casefold()
    return {kind for kind, patterns in _FEATURES.items() if any(p.search(folded) for p in patterns)}


def search_queries(start_epoch, end_epoch, names, limit=MAX_QUERY_TERMS_LENGTH):
    """Bundle vendor names into as few Gmail queries as fit the length limit."""
    base = f'after:{start_epoch - 1} before:{end_epoch} has:attachment filename:pdf'
    terms, seen = [], set()
    for name in sorted(names, key=str.casefold):
        if name.casefold() in seen:
            continue
        seen.add(name.casefold())
        terms.append(f'"{name}"')
        if name.isascii() and re.fullmatch(_WORD, name):
            terms.append(f'filename:{name.casefold()}')
    queries, chunk = [], []
    for term in terms:
        if chunk and len(' '.join(chunk + [term])) > limit:
            queries.append(chunk)
            chunk = []
        chunk.append(term)
    if chunk:
        queries.append(chunk)
    return [f'{base} {{{" ".join(chunk)}}}' for chunk in queries]


class VendorMatcher:
    def __init__(self, accounts):
        self.patterns = {account['id']: tuple(_phrase(name) for name in search_names(account))
                         for account in accounts}

    def find(self, text):
        folded = text.casefold()
        return {account_id for account_id, patterns in self.patterns.items()
                if any(pattern.search(folded) for pattern in patterns)}


@dataclass(frozen=True)
class AttachmentDecision:
    part: dict
    account_ids: tuple[str, ...]
    document_type: str | None
    reason: str  # matched, ambiguous_vendor, document_type_unclear, no_invoice_feature


def is_pdf(part):
    filename = part.get('filename')
    return (isinstance(filename, str) and Path(filename.replace('\\', '/')).suffix.casefold() == '.pdf'
            and not part.get('mimeType', '').startswith('image/'))


def decide_attachments(message, accounts):
    """Assign each PDF attachment to one vendor account and one document type, or say why not.

    Evidence order: the attachment file name, then sender and subject, then the message
    text. A lower tier is used only when the higher tiers name no vendor at all, so a
    Stripe receipt names its vendor in the subject or file name even though Stripe sent it.
    """
    matcher = VendorMatcher(accounts)
    header_vendors = matcher.find(message.sender_header + '\n' + message.subject)
    body_vendors = None
    message_types = document_types(message.subject + '\n' + message.body_text)
    decisions = []
    for part in message.attachments:
        if not is_pdf(part):
            continue
        filename = Path(part['filename'].replace('\\', '/')).name
        file_vendors = matcher.find(filename)
        if len(file_vendors) == 1:
            vendors = file_vendors
        else:
            vendors = file_vendors | header_vendors
        if not vendors:
            if body_vendors is None:
                body_vendors = matcher.find(message.body_text)
            vendors = body_vendors
        if not vendors:
            continue  # Gmail matched something else, for example text inside the PDF.
        types = document_types(filename)
        kind = next(iter(types)) if len(types) == 1 else None
        if len(vendors) > 1:
            reason = 'ambiguous_vendor'
        elif kind:
            reason = 'matched'
        elif types or message_types:
            reason = 'document_type_unclear'
        else:
            reason = 'no_invoice_feature'
        decisions.append(AttachmentDecision(part, tuple(sorted(vendors)), kind, reason))
    return decisions


def build_candidate(message, decision, account, mailbox_id, fetch_attachment, maximum=MAX_BYTES):
    """Download one matched PDF through the existing Gmail attachment path."""
    content, _ = attachment_content(message, decision.part, fetch_attachment, maximum)
    return DocumentCandidate(
        account['id'], account['vendor_id'], decision.document_type, decision.part['filename'], content,
        attachment_source(mailbox_id, message, decision.part),
        InvoiceMetadata(provenance='gmail_search'), owner_id=account['owner_id'],
    )
