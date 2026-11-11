from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from email import policy
from email.parser import BytesParser
import re
from defusedxml import ElementTree
from .types import DOCUMENT_TYPES, MAX_BYTES


def metadata_valid(metadata):
    for field in ('issue_date','period_start','period_end'):
        value = metadata.get(field)
        if value is not None:
            if not isinstance(value,str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',value): raise ValueError('invalid_date')
            date.fromisoformat(value)
    if bool(metadata.get('period_start')) != bool(metadata.get('period_end')): raise ValueError('paired_period_required')
    if metadata.get('period_start') and metadata['period_start'] > metadata['period_end']: raise ValueError('reversed_period')
    if (metadata.get('amount') is None) != (metadata.get('currency') is None): raise ValueError('paired_amount_currency_required')
    if metadata.get('amount') is not None:
        try:
            amount = metadata['amount']
            if not isinstance(amount,str) or not re.fullmatch(r'-?[0-9]+(?:\.[0-9]+)?',amount) or not Decimal(amount).is_finite(): raise ValueError('invalid_amount')
        except InvalidOperation:
            raise ValueError('invalid_amount') from None
        if not isinstance(metadata['currency'],str) or not re.fullmatch('[A-Z]{3}',metadata['currency']): raise ValueError('invalid_currency')
    if metadata.get('invoice_id') is not None and (not isinstance(metadata['invoice_id'],str) or not metadata['invoice_id'].strip()): raise ValueError('invalid_invoice_id')


def validate_content(content, extension, document_type, maximum=MAX_BYTES):
    if document_type not in DOCUMENT_TYPES: raise ValueError('invalid_document_type')
    if not content or len(content)>maximum: raise ValueError('invalid_document_size')
    if extension == '.pdf':
        if not content.startswith(b'%PDF-'): raise ValueError('invalid_pdf_header')
    elif extension == '.xml':
        try: ElementTree.fromstring(content, forbid_dtd=True, forbid_entities=True, forbid_external=True)
        except Exception: raise ValueError('unsafe_or_invalid_xml') from None
    elif extension == '.eml':
        if document_type != 'unknown': raise ValueError('source_message_must_be_unknown')
        message = BytesParser(policy=policy.default).parsebytes(content)
        if not message.keys(): raise ValueError('invalid_source_message')
    else:
        raise ValueError('unsupported_extension')


def confined(root, relative):
    if not isinstance(relative,str) or Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError('unsafe_document_path')
    path = (Path(root)/relative).resolve()
    if not path.is_relative_to(Path(root).resolve()): raise ValueError('unsafe_document_path')
    return path

def expectation_matches(e,account,kind,metadata):
    return e['account_id']==account and e['required_type']==kind and metadata.get('verified') and metadata.get('period_start')==e['period_start'] and metadata.get('period_end')==e['period_end']
