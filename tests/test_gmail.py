import json
import pytest
from requests import ConnectionError


class Response:
    def __init__(self,body,status=200,headers=None): self.body=body;self.status_code=status;self.headers=headers or {}
    def iter_content(self,chunk_size): yield json.dumps(self.body).encode()
    def close(self): pass


class Session:
    def __init__(self,responses): self.responses=iter(responses);self.calls=[]
    def get(self,url,**kwargs):
        self.calls.append((url,kwargs))
        value=next(self.responses)
        if isinstance(value,Exception): raise value
        return value


def test_pagination_metadata_and_timeouts():
    from goco.models.integrations.mail.gmail import GmailClient
    session=Session([Response({'emailAddress':'finance@example.test'}),Response({'messages':[{'id':'one'}],'nextPageToken':'p2'}),Response({'messages':[{'id':'two'}]}),Response({'id':'one','internalDate':'0','payload':{'headers':[]}})])
    client=GmailClient('secret','finance@example.test',session=session,sleep=lambda _:None)
    first=client.list_messages('after:1 before:2')
    second=client.list_messages('after:1 before:2',first.get('nextPageToken'))
    assert second['messages'][0]['id']=='two'
    client.get_message('one',metadata_only=True)
    assert session.calls[-1][1]['params']['format']=='metadata'
    assert session.calls[-1][1]['timeout']==(10,30)
    assert session.calls[2][1]['params']['pageToken']=='p2'


def test_identity_mismatch_and_auth_errors_are_safe():
    from goco.models.integrations.mail.gmail import GmailClient
    from goco.models.integrations.mail.auth import AuthRequired
    with pytest.raises(AuthRequired): GmailClient('secret','finance@example.test',session=Session([Response({'emailAddress':'other@example.test'})]))
    with pytest.raises(AuthRequired) as error: GmailClient('secret','finance@example.test',session=Session([Response({'secret':'TOKEN'},401)]))
    assert 'TOKEN' not in str(error.value)


def test_three_attempts_and_capped_retry_after():
    from goco.models.integrations.mail.gmail import GmailClient, TransportError
    delays=[]
    session=Session([Response({'error':{}},429,{'Retry-After':'999'}),ConnectionError('secret'),Response({'error':{}},503)])
    with pytest.raises(TransportError) as error: GmailClient('secret','finance@example.test',session=session,sleep=delays.append)
    assert len(session.calls)==3 and max(delays)<=60
    assert 'secret' not in str(error.value)


def test_gmail_retries_403_user_rate_limit():
    from goco.models.integrations.mail.gmail import GmailClient

    limited = Response({'error': {'errors': [{'reason': 'userRateLimitExceeded'}]}}, 403)
    session = Session([Response({'emailAddress': 'finance@example.test'}), limited, Response({'messages': []})])
    delays = []
    client = GmailClient('secret', 'finance@example.test', session=session, sleep=delays.append)
    assert client.list_messages('after:1') == {'messages': []}
    assert len(delays) == 1
