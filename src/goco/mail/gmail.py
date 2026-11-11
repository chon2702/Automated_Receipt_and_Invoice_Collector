import base64
import binascii
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
import json
import random
import time
from urllib.parse import quote
import requests
from ..auth import AuthRequired
from ..models import MAX_BYTES


class TransportError(Exception):
    pass


def decode_data(data,maximum=MAX_BYTES):
    if not isinstance(data,str) or len(data)>(maximum+2)//3*4+4: raise ValueError('invalid_attachment_data')
    try:
        result=base64.b64decode(data+'='*((-len(data))%4),altchars=b'-_',validate=True)
    except (binascii.Error,ValueError): raise ValueError('invalid_attachment_data') from None
    if len(result)>maximum: raise ValueError('invalid_document_size')
    return result


class GmailClient:
    def __init__(self,token,address,session=None,sleep=time.sleep):
        self.token=token;self.session=session or requests.Session();self.sleep=sleep
        profile=self._get('profile')
        if profile.get('emailAddress','').casefold()!=address.casefold(): raise AuthRequired('mailbox_identity_mismatch')

    def _get(self,resource,params=None):
        for attempt in range(3):
            response=None
            try:
                response=self.session.get('https://gmail.googleapis.com/gmail/v1/users/me/'+resource,
                    params=params or {},headers={'Authorization':'Bearer '+self.token},timeout=(10,30),stream=True,allow_redirects=False)
                status=response.status_code
                if status==401: raise AuthRequired('login_required')
                if status==429 or 500<=status<=599:
                    if attempt==2: raise TransportError('gmail_retry_exhausted')
                    retry=response.headers.get('Retry-After','')
                    try: delay=float(retry)
                    except ValueError:
                        try: delay=(parsedate_to_datetime(retry)-datetime.now(timezone.utc)).total_seconds()
                        except (ValueError,TypeError,OverflowError): delay=2**attempt+random.random()
                    self.sleep(max(0,min(60,delay)))
                    continue
                if status!=200: raise TransportError('gmail_access_denied' if status==403 else 'gmail_request_failed')
                content=bytearray()
                for chunk in response.iter_content(65536):
                    content.extend(chunk)
                    if len(content)>2*MAX_BYTES+1024*1024: raise TransportError('gmail_response_too_large')
                data=json.loads(content)
                if not isinstance(data,dict): raise TransportError('gmail_invalid_response')
                return data
            except requests.RequestException:
                if attempt==2: raise TransportError('gmail_network_failed') from None
                self.sleep(min(60,2**attempt+random.random()))
            except (ValueError,TypeError): raise TransportError('gmail_invalid_response') from None
            finally:
                if response is not None: response.close()

    def list_messages(self,query,page_token=None):
        params={'q':query,'maxResults':100,'includeSpamTrash':'false'}
        if page_token: params['pageToken']=page_token
        return self._get('messages',params)

    def get_message(self,message_id,metadata_only=False):
        params={'format':'metadata' if metadata_only else 'full'}
        if metadata_only: params['metadataHeaders']=['From','To','Cc','Delivered-To','Subject']
        return self._get('messages/'+quote(message_id,safe=''),params)

    def get_attachment(self,message_id,attachment_id):
        data=self._get('messages/'+quote(message_id,safe='')+'/attachments/'+quote(attachment_id,safe=''))
        if data.get('size',0)>MAX_BYTES: raise ValueError('invalid_document_size')
        return decode_data(data.get('data'))
