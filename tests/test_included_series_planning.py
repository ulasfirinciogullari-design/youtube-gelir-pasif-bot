"""Native planning keeps raw observations, fixed source identities and brief limits."""
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from app.services import included_series_planning as planning, included_research_sources as sources
from app.services import production_next_series as series, production_included_router as router
from app.services.production_spend import SpendBlocked

URLS = ['https://www.ibm.com/history/upc', 'https://www.gs1us.org/upcs-barcodes-prefixes/barcode-types']
CONTEXT = {'language': 'en', 'channel_identity': 'Business mini-documentaries',
    'channel_title': 'Margin Verdict', 'channel_bio': '', 'current_series_title': 'Earlier choices',
    'existing_topics': ['A previously consumed subject https://www.bep.gov/currency/serial-numbers']}


def page(url, index=0):
    return {'url': url, 'text': 'Actually retrieved primary evidence describing product identification at checkout.',
        'text_sha256': str(index) * 64, 'category': 'evergreen_primary'}


def answer():
    return {'can_prepare': True, 'language': 'en', 'series_title': 'The checkout decision',
        'briefs': [{'question': 'How did scanning change the grocery business?', 'sources': [
            {'source_id': 's01', 'evidence': 'The retrieved IBM history describes how grocery industry demand led to the UPC.'},
            {'source_id': 's02', 'evidence': 'The retrieved GS1 US page explains how scanning identifies physical products.'}]}]}


@pytest.fixture
def generate(monkeypatch):
    pages = [page(url, i) for i, url in enumerate(URLS)]
    payload = answer()
    call = Mock(side_effect=lambda *a, **kw: deepcopy(payload))
    monkeypatch.setattr(planning, 'read_planning_pages', lambda: deepcopy(pages))
    monkeypatch.setattr(router, 'generate_text_json', call)
    return pages, payload, call


def test_real_native_entry_maps_ids_to_two_literal_urls_and_preserves_observed_reply(generate):
    pages, payload, call = generate
    before = deepcopy(payload)
    output = series._generate(CONTEXT, ('abacus_included', 'route-llm', 'unused'))
    assert payload == before and call.call_count == 1
    assert call.call_args.kwargs == {'purpose': 'next_series'}
    prompt, schema = call.call_args.args
    assert 'Evergreen/historical source pages are not current news' in prompt
    assert 'No financial advice' in prompt
    assert schema['properties']['briefs']['items']['properties']['sources']['minItems'] == 2
    question = payload['briefs'][0]['question']
    assert output['briefs'][0]['brief'] == question + ' ' + ' '.join(URLS)
    assert len(output['briefs'][0]['brief']) <= 240
    assert [row['url'] for row in output['briefs'][0]['sources']] == URLS
    checked = series._validate_output(output, CONTEXT)
    assert checked['briefs'] == output['briefs'] and 'qa_approved' not in output


@pytest.mark.parametrize('damage', ['url_in_question', 'too_long', 'source_id', 'duplicate_id', 'url_field',
    'missing_source', 'empty_evidence', 'added_approval', 'wrong_language', 'false_with_briefs'])
def test_malformed_observed_output_is_never_rewritten_retried_or_promoted(generate, damage):
    _, payload, call = generate
    item = payload['briefs'][0]
    if damage == 'url_in_question': item['question'] = 'Read an invented source https://www.ibm.com/not-read'
    elif damage == 'too_long': item['question'] = 'A' * 241
    elif damage == 'source_id': item['sources'][0]['source_id'] = 'never-read'
    elif damage == 'duplicate_id': item['sources'][1]['source_id'] = 's01'
    elif damage == 'url_field': item['sources'][0]['url'] = 'https://www.ibm.com/not-read'
    elif damage == 'missing_source': item['sources'].pop()
    elif damage == 'empty_evidence': item['sources'][0]['evidence'] = ''
    elif damage == 'added_approval': payload['qa_approved'] = True
    elif damage == 'wrong_language': payload['language'] = 'tr'
    elif damage == 'false_with_briefs': payload['can_prepare'] = False
    before = deepcopy(payload)
    with pytest.raises(series._InvalidOutput): series._generate(CONTEXT, ('abacus_included', 'route-llm', 'unused'))
    assert call.call_count == 1 and payload == before


def test_negative_planner_verdict_remains_negative(generate):
    _, payload, call = generate
    payload.update(can_prepare=False, series_title='', briefs=[])
    result = series._generate(CONTEXT, ('abacus_included', 'route-llm', 'unused'))
    assert result == payload and series._validate_output(result, CONTEXT) is None and call.call_count == 1


def test_unknown_model_outcome_is_not_classified_as_a_definitive_rejection(generate):
    _, _, call = generate
    call.side_effect = SpendBlocked('included_router_response_unverified')
    with pytest.raises(SpendBlocked): series._generate(CONTEXT, ('abacus_included', 'route-llm', 'unused'))
    assert call.call_count == 1


@pytest.mark.parametrize('urls', [URLS[:1], [URLS[0], URLS[0]], [URLS[0], 'https://127.0.0.1/private']])
def test_inadequate_or_untrusted_sources_stop_before_any_model_reservation(generate, urls):
    pages, _, call = generate
    pages[:] = [page(url, i) for i, url in enumerate(urls)]
    with pytest.raises((series._InvalidOutput, SpendBlocked)):
        series._generate(CONTEXT, ('abacus_included', 'route-llm', 'unused'))
    call.assert_not_called()


def test_dynamic_question_budget_accounts_for_two_long_urls_without_truncation():
    urls = ['https://www.federalreserve.gov/newsevents/pressreleases/enforcement20260918a.htm',
        'https://www.federalreserve.gov/newsevents/pressreleases/enforcement20260918b.htm']
    indexed, limit, schema = planning._contract([page(url, i) for i, url in enumerate(urls)], 'en')
    assert limit == 240 - sum(map(len, urls)) - 2
    payload = answer();payload['briefs'][0]['question'] = 'A' * limit
    output = planning._decode(payload, indexed, limit, 'en')
    assert len(output['briefs'][0]['brief']) == 240
    payload['briefs'][0]['question'] += 'A'
    with pytest.raises(ValueError): planning._decode(payload, indexed, limit, 'en')


def test_source_reader_keeps_news_and_evergreen_origins_bounded_and_skips_unreadable_pages(monkeypatch):
    news = 'https://www.federalreserve.gov/newsevents/pressreleases/enforcement20260918a.htm'
    candidates = [{'url': news + 'x' * 200, 'published_at': '2026-09-18'},
        {'url': news, 'published_at': '2026-09-18'}]
    calls = []
    monkeypatch.setattr(sources, 'feed_candidates', lambda: candidates)
    def fetch(url):
        calls.append(url)
        if url == planning.EVERGREEN_SOURCES[2]: raise SpendBlocked('unavailable')
        return {key: value for key, value in page(url).items() if key != 'category'}
    monkeypatch.setattr(sources, 'fetch_page', fetch)
    pages = planning.read_planning_pages()
    assert len(calls) == 5 and len(pages) == 4
    assert next(row for row in pages if row['url'] == news)['category'] == 'recent_official_news'
    assert all(row['category'] == 'evergreen_primary' and 'published_at' not in row
        for row in pages if row['url'] != news)
    assert len({row['url'] for row in pages}) == len(pages)


def test_duplicate_topic_remains_rejected_after_url_mapping(generate):
    _, _, _call = generate
    output = planning.generate(CONTEXT)
    repeated = {**CONTEXT, 'existing_topics': [output['briefs'][0]['brief']]}
    with pytest.raises(ValueError): series._validate_output(output, repeated)
