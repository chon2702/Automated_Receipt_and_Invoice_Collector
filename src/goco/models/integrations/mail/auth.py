"""Delegated Gmail OAuth. Tokens live only in the operating system vault."""
import json
import os
from pathlib import Path
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

READONLY='https://www.googleapis.com/auth/gmail.readonly'
TOKEN_URI='https://oauth2.googleapis.com/token'


class AuthRequired(Exception):
    pass


class CredentialStore:
    def __init__(self, backend=None):
        if backend is None:
            if os.name != 'nt': raise AuthRequired('windows_vault_required')
            # Explicit OS backend prevents fallback to a third-party plaintext store.
            from keyring.backends.Windows import WinVaultKeyring
            backend=WinVaultKeyring()
        self.backend=backend

    def get(self, secret_ref):
        try:
            text=self.backend.get_password('goco-autoinvoices',secret_ref)
            return json.loads(text) if text else None
        except Exception:
            raise AuthRequired('credential_store_unavailable') from None

    def put(self, secret_ref, state):
        try: self.backend.set_password('goco-autoinvoices',secret_ref,json.dumps(state))
        except Exception: raise AuthRequired('credential_store_unavailable') from None


class TimedRequest(Request):
    def __call__(self,*args,**kwargs):
        kwargs['timeout']=(10,30)
        return super().__call__(*args,**kwargs)


def ensure_credentials(mailbox, secrets, allow_refresh=True):
    state=secrets.get(mailbox['secret_ref'])
    if not state: raise AuthRequired('login_required')
    try:
        if state.get('token_uri')!=TOKEN_URI or set(state.get('scopes',[]))!={READONLY}:
            raise ValueError()
        credentials=Credentials.from_authorized_user_info(state,scopes=[READONLY])
        if not credentials.valid:
            if not allow_refresh or not credentials.refresh_token: raise AuthRequired('login_required')
            credentials.refresh(TimedRequest())
            secrets.put(mailbox['secret_ref'],json.loads(credentials.to_json()))
        if not credentials.valid: raise AuthRequired('login_required')
        return credentials
    except AuthRequired: raise
    except Exception: raise AuthRequired('credential_refresh_failed') from None


def login(mailbox,secrets):
    try:
        data=json.loads(Path(mailbox['oauth_client_file']).read_text(encoding='utf-8'))
        client=data['installed']
        if client['auth_uri']!='https://accounts.google.com/o/oauth2/auth' or client['token_uri']!=TOKEN_URI:
            raise ValueError()
        flow=InstalledAppFlow.from_client_config(data,scopes=[READONLY])
        credentials=flow.run_local_server(host='127.0.0.1',port=0,open_browser=True,timeout_seconds=180,
            access_type='offline',prompt='consent',login_hint=mailbox['address'],authorization_prompt_message='Complete Gmail consent in your browser.')
        if not credentials.refresh_token: raise AuthRequired('offline_consent_required')
        from .gmail import GmailClient
        GmailClient(credentials.token,mailbox['address'])
        secrets.put(mailbox['secret_ref'],json.loads(credentials.to_json()))
    except AuthRequired: raise
    except Exception: raise AuthRequired('login_failed') from None


def make_client(mailbox,dry_run=False):
    from .gmail import GmailClient
    credentials=ensure_credentials(mailbox,CredentialStore(),allow_refresh=not dry_run)
    return GmailClient(credentials.token,mailbox['address'])
