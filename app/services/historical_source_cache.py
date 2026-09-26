"""Private, dated copies of explicitly reviewed public historical references.

An operations process on the existing VPS retrieves the original bounded HTTP
body. No route accepts user/model supplied snapshots. A cached page is source
material, never a factual approval; all sentence and visual reviews still run.
"""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json

from app.services.production_spend import SpendBlocked

PREFIX = 'youtube_studio:historical_sources:v1:'
URLS = (
    'https://www.sony.com/en/SonyInfo/CorporateInfo/History/sonyhistory-e.html',
    'https://www.sony.com/en/SonyInfo/News/Press/199907/99-059/',
    'https://www.sony.com/en/SonyInfo/CorporateInfo/History/SonyHistory/1-01.html',
    'https://corporate.mcdonalds.com/corpmcd/our-stories/article/first-mcd-drivethru.html',
    'https://corporate.mcdonalds.com/corpmcd/our-stories/article/mcdonalds-commitment-to-making-an-impact-inthe-community.html',
)
MAX_AGE = timedelta(hours=72)


def _require(value):
    if not value:
        raise SpendBlocked('included_historical_source_unavailable')


def _raw(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def capture(url):
    from app.services import included_research_sources as sources
    _require(url in URLS)
    actual, body, mime = sources._get(url)
    _require(actual == url)
    return {'version': 1, 'url': url, 'mime': mime,
        'retrieved_at': datetime.now(timezone.utc).isoformat(),
        'body_sha256': _sha(body), 'body_base64': base64.b64encode(body).decode('ascii')}


def _checked(record, url, now):
    from app.services import included_research_sources as sources
    _require(url in URLS and type(record) is dict and set(record) == {
        'version', 'url', 'mime', 'retrieved_at', 'body_sha256', 'body_base64'}
        and type(record['version']) is int and record['version'] == 1 and record['url'] == url
        and record['mime'] in ('text/html', 'application/xhtml+xml', 'text/plain')
        and type(record['body_base64']) is str and len(record['body_base64']) <= 1_400_000)
    try:
        date = datetime.fromisoformat(record['retrieved_at'])
        body = base64.b64decode(record['body_base64'], validate=True)
    except (ValueError, TypeError):
        raise SpendBlocked('included_historical_source_unavailable') from None
    _require(date.tzinfo is not None and now - MAX_AGE <= date <= now + timedelta(minutes=5)
        and 200 <= len(body) <= sources.MAX_BYTES and _sha(body) == record['body_sha256'])
    page = sources.page_from_body(url, body, record['mime'], date)
    page['observation'] = {'method': 'dated_primary_source_cache',
        'retrieved_at': record['retrieved_at'], 'body_sha256': record['body_sha256']}
    return page


def install(client, record, *, now=None):
    """Called only by authenticated server operations; keep every old snapshot."""
    now = now or datetime.now(timezone.utc)
    page = _checked(record, record.get('url'), now)
    raw = _raw(record); digest = _sha(raw.encode())
    key = PREFIX + 'record:' + digest
    if not client.set(key, raw, nx=True):
        _require(client.get(key) == raw)
    pointer = PREFIX + 'url:' + _sha(record['url'].encode())
    # An older delayed refresh must not replace a newer observation.
    with client.pipeline() as pipe:
        pipe.watch(pointer)
        prior = pipe.get(pointer)
        if prior is not None:
            old = json.loads(pipe.get(PREFIX + 'record:' + prior))
            if datetime.fromisoformat(old['retrieved_at']) >= datetime.fromisoformat(record['retrieved_at']):
                return {'status': 'already_current', 'url': record['url']}
        pipe.multi(); pipe.set(pointer, digest)
        _require(pipe.execute() == [True])
    return {'status': 'installed', 'url': record['url'], 'body_sha256': page['body_sha256'],
            'retrieved_at': record['retrieved_at']}


def read(url, *, now=None, client=None):
    from app.services.content_plan import _client
    _require(url in URLS)
    client = client or _client(); now = now or datetime.now(timezone.utc)
    try:
        digest = client.get(PREFIX + 'url:' + _sha(url.encode()))
        _require(type(digest) is str and len(digest) == 64)
        raw = client.get(PREFIX + 'record:' + digest)
        _require(type(raw) is str and len(raw) <= 1_500_000 and _sha(raw.encode()) == digest)
        return _checked(json.loads(raw), url, now)
    except SpendBlocked:
        raise
    except Exception:
        raise SpendBlocked('included_historical_source_unavailable') from None
