"""Bounded public citation capture. Retrieved text is data, never instructions.

Receipts attest local HTTPS retrieval and byte integrity, not factual truth,
editorial independence or historical availability before this capture.
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime
import hashlib
import http.client
from html.parser import HTMLParser
import ipaddress
import os
from pathlib import Path
import re
import socket
import ssl
import tempfile
import threading
import time
from urllib.parse import urlsplit, urlunsplit

VERSION='public-source-capture-v1'
MAX_URLS=4
MAX_BYTES=512*1024
TIMEOUT=5.0
HOSTS={
    'sec.gov':'SEC','www.sec.gov':'SEC',
    'federalreserve.gov':'Federal Reserve','www.federalreserve.gov':'Federal Reserve',
    'ecb.europa.eu':'ECB','www.ecb.europa.eu':'ECB',
    'binance.com':'Binance','www.binance.com':'Binance',
    'coinbase.com':'Coinbase','www.coinbase.com':'Coinbase','blog.coinbase.com':'Coinbase',
    'x.com':'X platform; author independence unknown','www.x.com':'X platform; author independence unknown',
}


class CaptureError(ValueError):
    pass


@dataclass(frozen=True)
class RetrievedSource:
    receipt: dict
    text: str
    artifact: Path


def canonical_url(value):
    if (not isinstance(value,str) or not 1<=len(value)<=2048
            or any(ch.isspace() or ord(ch)<32 for ch in value)
            or re.search(r'%0[ad]',value,re.I)):
        raise CaptureError('invalid_https_url')
    try:
        parsed=urlsplit(value)
        if (parsed.scheme!='https' or parsed.hostname not in HOSTS or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None,443)):
            raise CaptureError('host_or_scheme_not_allowed')
        if re.search(r'(?:^|&)(?:access_token|api_key|token|auth|session|password)=',parsed.query,re.I):
            raise CaptureError('authenticated_url_not_allowed')
        return urlunsplit(('https',parsed.hostname,parsed.path or '/',parsed.query,''))
    except (ValueError,TypeError) as exc:
        if isinstance(exc,CaptureError): raise
        raise CaptureError('invalid_https_url') from None


def _public_ip(value):
    try:
        address=ipaddress.ip_address(value)
        return address.is_global and not (address.is_multicast or address.is_unspecified
            or address.is_loopback or address.is_link_local or address.is_reserved)
    except ValueError:
        return False


def _resolve(host):
    addresses=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM,proto=socket.IPPROTO_TCP)
    if not addresses or any(not _public_ip(item[4][0]) for item in addresses):
        raise CaptureError('non_public_dns_address')
    return list(dict.fromkeys(item[4][0] for item in addresses))


def _download(url):
    """One GET, no redirects/proxies/cookies; DNS is pinned to checked public IPs."""
    deadline=time.monotonic()+TIMEOUT
    host=urlsplit(url).hostname
    addresses=_resolve(host)
    def remaining():
        value=deadline-time.monotonic()
        if value<=0: raise CaptureError('source_timeout')
        return value
    connection=http.client.HTTPSConnection(host,timeout=remaining())
    watchdog=None
    live_socket=None
    response=None
    def abort():
        sock=live_socket
        if sock is not None:
            try: sock.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            try: sock.close()
            except OSError: pass
    try:
        # Connecting to a numeric address avoids a second hostname resolution.
        raw=socket.create_connection((addresses[0],443),timeout=remaining())
        if raw.getpeername()[0]!=addresses[0] or not _public_ip(raw.getpeername()[0]):
            raw.close()
            raise CaptureError('peer_address_mismatch')
        try:
            connection.sock=ssl.create_default_context().wrap_socket(raw,server_hostname=host,
                                                                   do_handshake_on_connect=False)
        except Exception:
            raw.close()
            raise
        live_socket=connection.sock
        watchdog=threading.Timer(remaining(),abort);watchdog.daemon=True;watchdog.start()
        connection.sock.settimeout(remaining())
        connection.sock.do_handshake()
        parsed=urlsplit(url)
        connection.request('GET',parsed.path+('?' + parsed.query if parsed.query else ''),headers={
            'User-Agent':'MesaSiete-Research-Citation/1.0','Accept':'text/html,text/plain',
            'Accept-Encoding':'identity'})
        response=connection.getresponse()
        if 300<=response.status<400: raise CaptureError('redirect_not_allowed')
        if response.status!=200: raise CaptureError('http_status_not_200')
        headers={key.lower():value for key,value in response.getheaders()}
        if sum(len(k)+len(v) for k,v in headers.items())>32768:
            raise CaptureError('response_headers_too_large')
        media=headers.get('content-type','').split(';',1)[0].lower().strip()
        if media not in ('text/html','text/plain'): raise CaptureError('unreadable_content_type')
        if headers.get('content-encoding','identity').lower().strip() not in ('','identity'):
            raise CaptureError('compressed_content_not_allowed')
        try: length=int(headers.get('content-length','0'))
        except ValueError: raise CaptureError('invalid_content_length') from None
        if length<0 or length>MAX_BYTES: raise CaptureError('source_too_large')
        body=bytearray()
        while True:
            live_socket.settimeout(remaining())
            part=response.read1(min(16384,MAX_BYTES+1-len(body)))
            if not part: break
            body.extend(part)
            if len(body)>MAX_BYTES: raise CaptureError('source_too_large')
        remaining()
        return bytes(body),headers
    finally:
        if watchdog is not None: watchdog.cancel()
        if response is not None: response.close()
        connection.close()
        if live_socket is not None: live_socket.close()


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text=[];self.dates=[];self.ignored=0
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag in ('script','style','template','noscript'): self.ignored+=1
        if self.ignored: return
        key=(attrs.get('property') or attrs.get('name') or attrs.get('itemprop') or '').lower()
        if tag=='meta' and key in ('article:published_time','datepublished','pubdate','dc.date','dc.date.issued'):
            self.dates.append((attrs.get('content',''),'html-meta:'+key))
        if tag=='time' and attrs.get('itemprop','').lower()=='datepublished':
            self.dates.append((attrs.get('datetime',''),'html-time:datePublished'))
    def handle_endtag(self,tag):
        if tag in ('script','style','template','noscript'): self.ignored=max(0,self.ignored-1)
    def handle_data(self,data):
        if not self.ignored: self.text.append(data)


def normalized_text(value):
    return ' '.join(value.casefold().split())


def _extract(raw,headers,received):
    if not raw or len(raw)>MAX_BYTES: raise CaptureError('empty_or_oversize_content')
    match=re.search(r'charset\s*=\s*["\']?([A-Za-z0-9_-]+)',headers.get('content-type',''),re.I)
    encoding=match.group(1) if match else 'utf-8'
    try: decoded=raw.decode(encoding,errors='strict')
    except (LookupError,UnicodeError): raise CaptureError('unreadable_text_encoding') from None
    if headers.get('content-type','').split(';',1)[0].lower().strip()=='text/html':
        page=_Page();page.feed(decoded)
        text=' '.join(' '.join(page.text).split());dates=page.dates
    else:
        text=' '.join(decoded.split());dates=[]
    if len(text)<80 or any(token in normalized_text(text[:600]) for token in
        ('access denied','verify you are human','enable javascript','just a moment','log in to x','sign in to x')):
        raise CaptureError('article_content_unavailable')
    found=[];metadata=[]
    for value,method in dates[:20]:
        metadata.append(dict(value=value[:100],method=method,scope='publisher-declared metadata'))
        try:
            parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
            if parsed.tzinfo is not None: found.append(parsed.timestamp())
        except (ValueError,OverflowError): pass
    valid=bool(found) and max(found)-min(found)<=60 and 0<max(found)<=received+120
    return text,(max(found) if valid else None),metadata


def _store(raw,root):
    digest=hashlib.sha256(raw).hexdigest()
    folder=Path(root);folder.mkdir(parents=True,exist_ok=True)
    destination=folder/(digest+'.bin')
    if destination.exists():
        if hashlib.sha256(destination.read_bytes()).hexdigest()!=digest:
            raise CaptureError('stored_capture_integrity_failure')
    else:
        fd,name=tempfile.mkstemp(dir=folder,prefix='.capture-')
        try:
            with os.fdopen(fd,'wb') as output:
                output.write(raw);output.flush();os.fsync(output.fileno())
            try: os.link(name,destination)
            except FileExistsError:
                if destination.read_bytes()!=raw: raise CaptureError('stored_capture_integrity_failure')
        finally: Path(name).unlink(missing_ok=True)
    return destination,digest


def valid_capture(source,now):
    if not isinstance(source,RetrievedSource): return False
    receipt=source.receipt
    try:
        return (receipt.get('version')==VERSION and receipt.get('retrievalVerified') is True
            and canonical_url(receipt['url'])==receipt['url'] and 0<=now-receipt['retrievedAt']<=36*3600
            and source.artifact.stat().st_size<=MAX_BYTES
            and hashlib.sha256(source.artifact.read_bytes()).hexdigest()==receipt['contentSha256']
            and hashlib.sha256(normalized_text(source.text).encode()).hexdigest()==receipt['textSha256'])
    except (OSError,ValueError,TypeError,KeyError): return False


async def retrieve_urls(urls,*,root):
    """At most four distinct citation GETs, concurrently; no links discovered."""
    selected=[];failures=[];aliases={}
    for value in urls:
        try: url=canonical_url(value)
        except CaptureError as exc:
            failures.append(dict(url='[invalid or disallowed URL]',error=str(exc)));continue
        aliases[value]=url
        if url not in selected: selected.append(url)
    excess=selected[MAX_URLS:];selected=selected[:MAX_URLS]
    failures.extend(dict(url=url,error='task_url_budget') for url in excess)
    async def capture(url):
        try:
            raw,headers=await asyncio.wait_for(asyncio.to_thread(_download,url),TIMEOUT)
            received=time.time()
            text,published,metadata=_extract(raw,headers,received)
            artifact,digest=_store(raw,root)
            return RetrievedSource(dict(version=VERSION,url=url,retrievedAt=received,receivedAt=received,
                provenance='retrieved',contentSha256=digest,textSha256=hashlib.sha256(normalized_text(text).encode()).hexdigest(),
                artifact=artifact.name,bytes=len(raw),publisherGroup=HOSTS[urlsplit(url).hostname],
                publishedAt=published,publicationMetadata=metadata,retrievalVerified=True,
                factVerified=False,editorialIndependenceVerified=False,
                historicalAvailabilityReconstructed=False),text,artifact)
        except (asyncio.TimeoutError,TimeoutError): return dict(url=url,error='source_timeout')
        except CaptureError as exc: return dict(url=url,error=str(exc))
        except (OSError,ssl.SSLError,http.client.HTTPException,ValueError): return dict(url=url,error='source_retrieval_failed')
    fetched=await asyncio.gather(*(capture(url) for url in selected))
    captures=[];seen={}
    for item in fetched:
        if not isinstance(item,RetrievedSource): failures.append(item);continue
        digest=item.receipt['textSha256']
        if digest in seen: item.receipt['duplicateOf']=seen[digest]
        else: seen[digest]=item.receipt['url']
        captures.append(item)
    return dict(captures=captures,failures=failures,aliases=aliases,requestedUrls=len(selected),
                retrievedUrls=len(captures),uniqueContents=len(seen),maximumUrls=MAX_URLS,
                timeoutSeconds=TIMEOUT,maximumBytes=MAX_BYTES,redirectPolicy='reject-all',
                factVerified=False,editorialIndependenceVerified=False)
