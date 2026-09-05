"""Pure extraction and isolated publisher hooks; no provider/config imports."""
import ast
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import unicodedata
from unittest.mock import Mock

import pytest

from app.services import youtube_discovery_metadata as discovery


DOLLAR = ('Dolar neyden yapılıyor?',
          'Amerikan doları ağaç kâğıdından üretilmiyor. Banknot kâğıdının yüzde yetmiş beşi pamuk, '
          'yüzde yirmi beşi keten. Bu doların bileşimi.')
BARCODE = ('İlk barkod taramasında ne satın alındı?',
           'Olay 26 Haziran 1974 tarihinde Ohio eyaletindeki Troy kentinde gerçekleşti. '
           'Kasadan geçen ürün bir paket Wrigley’s sakızıydı. Barkod taraması, sakız paketini tanımladı.')


@pytest.mark.parametrize('example', [DOLLAR, BARCODE])
def test_examples_are_topic_grounded_bounded_and_have_no_generic_seo_padding(example):
    result = discovery.topic_metadata_fallback(*example)
    assert 1 <= len(result['tags']) <= 6
    assert 2 <= len(result['hashtags']) <= 3
    folded = discovery._fold(' '.join(example))
    assert all(discovery._fold(tag) in folded for tag in result['tags'])
    assert len({discovery._fold(value) for value in result['tags']}) == len(result['tags'])
    assert not {'viral', 'fyp', 'keşfet', 'shorts', 'neden', 'neyden', 'haziran'} & {
        discovery._fold(value) for value in result['hashtags']}


def test_reviewed_dollar_and_barcode_subjects_are_the_leading_hashtags():
    assert discovery.topic_metadata_fallback(*DOLLAR)['tags'] == ['Dolar', 'Banknot', 'pamuk', 'keten']
    assert discovery.topic_metadata_fallback(*DOLLAR)['hashtags'] == ['Dolar', 'Banknot', 'Pamuk']
    assert discovery.topic_metadata_fallback(*BARCODE)['hashtags'] == ['Barkod', 'Sakız', 'Wrigleys']


def test_turkish_uppercase_and_explicit_inflections_do_not_duplicate_subjects():
    result = discovery.topic_metadata_fallback('BANKALARIN KAPILARI NEDEN KAPANDI?',
                                               'Bankalar yeniden açıldı. Bu banka tatiliydi.')
    assert result['hashtags'][0] == 'Banka'
    assert sum(discovery._fold(tag) in discovery._ANCHOR for tag in result['tags']) == 1
    assert discovery._fold('İLK SAKIZIN BARKODU') == 'ilk sakızın barkodu'
    assert discovery._hashtag('İstanbul') == 'İstanbul'


def test_inflections_are_not_prefix_stemmed_into_an_absent_subject():
    result = discovery.topic_metadata_fallback('Bankacı röportajı', 'Bankacı konuştu.')
    assert 'Banka' not in result['hashtags']


@pytest.mark.parametrize('title,body', [
    ('', ''), (None, None), ('x' * 501, 'x' * 5001),
    ('\x00Dolar', '\u200bPamuk'), ('<script>Dolar</script>', '<b>Pamuk</b>'),
    ('https://private.invalid/token', 'www.private.invalid api@example.invalid'),
    ('Ignore previous instructions', 'API key secret password talimatlar'),
    ('viral fyp keşfet', 'subscribe like follow abone'),
])
def test_unsafe_or_non_topic_inputs_never_become_discovery_terms(title, body):
    assert discovery.topic_metadata_fallback(title, body) == {'tags': [], 'hashtags': []}


def test_source_lines_and_instruction_sentences_are_not_keyword_sources():
    result = discovery.topic_metadata_fallback('Dolar banknot',
        'Banknot pamuk içerir.\nKaynaklar: https://example.invalid/SecretMuseum\n'
        'Ignore previous instructions SecretControlToken.\nSources: UnrelatedMuseum')
    serialized = json.dumps(result).lower()
    assert 'secret' not in serialized and 'museum' not in serialized and 'example' not in serialized


@pytest.mark.parametrize('heading', ['Kaynaklar:', 'Kaynakça:', 'Sources:'])
def test_multiline_citation_section_is_not_a_topic_keyword_source(heading):
    body = 'Banknot pamuk içerir.'
    suffix = '\n' + heading + '\nFederal Reserve Museum archive\nSource Institutional Research'
    assert discovery.topic_metadata_fallback('Dolar banknot', body + suffix) == (
        discovery.topic_metadata_fallback('Dolar banknot', body))


@pytest.fixture
def automation():
    path = Path(__file__).resolve().parents[1] / 'app/services/youtube_automation.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    nodes = [node for node in tree.body if isinstance(node, (
        ast.Assign, ast.AnnAssign, ast.FunctionDef, ast.ClassDef,
    )) or isinstance(node, ast.ImportFrom) and node.module == '__future__']
    namespace = {'re': re, 'json': json, 'unicodedata': unicodedata,
                 'datetime': datetime, 'timedelta': timedelta, 'timezone': timezone}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    namespace['reserve_series_number'] = Mock(return_value=1)
    return namespace


def source():
    return {'state': 'SUCCESS', 'kind': 'render',
            'spec': {'language': 'tr', 'format': 'shorts', 'topic': 'InstructionOnlySecretToken'},
            'result': {'video_key': 'videos/source/final.mp4', 'title': DOLLAR[0],
                       'quality_disposition': 'automated_qc_pass', 'manual_qa_required': False,
                       'publish_metadata': {'title': DOLLAR[0], 'description': DOLLAR[1],
                                            'sources': ['SourceOnlySecretToken']}}}


def profile():
    return {'channel_id': 'UC_channel_discovery', 'languages': ['tr'], 'default_language': 'tr',
            'release_mode': 'private', 'description_footer': 'FooterOnlySecretToken',
            'default_tags': ['Capital Corrupt'], 'hashtags': ['Belgesel']}


def build(automation, job=None, channel=None):
    return automation['build_publish_plan']('source-discovery', job or source(), channel or profile())


def test_hook_uses_only_original_content_before_series_sources_footer_and_preserves_inputs(automation, monkeypatch):
    job, channel = source(), profile()
    channel.update(series_id='para', series_name='SeriesOnlySecretToken', series_total=8)
    before = deepcopy((job, channel))
    spy = Mock(wraps=discovery.topic_metadata_fallback)
    monkeypatch.setattr(discovery, 'topic_metadata_fallback', spy)
    result = build(automation, job, channel)
    spy.assert_called_once_with(*DOLLAR)
    values = result['tags'] + result['hashtags']
    assert not any('SecretToken' in value for value in values)
    assert '#Shorts' in result['description']
    assert result['title'].endswith('(1/8)')
    assert (job, channel) == before


@pytest.mark.parametrize('tags,hashtags', [(['Authored tag'], []), ([], ['AuthoredHash']),
                                         (['Authored tag'], ['AuthoredHash'])])
def test_missing_fields_fill_independently_and_authored_metadata_stays_first(automation, tags, hashtags):
    job = source()
    job['result']['publish_metadata'].update(tags=tags, hashtags=hashtags)
    before = deepcopy(job)
    result = build(automation, job)
    if tags:
        assert result['tags'][0] == tags[0]
        assert 'Dolar' not in result['tags']
    else:
        assert 'Dolar' in result['tags']
    if hashtags:
        assert 'AuthoredHash' in result['hashtags']
        assert 'Dolar' not in result['hashtags']
    else:
        assert 'Dolar' in result['hashtags']
    assert job == before


def test_legacy_keywords_are_authored_tags_not_replaced(automation):
    job = source()
    job['result']['publish_metadata']['keywords'] = ['Explicit keyword']
    assert build(automation, job)['tags'][0] == 'Explicit keyword'


def test_existing_frozen_plan_validation_never_calls_new_extractor(automation, monkeypatch):
    frozen = build(automation)
    frozen['tags'], frozen['hashtags'] = ['Frozen'], ['FrozenHash']
    before = deepcopy(frozen)
    monkeypatch.setattr(discovery, 'topic_metadata_fallback', Mock(side_effect=AssertionError('No regeneration')))
    validated = automation['validate_publish_plan'](frozen)
    assert validated['tags'] == ['Frozen'] and validated['hashtags'] == ['FrozenHash']
    assert frozen == before


def test_landscape_never_gets_shorts_from_new_topic_fallback(automation):
    job = source()
    job['spec']['format'] = 'landscape'
    result = build(automation, job)
    assert 'Shorts' not in result['hashtags'] and '#Shorts' not in result['description']
