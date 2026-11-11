from datetime import datetime, timedelta
import json
import pytest
from google.oauth2.credentials import Credentials


class Secrets:
    def __init__(self, value=None): self.value=value;self.writes=0
    def get(self, ref): return self.value
    def put(self, ref, value): self.value=value;self.writes+=1


def test_expired_dry_run_never_refreshes_or_writes():
    from goco.models.integrations.mail.auth import ensure_credentials, AuthRequired, READONLY
    credentials=Credentials('secret',refresh_token='refresh',token_uri='https://oauth2.googleapis.com/token',client_id='client',client_secret='secret',scopes=[READONLY],expiry=datetime(2000,1,1))
    secrets=Secrets(json.loads(credentials.to_json()))
    with pytest.raises(AuthRequired): ensure_credentials({'secret_ref':'mail'},secrets,allow_refresh=False)
    assert secrets.writes==0


def test_refresh_rotation_stays_in_secret_store(monkeypatch):
    from goco.models.integrations.mail.auth import ensure_credentials, READONLY
    credentials=Credentials('old',refresh_token='refresh',token_uri='https://oauth2.googleapis.com/token',client_id='client',client_secret='secret',scopes=[READONLY],expiry=datetime(2000,1,1))
    secrets=Secrets(json.loads(credentials.to_json()))
    def refresh(self, request):
        self.token='new';self.expiry=datetime(2099,1,1);self._refresh_token='rotated'
    monkeypatch.setattr(Credentials,'refresh',refresh)
    ensure_credentials({'secret_ref':'mail'},secrets,allow_refresh=True)
    assert secrets.writes==1 and secrets.value['refresh_token']=='rotated'


def test_unavailable_keyring_has_safe_failure():
    from goco.models.integrations.mail.auth import CredentialStore, AuthRequired
    class Broken:
        def get_password(self,*args): raise RuntimeError('sensitive TOKEN')
    with pytest.raises(AuthRequired) as error: CredentialStore(Broken()).get('mail')
    assert 'TOKEN' not in str(error.value)
