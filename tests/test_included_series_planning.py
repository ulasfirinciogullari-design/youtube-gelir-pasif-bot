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


@pytest.fixture(autouse=True)
def no_live_discovery(monkeypatch):
    monkeypatch.setattr(planning, 'discover_candidates', lambda history, rotation: [])


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
    monkeypatch.setattr(planning, 'read_planning_pages', lambda context=None: deepcopy(pages))
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


@pytest.mark.parametrize('title', ['Earlier choices', '  Earlier   choices  '])
def test_repeated_series_label_uses_literal_existing_question_without_another_call(generate, title):
    _, payload, call = generate
    payload['series_title'] = title
    observed = deepcopy(payload)
    result = planning.generate(CONTEXT)
    assert payload == observed and call.call_count == 1
    assert result['series_title'] == payload['briefs'][0]['question']
    assert result['briefs'][0]['brief'] == payload['briefs'][0]['question'] + ' ' + ' '.join(URLS)
    assert series._validate_output(result, CONTEXT)['briefs'] == result['briefs']
    # Correcting a label cannot admit a repeated topic or grant media approval.
    repeated = {**CONTEXT, 'existing_topics': [result['briefs'][0]['brief']]}
    with pytest.raises(ValueError): series._validate_output(result, repeated)
    assert 'qa_approved' not in result and 'media_budget_approved' not in result


def test_overlong_question_is_never_shortened_to_create_a_series_label(generate):
    pages, payload, _ = generate
    payload['series_title'] = CONTEXT['current_series_title']
    payload['briefs'][0]['question'] = 'A' * 101
    indexed, limit, _ = planning._contract(pages, 'en')
    decoded = planning._decode(payload, indexed, limit, 'en')
    before = deepcopy(decoded)
    result = planning.label_batch(decoded, payload, CONTEXT)
    assert result == before and decoded == before
    with pytest.raises(ValueError): series._validate_output(result, CONTEXT)


def test_unused_documentary_outline_is_preserved_in_observation_without_becoming_approval(generate):
    _, payload, call = generate
    payload['briefs'][0]['video_plan'] = {'a_roll_speech_outline': 'Explain the sourced business trade-off.',
        'b_roll_footage_setting': 'Modern grocery checkout.'}
    before = deepcopy(payload)
    output = planning.generate(CONTEXT)
    assert payload == before and call.call_count == 1
    assert set(output['briefs'][0]) == {'brief', 'sources'}
    payload['briefs'][0]['video_plan']['qa_approved'] = True
    with pytest.raises(ValueError): planning.generate(CONTEXT)


def test_source_families_rotate_using_older_series_even_after_current_profile_changed(monkeypatch):
    monkeypatch.setattr(sources, 'feed_candidates', lambda: [])
    seen = []
    def fetch(url):
        seen.append(url);return page(url)
    monkeypatch.setattr(sources, 'fetch_page', fetch)
    context = {**CONTEXT, 'existing_topics': ['New current question'],
        'previous_topics': ['Old question ' + ' '.join(pair) for pair in planning.SOURCE_PAIRS[:2]]}
    pages = planning.read_planning_pages(context)
    assert {row['url'] for row in pages} == {url for pair in planning.SOURCE_PAIRS[2:4] for url in pair}
    assert len(seen) == 4


def test_finished_negative_attempts_explore_every_source_family_without_a_new_topic(monkeypatch):
    monkeypatch.setattr(sources, 'feed_candidates', lambda: [])
    monkeypatch.setattr(sources, 'fetch_page', lambda url: page(url))
    windows = []
    for slot in range(len(planning.SOURCE_PAIRS)):
        rows = planning.read_planning_pages({'source_rotation': slot})
        assert len(rows) == 4
        windows.append(tuple(row['url'] for row in rows))
    assert len(set(windows)) == len(planning.SOURCE_PAIRS)
    assert {url for window in windows for url in window} == {url for pair in planning.SOURCE_PAIRS for url in pair}
    assert tuple(row['url'] for row in planning.read_planning_pages(
        {'source_rotation': len(planning.SOURCE_PAIRS)})) == windows[0]
    assert all(planning._contract([page(url) for url in window], 'en')[1] >= 20 for window in windows)


@pytest.mark.parametrize('rotation', [True, -1, 240, '3'])
def test_invalid_rotation_never_fetches_sources(monkeypatch, rotation):
    fetch = Mock();monkeypatch.setattr(sources, 'fetch_page', fetch)
    with pytest.raises(ValueError): planning.read_planning_pages({'source_rotation': rotation})
    fetch.assert_not_called()


def test_server_attempt_selector_is_not_part_of_paid_editorial_request(generate):
    _, _, call = generate
    first = planning.generate({**CONTEXT, 'source_rotation': 0})
    request = deepcopy((call.call_args.args, call.call_args.kwargs))
    assert planning.generate({**CONTEXT, 'source_rotation': 8}) == first
    assert (call.call_args.args, call.call_args.kwargs) == request and 'source_rotation' not in request[0][0]


def test_discovered_articles_are_actually_read_and_keep_the_two_source_gate(monkeypatch):
    links = ['https://ikeamuseum.com/en/explore/the-story-of-ikea/price-wars/',
             'https://ikeamuseum.com/en/explore/the-story-of-ikea/more-than-low-price/']
    discovery = Mock(return_value=[{'url': url, 'category': 'discovered_primary'} for url in links])
    monkeypatch.setattr(planning, 'discover_candidates', discovery)
    monkeypatch.setattr(sources, 'feed_candidates', lambda: [])
    fetch = Mock(side_effect=lambda url: page(url))
    monkeypatch.setattr(sources, 'fetch_page', fetch)
    rows = planning.read_planning_pages({'source_rotation': len(planning.SOURCE_PAIRS) + 3,
        'existing_topics': ['current question'], 'previous_topics': ['older question']})
    discovery.assert_called_once_with(['current question', 'older question'], 3)
    assert fetch.call_count == 4 and len(rows) == 4
    assert [row['url'] for row in rows if row['category'] == 'discovered_primary'] == links
    assert planning._contract(rows, 'en')[1] >= 20


def test_unreadable_discovered_link_is_not_provided_as_evidence(monkeypatch):
    link = 'https://ikeamuseum.com/en/explore/the-story-of-ikea/price-wars/'
    monkeypatch.setattr(planning, 'discover_candidates', lambda *a: [
        {'url': link, 'category': 'discovered_primary'},
        {'url': link + 'missing/', 'category': 'discovered_primary'}])
    monkeypatch.setattr(sources, 'feed_candidates', lambda: [])
    def fetch(url):
        if url.endswith('missing/'): raise SpendBlocked('unavailable')
        return page(url)
    monkeypatch.setattr(sources, 'fetch_page', fetch)
    rows = planning.read_planning_pages({'source_rotation': len(planning.SOURCE_PAIRS)})
    assert len(rows) == 3 and all(not row['url'].endswith('missing/') for row in rows)
