"""Opaque, inspectable and scope-bound research evidence references."""
from __future__ import annotations
import base64,hashlib,hmac,json
from typing import Any

def _validate(v:Any)->dict[str,Any]:
 if not isinstance(v,dict):raise ValueError('reference payload is not an object')
 kind=v.get('kind');expected={'v','kind','corpus','source','family','contract','parser','run','occurrence','index'}|({'child'} if kind=='text' else set())
 if kind not in ('text','container') or v.get('v')!=2 or set(v)!=expected:raise ValueError('unsupported research evidence reference shape')
 for k in ('corpus','source','family','contract','parser','run','occurrence'):
  if not isinstance(v.get(k),str) or not v[k]:raise ValueError('invalid research evidence reference field: '+k)
 if not isinstance(v.get('index'),int) or isinstance(v['index'],bool) or v['index']<=0:raise ValueError('invalid research evidence index')
 if kind=='text' and (not isinstance(v.get('child'),str) or not v['child']):raise ValueError('invalid research evidence child')
 return v
def encode_ref(v):
 body=json.dumps(_validate(v),ensure_ascii=False,sort_keys=True,separators=(',',':')).encode();seal=hashlib.sha256(b'genshin-lore-research-ref-v2\0'+body).digest()[:16];return base64.urlsafe_b64encode(body+seal).decode().rstrip('=')
def decode_ref(token):
 try:
  raw=base64.urlsafe_b64decode(token+'='*(-len(token)%4));body,seal=raw[:-16],raw[-16:];expected=hashlib.sha256(b'genshin-lore-research-ref-v2\0'+body).digest()[:16]
  if not hmac.compare_digest(seal,expected):raise ValueError
  return _validate(json.loads(body))
 except Exception as exc:raise ValueError('invalid research evidence reference') from exc
