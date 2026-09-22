import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import fakeredis
import pytest

from app.services import historical_source_cache as cache, included_research_sources as sources
from app.services.production_spend import SpendBlocked

NOW = datetime(2026, 9, 22, 11, tzinfo=timezone.utc)


def record(url=cache.URLS[0], *, when=NOW):
    body = ('<main>Historical primary source material. ' * 20 + '</main>').encode()
    return {'version': 1, 'url': url, 'mime': 'text/html', 'retrieved_at': when.isoformat(),
        'body_sha256': hashlib.sha256(body).hexdigest(), 'body_base64': base64.b64encode(body).decode()}


def test_real_http_capture_preserves_body_url_and_date(monkeypatch):
    original = record(); body = base64.b64decode(original['body_base64'])
    monkeypatch.setattr(sources, '_get', lambda url: (url, body, 'text/html'))
    saved = cache.capture(cache.URLS[0])
    assert saved['body_base64'] == original['body_base64'] and saved['body_sha256'] == original['body_sha256']
    assert datetime.fromisoformat(saved['retrieved_at']).tzinfo is not None


def test_unavailable_direct_read_uses_dated_original_and_never_relabels_it_as_fresh(monkeypatch):
    from app.services import content_plan
    c = fakeredis.FakeRedis(decode_responses=True); value = record()
    cache.install(c, value, now=NOW)
    before = {k: c.dump(k) for k in c.scan_iter()}
    monkeypatch.setattr(content_plan, '_client', lambda: c)
    monkeypatch.setattr(sources, '_get', lambda url: (_ for _ in ()).throw(SpendBlocked('source_unavailable')))
    page = sources.fetch_page(value['url'], now=NOW + timedelta(hours=30))
    assert page['retrieved_at'] == value['retrieved_at'] and page['body_sha256'] == value['body_sha256']
    assert page['observation']['method'] == 'dated_primary_source_cache'
    assert value['retrieved_at'] in sources.source_prompt([page])
    assert {k: c.dump(k) for k in c.scan_iter()} == before
    with pytest.raises(SpendBlocked): sources.fetch_page(value['url'], now=NOW + timedelta(hours=73))


@pytest.mark.parametrize('damage', ['body', 'hash', 'future', 'stale', 'wrong_url', 'mime', 'oversized', 'empty'])
def test_invalid_or_unreviewed_source_cannot_be_installed(damage):
    c = fakeredis.FakeRedis(decode_responses=True); value = record()
    if damage == 'body': value['body_base64'] = base64.b64encode(b'different').decode()
    if damage == 'hash': value['body_sha256'] = 'f' * 64
    if damage == 'future': value['retrieved_at'] = (NOW + timedelta(hours=1)).isoformat()
    if damage == 'stale': value['retrieved_at'] = (NOW - timedelta(days=4)).isoformat()
    if damage == 'wrong_url': value['url'] = 'https://www.sony.com/not-reviewed'
    if damage == 'mime': value['mime'] = 'application/javascript'
    if damage == 'oversized': value['body_base64'] = 'a' * 1_400_001
    if damage == 'empty': value['body_base64'] = ''
    with pytest.raises(SpendBlocked): cache.install(c, value, now=NOW)
    assert c.dbsize() == 0


def test_refresh_retains_prior_snapshot_and_old_delayed_refresh_cannot_replace_latest():
    c = fakeredis.FakeRedis(decode_responses=True); first = record()
    cache.install(c, first, now=NOW)
    old_records = {k: c.get(k) for k in c.scan_iter(match=cache.PREFIX + 'record:*')}
    second = record(when=NOW + timedelta(hours=12))
    cache.install(c, second, now=NOW + timedelta(hours=12))
    assert cache.install(c, first, now=NOW + timedelta(hours=12))['status'] == 'already_current'
    assert cache.read(first['url'], client=c, now=NOW + timedelta(hours=12))['retrieved_at'] == second['retrieved_at']
    assert all(c.get(k) == v for k, v in old_records.items())
    pointer = cache.PREFIX + 'url:' + cache._sha(first['url'].encode())
    key = cache.PREFIX + 'record:' + c.get(pointer)
    damaged = json.loads(c.get(key)); damaged['body_sha256'] = 'a' * 64
    c.set(key, cache._raw(damaged))
    with pytest.raises(SpendBlocked): cache.read(first['url'], client=c, now=NOW + timedelta(hours=12))
