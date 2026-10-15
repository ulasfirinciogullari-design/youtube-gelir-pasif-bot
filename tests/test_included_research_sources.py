from datetime import datetime, timezone
import hashlib

import pytest

from app.services import included_research_sources as sources
from app.services.production_spend import SpendBlocked

NOW = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
URL = 'https://www.federalreserve.gov/newsevents/pressreleases/monetary20260918a.htm'


@pytest.mark.parametrize('url', ['http://www.usmint.gov/news', 'https://www.usmint.gov.evil.test/a',
    'https://127.0.0.1/a', 'https://user@www.usmint.gov/a', 'https://www.usmint.gov:8443/a',
    'https://www.usmint.gov/a?secret=1', 'https://www.usmint.gov/a#x', 'https://www.usmint.gov/\nx'])
def test_unapproved_source_urls_rejected(url):
    with pytest.raises(SpendBlocked): sources._url(url)


def test_main_article_read_and_observation_preserved_without_navigation(monkeypatch):
    text = 'A documented central bank decision with the precise date and source evidence. ' * 8
    raw = ('<html><nav>Do not cite navigation</nav><main><h1>Decision</h1><p>' + text
           + '</p><script>untrustedScript()</script></main><footer>Footer</footer></html>').encode()
    monkeypatch.setattr(sources, '_get', lambda url: (url, raw, 'text/html'))
    page = sources.fetch_page(URL, now=NOW)
    assert page['url'] == URL and page['body_sha256'] == hashlib.sha256(raw).hexdigest()
    assert 'Decision' in page['text'] and 'navigation' not in page['text'] and 'untrustedScript' not in page['text']
    assert sources.source_prompt([page]) == sources.source_prompt([
        {**page, 'retrieved_at': 'later observation', 'body_sha256': 'changed HTML navigation'}])
    sources.consulted_sources_only([{'url': URL, 'evidence': 'source fact'}], [page])
    with pytest.raises(SpendBlocked, match='unconsulted_source'):
        sources.consulted_sources_only([{'url': 'https://www.usmint.gov/not-read'}], [page])


def test_recent_feed_only_deduplicates_and_never_treats_title_as_retrieved_evidence(monkeypatch):
    body = ('<rss><channel><item><title>Current policy decision</title><link>' + URL
        + '</link><pubDate>Fri, 18 Sep 2026 12:00:00 GMT</pubDate></item>'
        '<item><title>Old decision</title><link>https://www.federalreserve.gov/old.htm</link>'
        '<pubDate>Mon, 01 Jun 2026 12:00:00 GMT</pubDate></item>'
        '<item><title>Unapproved link</title><link>https://evil.test/a</link>'
        '<pubDate>Fri, 18 Sep 2026 12:00:00 GMT</pubDate></item></channel></rss>').encode()
    monkeypatch.setattr(sources, '_get', lambda url: (url, body, 'application/rss+xml'))
    result = sources.feed_candidates(now=NOW)
    assert len(result) == 1 and result[0]['url'] == URL and 'text' not in result[0]
    monkeypatch.setattr(sources, '_get', lambda url: (url, b'<!DOCTYPE rss><rss/>', 'text/xml'))
    assert sources.feed_candidates(now=NOW) == []


def test_primary_source_requirement_does_not_match_unrelated_word_fragments(monkeypatch):
    calls = []
    monkeypatch.setattr(sources, 'fetch_page', lambda url, **kw: calls.append(url) or {'url': url})
    with pytest.raises(SpendBlocked, match='primary_source_required'):
        sources.research_pages('Present central planning')
    assert calls == []
    assert len(sources.research_pages('Bir sent neden pahalı?')) == 3
    calls.clear()
    assert sources.research_pages('Read ' + URL + '.') == [{'url': URL}]


def test_network_reader_rejects_private_dns_before_http(monkeypatch):
    from contextlib import nullcontext
    from unittest.mock import Mock
    client = Mock()
    monkeypatch.setattr(sources.httpx, 'Client', lambda **kwargs: nullcontext(client))
    monkeypatch.setattr(sources.socket, 'getaddrinfo', lambda *a, **kw: [(2, 1, 6, '', ('127.0.0.1', 443))])
    with pytest.raises(SpendBlocked): sources._get(URL)
    client.stream.assert_not_called()


@pytest.mark.parametrize('page_count', [0, 1, 2])
def test_research_requires_two_distinct_retrieved_pages_before_a_provider_call(monkeypatch, page_count):
    from app.services import research, production_included_router as router
    from unittest.mock import Mock
    pages = [{'url': URL, 'text': 'Primary source material', 'text_sha256': 'a' * 64}] * page_count
    generate = Mock(side_effect=AssertionError('A model must not invent a missing second source'))
    monkeypatch.setattr(research, '_studio_plan_provider', lambda: 'abacus_included')
    monkeypatch.setattr(sources, 'research_pages', lambda topic: pages)
    monkeypatch.setattr(router, 'generate_text_json', generate)
    with pytest.raises(SpendBlocked, match='primary_source_unavailable'):
        research.research_and_script('A source-backed historic event ' + URL, .5, 'tr')
    generate.assert_not_called()


def test_source_schema_limits_model_to_observed_urls_without_changing_legacy_contract():
    from app.services import research
    from copy import deepcopy
    second = 'https://www.bep.gov/currency/serial-numbers'
    legacy = research._research_json_schema(6, exact_scene_count=True)
    before = deepcopy(legacy)
    result = sources.consulted_source_schema(legacy, [{'url': URL}, {'url': second}])
    assert legacy == before
    assert result['properties']['sources']['items']['properties']['url']['enum'] == sorted([URL, second])
    assert result['properties']['sources']['minItems'] == result['properties']['sources']['maxItems'] == 2
    assert 'enum' not in legacy['properties']['sources']['items']['properties']['url']
    # Even if a model ignores the schema, the post-response check stays strict.
    with pytest.raises(SpendBlocked, match='unconsulted_source'):
        sources.consulted_sources_only([{'url': 'https://www.bep.gov/not-read'}], [{'url': URL}, {'url': second}])


def test_unavailable_goldman_museum_references_use_only_actually_read_official_backups(monkeypatch):
    from app.services import research
    reads = []
    def fetch(url, **kwargs):
        reads.append(url)
        if url not in sources.GOLDMAN_BACKUP_SOURCES:
            raise SpendBlocked('included_research_source_unavailable')
        return {'url': url, 'text': 'Synthetic museum material for this source only.',
                'text_sha256': hashlib.sha256(url.encode()).hexdigest()}
    monkeypatch.setattr(sources, 'fetch_page', fetch)
    topic = 'Goldman shopping carts introduced in 1937. ' + sources.GOLDMAN_REFERENCE
    pages = sources.research_pages(topic)
    assert [p['url'] for p in pages] == list(sources.GOLDMAN_BACKUP_SOURCES)
    assert reads == [sources.GOLDMAN_REFERENCE, sources.COMPANIONS[sources.GOLDMAN_REFERENCE],
                     *sources.GOLDMAN_BACKUP_SOURCES]
    schema = sources.consulted_source_schema(research._research_json_schema(6, exact_scene_count=True), pages)
    assert schema['properties']['sources']['items']['properties']['url']['enum'] == sorted(sources.GOLDMAN_BACKUP_SOURCES)
    assert schema['properties']['sources']['minItems'] == 2
    with pytest.raises(SpendBlocked, match='unconsulted_source'):
        sources.consulted_sources_only([{'url': sources.GOLDMAN_REFERENCE}], pages)
    # A shopping word without an authored reference does not trigger a search
    # or a new topic. In particular, the existing episode brief stays intact.
    reads.clear()
    with pytest.raises(SpendBlocked, match='primary_source_required'):
        sources.research_pages('A shopping cart without a cited source')
    assert reads == []


@pytest.mark.parametrize('url', [
    'https://www.okhistory.org/publications/enc/entry?entry=GO005',
    'https://www.okhistory.org/publications/enc/entry?entry=GO004&next=https://127.0.0.1',
    'https://www.okhistory.org/publications/enc/entry?entry=GO004#extra',
    'https://www.okhistory.org/publications/enc/entry?entry=GO004&entry=GO004',
    'https://www.okhistory.org/historycenter/atour?entry=GO004',
    'https://www.okhistory.org.evil.test/publications/enc/entry?entry=GO004',
    'http://www.okhistory.org/publications/enc/entry?entry=GO004',
])
def test_goldman_query_exception_does_not_allow_other_queries_hosts_or_protocols(url):
    assert sources._url(sources.GOLDMAN_BACKUP_SOURCES[1]) == sources.GOLDMAN_BACKUP_SOURCES[1]
    with pytest.raises(SpendBlocked): sources._url(url)


def test_one_available_cart_backup_still_cannot_start_a_model_call(monkeypatch):
    from app.services import research, production_included_router as router
    from unittest.mock import Mock
    def fetch(url, **kwargs):
        if url != sources.GOLDMAN_BACKUP_SOURCES[0]:
            raise SpendBlocked('included_research_source_unavailable')
        return {'url': url, 'text': 'Only one synthetic consulted source.', 'text_sha256': 'a' * 64}
    generate = Mock(side_effect=AssertionError('one source cannot fund a research call'))
    monkeypatch.setattr(sources, 'fetch_page', fetch)
    monkeypatch.setattr(research, '_studio_plan_provider', lambda: 'abacus_included')
    monkeypatch.setattr(router, 'generate_text_json', generate)
    with pytest.raises(SpendBlocked, match='primary_source_unavailable'):
        research.research_and_script('Goldman ' + sources.GOLDMAN_REFERENCE, .5, 'tr')
    generate.assert_not_called()
