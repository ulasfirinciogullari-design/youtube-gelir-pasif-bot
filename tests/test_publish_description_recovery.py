from copy import deepcopy
from io import BytesIO
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import youtube_automation as automation
from app.services import youtube_publish_metadata as recovered_metadata


TASK_ID = '4ec31182-4637-4030-b2f2-1056284ca13f'
OTHER_ID = '11111111-1111-4111-8111-111111111111'


@pytest.fixture
def setup(monkeypatch):
    source = {
        'task_id': TASK_ID, 'state': 'SUCCESS', 'kind': 'render', 'spec': {'language': 'tr'},
        'result': {
            'task_id': TASK_ID,
            'title': 'Doların kâğıdı',
            'video_key': f'videos/{TASK_ID}/final.mp4',
            'metadata_key': f'videos/{TASK_ID}/metadata.json',
            'scenes': 2,
            'quality_disposition': 'automated_qc_pass',
            'manual_qa_required': False,
            'publish_metadata': {
                'title': 'Doların kâğıdı', 'description': None,
                'sources': ['https://www.bep.gov/currency'],
                'tags': ['para'], 'hashtags': ['Shorts'],
            },
        },
    }
    metadata = {
        'task_id': TASK_ID, 'title': source['result']['title'],
        'quality_disposition': 'automated_qc_pass', 'manual_qa_required': False,
        'scenes': [
            {'index': 0, 'narration': 'Bu banknot pamuk içerir.'},
            {'index': 1, 'narration': 'Keten de karışımın bir parçasıdır.'},
        ],
    }
    profile = {
        'channel_id': 'UC_channel_alpha', 'profile_revision': 'revision-one',
        'languages': ['tr', 'en'], 'default_language': 'tr',
        'release_mode': 'private', 'category_id': '28',
        'description_footer': 'Capital Corrupt',
    }
    client = Mock()
    bodies = []

    def response(payload=None, **overrides):
        raw = json.dumps(metadata, ensure_ascii=False).encode() if payload is None else payload
        body = BytesIO(raw)
        bodies.append(body)
        result = {
            'Body': body, 'ContentLength': len(raw), 'ContentType': 'application/json',
            'ResponseMetadata': {'HTTPStatusCode': 200},
        }
        result.update(overrides)
        return result

    client.get_object.side_effect = lambda **_kwargs: response()
    monkeypatch.setattr(recovered_metadata.storage, '_client', lambda: client)
    monkeypatch.setattr(recovered_metadata.storage, 'settings', SimpleNamespace(bucket='private-test'))
    monkeypatch.setattr(automation, '_redis', Mock(side_effect=AssertionError('Unexpected Redis write')))
    return SimpleNamespace(source=source, metadata=metadata, profile=profile, client=client,
                           bodies=bodies, response=response)


def plan(case):
    return automation.build_publish_plan(TASK_ID, case.source, case.profile)


def test_missing_description_uses_existing_approved_narration_without_mutation(setup):
    before = deepcopy((setup.source, setup.metadata, setup.profile))
    result = plan(setup)
    assert result['description'] == (
        'Bu banknot pamuk içerir. Keten de karışımın bir parçasıdır.'
        '\n\nKaynaklar:\nhttps://www.bep.gov/currency\n\nCapital Corrupt\n\n#Shorts'
    )
    assert result['release_mode'] == 'private'
    assert result['publish_at'] is None
    assert result['tags'] == ['para']
    assert result['quality_snapshot'] == {'quality_disposition': 'automated_qc_pass', 'manual_qa_required': False}
    assert (setup.source, setup.metadata, setup.profile) == before
    setup.client.get_object.assert_called_once_with(Bucket='private-test', Key=f'videos/{TASK_ID}/metadata.json')
    assert all(body.closed for body in setup.bodies)


def test_english_narration_is_copied_without_turkish_generated_copy(setup):
    setup.source['spec']['language'] = 'en'
    setup.metadata['scenes'] = [
        {'index': 0, 'narration': 'This banknote contains cotton.'},
        {'index': 1, 'narration': 'Linen is also part of the mixture.'},
    ]
    result = plan(setup)
    assert result['description'].startswith('This banknote contains cotton. Linen is also part of the mixture.')
    assert result['default_language'] == 'en'


def test_structured_sources_use_only_urls_and_skip_missing_or_non_string_urls(setup):
    setup.source['result']['publish_metadata']['sources'] = [
        {'url': 'https://www.bep.gov/currency', 'evidence': 'Verified statement, not publication copy.'},
        {'url': 'https://www.uscurrency.gov/', 'evidence': 'Another approved evidence sentence.'},
        {'evidence': 'No URL'}, {'url': None}, {'url': 5}, {'url': True}, {'url': {}},
        {'url': ''}, 'Legacy citation text',
    ]
    before = deepcopy(setup.source)
    result = plan(setup)
    assert '\n\nKaynaklar:\nhttps://www.bep.gov/currency\nhttps://www.uscurrency.gov/\nLegacy citation text' in result['description']
    assert 'evidence' not in result['description']
    assert "{'url'" not in result['description']
    assert setup.source == before


@pytest.mark.parametrize('description', ['Authored description.', '  Başlangıç\nİkinci satır.  '])
def test_authored_description_is_preserved_and_never_reads_storage(setup, description):
    setup.source['result']['publish_metadata']['description'] = description
    setup.source['result'].pop('metadata_key')
    result = plan(setup)
    assert result['description'].startswith(description.strip() + '\n\nKaynaklar:')
    setup.client.get_object.assert_not_called()


@pytest.mark.parametrize(('target', 'field', 'value'), [
    ('source', 'state', 'FAILURE'), ('source', 'state', 'PENDING'),
    ('source', 'kind', 'publish'), ('source', 'task_id', OTHER_ID),
    ('result', 'quality_disposition', 'manual_qa_preview'),
    ('result', 'quality_disposition', None),
    ('result', 'manual_qa_required', True), ('result', 'manual_qa_required', None),
    ('result', 'manual_qa_required', 0),
    ('result', 'task_id', OTHER_ID),
    ('result', 'video_key', f'videos/{OTHER_ID}/final.mp4'),
    ('result', 'metadata_key', f'videos/{OTHER_ID}/metadata.json'),
    ('result', 'metadata_key', 'https://private.example/metadata?token=SECRET'),
    ('result', 'scenes', True), ('result', 'scenes', 0), ('result', 'scenes', 257),
    ('result', 'scenes', []), ('result', 'title', None), ('result', 'title', ''),
    ('result', 'title', 'x' * 501),
])
def test_invalid_source_is_rejected_before_storage(setup, target, field, value):
    (setup.source if target == 'source' else setup.source['result'])[field] = value
    with pytest.raises(automation.MetadataValidationError):
        plan(setup)
    setup.client.get_object.assert_not_called()


@pytest.mark.parametrize(('field', 'value'), [
    ('task_id', OTHER_ID), ('title', 'Another final'),
    ('quality_disposition', 'manual_qa_preview'), ('quality_disposition', None),
    ('manual_qa_required', True), ('manual_qa_required', 0),
    ('scenes', None), ('scenes', {}), ('scenes', []),
    ('scenes', [{'index': 0, 'narration': 'Only one scene.'}]),
])
def test_metadata_must_match_approved_final(setup, field, value):
    setup.metadata[field] = value
    with pytest.raises(automation.MetadataValidationError):
        plan(setup)
    assert all(body.closed for body in setup.bodies)


@pytest.mark.parametrize('scene', [
    None, 'not a scene', {},
    {'index': 1, 'narration': 'Changed order.'},
    {'index': False, 'narration': 'Invalid index.'},
    {'index': '0', 'narration': 'Invalid index.'},
    {'index': 0}, {'index': 0, 'narration': None}, {'index': 0, 'narration': 12},
    {'index': 0, 'narration': ''}, {'index': 0, 'narration': ' \n\t '},
    {'index': 0, 'narration': 'x' * 4001}, {'index': 0, 'narration': 'No\x00control'},
])
def test_rejects_missing_malformed_or_reordered_narrations(setup, scene):
    setup.metadata['scenes'][0] = scene
    with pytest.raises(automation.MetadataValidationError):
        plan(setup)
    assert all(body.closed for body in setup.bodies)


def test_total_narration_is_bounded_not_silently_truncated(setup):
    for scene in setup.metadata['scenes']:
        scene['narration'] = 'x' * 2000
    with pytest.raises(automation.MetadataValidationError):
        plan(setup)
    assert all(body.closed for body in setup.bodies)


@pytest.mark.parametrize('overrides', [
    {'ContentLength': True}, {'ContentLength': 0}, {'ContentLength': 1024 * 1024 + 1},
    {'ContentLength': 2}, {'ContentLength': 1000000},
    {'ContentType': 'text/html'}, {'ContentEncoding': 'gzip'},
    {'ResponseMetadata': {'HTTPStatusCode': 206}},
])
def test_rejects_invalid_or_mis_sized_storage_response_and_closes(setup, overrides):
    setup.client.get_object.side_effect = lambda **_kwargs: setup.response(**overrides)
    with pytest.raises(automation.MetadataValidationError):
        plan(setup)
    assert all(body.closed for body in setup.bodies)


@pytest.mark.parametrize('payload', [
    b'not json SECRET', b'\xff', b'[]', b'null',
    b'{"task_id":"first","task_id":"second"}', b'{"bad":NaN}',
])
def test_invalid_json_fails_closed_without_exposing_body(setup, payload):
    setup.client.get_object.side_effect = lambda **_kwargs: setup.response(payload)
    with pytest.raises(automation.MetadataValidationError, match='Approved publish description is unavailable') as error:
        plan(setup)
    assert 'SECRET' not in str(error.value)
    assert all(body.closed for body in setup.bodies)


def test_storage_failure_is_safe_and_does_not_consume_series_number(setup, monkeypatch):
    setup.profile.update(series_id='money', series_name='Money', series_total=8)
    reserve = Mock(side_effect=AssertionError('Should not reserve a series number'))
    monkeypatch.setattr(automation, 'reserve_series_number', reserve)
    setup.client.get_object.side_effect = RuntimeError('https://private.example?secret=SECRET')
    with pytest.raises(automation.MetadataValidationError) as error:
        plan(setup)
    assert 'SECRET' not in str(error.value)
    reserve.assert_not_called()


@pytest.mark.parametrize('source', [None, {}, {'result': {}}, {'state': 'SUCCESS', 'kind': 'render'}])
def test_empty_or_incomplete_source_cannot_supply_approved_copy(setup, source):
    with pytest.raises(ValueError, match='Approved final narration is unavailable'):
        recovered_metadata.approved_narration_description(TASK_ID, source)
    setup.client.get_object.assert_not_called()


def test_empty_title_is_not_repaired_from_narration(setup):
    setup.source['result']['title'] = ''
    setup.source['result']['publish_metadata']['title'] = ''
    with pytest.raises(automation.MetadataValidationError):
        plan(setup)
    setup.client.get_object.assert_not_called()


def test_read_error_closes_body_and_never_exposes_storage_details(setup):
    body = Mock()
    body.read.side_effect = RuntimeError('https://private.example?token=SECRET')
    setup.client.get_object.side_effect = lambda **_kwargs: setup.response(Body=body)
    with pytest.raises(automation.MetadataValidationError) as error:
        plan(setup)
    assert 'SECRET' not in str(error.value)
    body.close.assert_called_once()


def test_reads_are_bounded_even_when_unrelated_metadata_is_large(setup):
    setup.metadata['unrelated_diagnostics'] = 'x' * (70 * 1024)
    reads = []

    class TrackedBody(BytesIO):
        def read(self, count=-1):
            reads.append(count)
            return super().read(count)

    raw = json.dumps(setup.metadata, ensure_ascii=False).encode()
    body = TrackedBody(raw)
    setup.client.get_object.side_effect = lambda **_kwargs: setup.response(raw, Body=body)
    assert plan(setup)['description'].startswith('Bu banknot')
    assert max(reads) == 64 * 1024
    assert reads[-1] == 1
    assert body.closed


def test_valid_fallback_keeps_existing_series_and_release_policy(setup, monkeypatch):
    setup.profile.update(series_id='money', series_name='Money', series_total=8, release_mode='public')
    reserve = Mock(return_value=1)
    monkeypatch.setattr(automation, 'reserve_series_number', reserve)
    result = plan(setup)
    assert result['title'].endswith('(1/8)')
    assert result['description'].startswith('Money · 1/8\n\nBu banknot')
    assert result['release_mode'] == 'public'
    reserve.assert_called_once_with('UC_channel_alpha', 'money', TASK_ID, total=8)
