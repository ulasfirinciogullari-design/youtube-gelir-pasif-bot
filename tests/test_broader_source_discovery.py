"""Fresh source windows survive one unavailable family without paid retries."""
from unittest.mock import Mock

import pytest

from app.services import included_source_discovery as discovery
from app.services import included_series_planning as planning
from app.services.production_spend import SpendBlocked


def articles(collection):
    index, prefix, trailing = collection
    return [prefix + slug + ('/' if trailing else '') for slug in ('first-story', 'second-story', 'third-story', 'fourth-story')]


def response(index):
    collection = next(row for row in discovery.COLLECTIONS if row[0] == index)
    links = articles(collection)
    return index, ''.join(f'<a href="{url}">Read</a>' for url in links).encode(), 'text/html'


def test_distinct_negative_windows_visit_all_families_then_new_articles(monkeypatch):
    get = Mock(side_effect=response)
    monkeypatch.setattr(discovery.sources, '_get', get)
    width = len(discovery.COLLECTIONS)
    for slot in range(width * 2):
        collection = discovery.COLLECTIONS[slot % width]
        rows = discovery.discover_candidates([], slot)
        assert [row['url'] for row in rows] == articles(collection)[(slot // width) * 2:][:2]
        assert all(row['category'] == 'discovered_primary' for row in rows)
    assert get.call_count == width * 2
    assert [c.args[0] for c in get.call_args_list[:width]] == [row[0] for row in discovery.COLLECTIONS]


def test_unavailable_family_does_not_trigger_http_or_paid_retries(monkeypatch):
    def fetch(index):
        if index == discovery.COLLECTIONS[1][0]:
            raise SpendBlocked('unavailable')
        return response(index)
    get = Mock(side_effect=fetch)
    monkeypatch.setattr(discovery.sources, '_get', get)
    assert discovery.discover_candidates([], 1) == []
    assert [row['url'] for row in discovery.discover_candidates([], 2)] == articles(discovery.COLLECTIONS[2])[:2]
    assert get.call_count == 2


@pytest.mark.parametrize('collection', discovery.COLLECTIONS)
def test_every_collection_rejects_unobserved_destinations_and_prefers_unused_articles(monkeypatch, collection):
    index, prefix, trailing = collection
    good = articles(collection)
    invalid = [index, good[0] + '?q=1', good[0] + '#part', prefix + '../elsewhere',
        prefix + 'nested/path', prefix + 'CTA_URL', 'https://example.com/fake',
        'https://user:password@' + good[0].split('://')[1], good[0].replace('https:', 'http:'),
        prefix + 'x' * 100, good[0].replace('.com/', '.com.evil.example/'),
        'https://127.0.0.1/private']
    html = ''.join(f'<a href="{url}">Read</a>' for url in [*invalid, *good, good[0]])
    get = Mock(return_value=(index, html.encode(), 'text/html'))
    monkeypatch.setattr(discovery.sources, '_get', get)
    rows = discovery.discover_candidates(['Already tried ' + good[0]], discovery.COLLECTIONS.index(collection))
    assert [row['url'] for row in rows] == good[1:3]
    get.assert_called_once_with(index)


def test_discovery_links_are_fetched_before_entering_planning_evidence(monkeypatch):
    monkeypatch.setattr(discovery.sources, '_get', response)
    monkeypatch.setattr(planning.sources, 'feed_candidates', lambda: [])
    observed = []
    def fetch(url):
        observed.append(url)
        return {'url': url, 'text': 'Retrieved primary source, not the index link.', 'text_sha256': 'a' * 64}
    monkeypatch.setattr(planning.sources, 'fetch_page', fetch)
    rows = planning.read_planning_pages({'source_rotation': len(planning.SOURCE_PAIRS) + 1})
    expected = articles(discovery.COLLECTIONS[1])[:2]
    assert len(observed) == 4 and set(expected) <= set(observed)
    assert [row['url'] for row in rows if row['category'] == 'discovered_primary'] == expected
    assert all(row['text'] == 'Retrieved primary source, not the index link.' for row in rows)
    _, limit, _ = planning._contract(rows, 'tr')
    assert limit >= 20
