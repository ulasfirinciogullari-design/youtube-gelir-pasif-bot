from unittest.mock import Mock
import pytest

from app.services import included_source_discovery as discovery
from app.services.production_spend import SpendBlocked


def response(body):
    return discovery.INDEX, body.encode(), 'text/html'


def test_index_parser_only_admits_observed_same_collection_articles(monkeypatch):
    good = [discovery.INDEX + slug + '/' for slug in ('price-wars', 'quality', 'wood')]
    urls = [good[0], good[1], good[0], good[2], discovery.INDEX + 'CTA_URL',
        discovery.INDEX + 'URL/', discovery.INDEX + 'wood/?q=secret',
        discovery.INDEX + 'wood/#fragment', discovery.INDEX + 'a/b/',
        'https://127.0.0.1/private/', 'https://example.org/wood/',
        'http://ikeamuseum.com/en/explore/the-story-of-ikea/wood/',
        discovery.INDEX + 'a' * 100 + '/']
    get = Mock(return_value=response(''.join(f'<a href="{url}">article</a>' for url in urls)))
    monkeypatch.setattr(discovery.sources, '_get', get)
    rows = discovery.discover_candidates([], 0)
    assert [row['url'] for row in rows] == good[:2]
    rows = discovery.discover_candidates(['consumed ' + good[0]], 0)
    assert [row['url'] for row in rows] == good[1:]
    assert all(set(row) == {'url', 'category'} for row in rows)
    get.assert_called_with(discovery.INDEX)


def test_negative_windows_walk_the_bounded_index_and_stop_at_link_cap(monkeypatch):
    monkeypatch.setattr(discovery, 'COLLECTIONS', (discovery.COLLECTIONS[0],))
    links = [discovery.INDEX + f'item-{i}/' for i in range(140)]
    monkeypatch.setattr(discovery.sources, '_get', lambda url: response(
        ''.join(f'<a href="{link}">item</a>' for link in links)))
    seen = {row['url'] for rotation in range(60)
        for row in discovery.discover_candidates([], rotation)}
    assert seen == set(links[:discovery.MAX_LINKS])


@pytest.mark.parametrize('case', ['unavailable', 'redirected', 'wrong_type', 'one_link'])
def test_unavailable_or_untrusted_discovery_returns_no_approved_sources(monkeypatch, case):
    result = response('<a href="quality/">one</a>')
    if case == 'redirected': result = ('https://example.org/index', result[1], result[2])
    if case == 'wrong_type': result = (result[0], result[1], 'application/json')
    get = Mock(return_value=result)
    if case == 'unavailable': get.side_effect = SpendBlocked('source_unavailable')
    monkeypatch.setattr(discovery.sources, '_get', get)
    assert discovery.discover_candidates([], 0) == []


@pytest.mark.parametrize('rotation', [True, -1, 240, '3'])
def test_bad_selection_never_fetches(monkeypatch, rotation):
    get = Mock();monkeypatch.setattr(discovery.sources, '_get', get)
    with pytest.raises(ValueError): discovery.discover_candidates([], rotation)
    get.assert_not_called()
