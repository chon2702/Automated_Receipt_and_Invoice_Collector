import base64
import pytest


def message(data=b'%PDF-1.7 example',accounts_subject='Invoice',received=1790805600000):
    return {'id':'m1','internalDate':str(received),'payload':{'mimeType':'multipart/mixed','headers':[{'name':'From','value':'Billing <billing@example.test>'},{'name':'To','value':'finance@example.test'},{'name':'Subject','value':accounts_subject}], 'parts':[{'mimeType':'multipart/mixed','parts':[{'filename':'../../invoice.pdf','mimeType':'application/pdf','body':{'data':base64.urlsafe_b64encode(data).decode(),'size':len(data)},'partId':'2.1'}]}]}}


def rule(account='a'):
    return dict(id='rule-'+account,account_id=account,sender_addresses=['billing@example.test'],recipient_addresses=['finance@example.test'],attachment_extensions=['.pdf'],document_type='invoice',enabled=True)


def test_nested_mime_preserves_original_and_unknown_metadata():
    from goco.models.integrations.mail.mime import normalize_message, extract_candidates
    from goco.models.integrations.vendors.rules import match_rules
    msg=normalize_message(message())
    decision=match_rules(msg,[rule()])
    candidates=extract_candidates(msg,decision,lambda *_: pytest.fail('inline attachment should not need a fetch'),{'a':{'id':'a','vendor_id':'vendor','owner_id':'person1'}},'mail')
    assert candidates[0].content==b'%PDF-1.7 example'
    assert candidates[0].metadata.period_start is None
    assert candidates[0].source.message_id=='m1'


def test_ambiguous_account_matching_never_guesses():
    from goco.models.integrations.mail.mime import normalize_message
    from goco.models.integrations.vendors.rules import match_rules
    decision=match_rules(normalize_message(message()),[rule(),rule('b')])
    assert decision.reason=='ambiguous_account' and not decision.rules


def test_bad_base64_and_oversize_rejected_before_fetch():
    from goco.models.integrations.mail.mime import normalize_message, extract_candidates
    from goco.models.integrations.vendors.rules import match_rules
    raw=message();raw['payload']['parts'][0]['parts'][0]['body']['data']='!!!!'
    msg=normalize_message(raw)
    with pytest.raises(ValueError): extract_candidates(msg,match_rules(msg,[rule()]),lambda *_:b'',{'a':{'id':'a','vendor_id':'vendor','owner_id':'person1'}},'mail')
    raw=message();body=raw['payload']['parts'][0]['parts'][0]['body'];body.pop('data');body.update(size=999999999,attachmentId='large')
    msg=normalize_message(raw)
    with pytest.raises(ValueError): extract_candidates(msg,match_rules(msg,[rule()]),lambda *_:pytest.fail('oversize must not fetch'),{'a':{'id':'a','vendor_id':'vendor','owner_id':'person1'}},'mail')
