"""Preparation only; real Redis WATCH with mocked models, no network calls."""
import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import NAMESPACE_URL, uuid5

import pytest

from test_channel_production import production


ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / 'app/services/production_next_series.py'
NOW = datetime(2026, 9, 6, 12, tzinfo=timezone.utc).timestamp()
URL = 'https://www.ikea.com/global/en/our-business/how-we-work/'


def _seed_preparation_binding(namespace, client, profile, channel, now, *, replace=False):
    """Arrange server-owned task proof before calling the preparation service."""
    day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
    dispatch_key = namespace['PREPARATION_DISPATCH_PREFIX'] + channel['id'] + ':' + day
    raw = client.get(dispatch_key)
    fields = ('version', 'channel_id', 'profile_revision', 'day', 'task_id', 'token')
    if raw is not None and not replace:
        record = json.loads(raw)
        return {key: record[key] for key in fields}
    task_id = str(uuid5(NAMESPACE_URL, f'test-series-preparation:{channel["id"]}:{day}'))
    binding = {'version': 1, 'channel_id': channel['id'],
               'profile_revision': profile['profile_revision'], 'day': day,
               'task_id': task_id, 'token': uuid5(NAMESPACE_URL, task_id + ':claim').hex}
    record = {**binding, 'status': 'reserved', 'connection_id': channel['connection_id'],
              'profile_sha256': namespace['_digest'](profile),
              'channel_sha256': namespace['_digest'](namespace['_planning_channel_identity'](channel)),
              'credential_sha256': namespace['_digest'](client.get(namespace['OAUTH_CREDENTIAL_PREFIX'] + channel['id'])),
              'authorization_epoch_sha256': namespace['_digest'](client.get(namespace['AUTH_EPOCH_KEY'])),
              'created_at': now}
    encoded = namespace['_json'](record)
    if raw != encoded:
        client.set(dispatch_key, encoded)
    client.set(namespace['PREPARATION_EXECUTION_PREFIX'] + task_id, binding['token'])
    return binding


def _answer(language='en'):
    return {'can_prepare': True, 'language': language,
            'series_title': 'Small Decisions, Big Businesses' if language == 'en' else 'Küçük Kararlar, Büyük İşler',
            'briefs': [{'brief': ('Why did removable table legs change furniture shipping? ' if language == 'en'
                                  else 'Sökülebilen masa ayakları mobilya taşımacılığını nasıl değiştirdi? ') + URL,
                        'sources': [{'url': URL, 'evidence': 'The public source describes flat-pack furniture and transportation.'}]}]}


@pytest.fixture
def case(production):
    scheduler, client = production
    tree = ast.parse(FILE.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom) and (node.module or '').startswith('app.'))]
    evidence = {}
    evidence_file = ROOT / 'app/services/source_evidence.py'
    exec(compile(ast.parse(evidence_file.read_text(encoding='utf-8')), str(evidence_file), 'exec'), evidence)
    namespace = {'settings': SimpleNamespace(redis_url='redis://not-used'),
                 'normalize_evidence_sources': evidence['normalize_evidence_sources'],
                 'AUTH_EPOCH_KEY': 'youtube_studio:oauth:authorization_epoch:v2',
                 **{key: getattr(scheduler, key) for key in (
                     'CHANNEL_STATE_PREFIX', 'PROFILE_PREFIX', 'OAUTH_CHANNEL_PREFIX',
                     'OAUTH_CREDENTIAL_PREFIX', 'OAUTH_CHANNEL_INDEX', '_prefix_digest')}}
    exec(compile(tree, str(FILE), 'exec'), namespace)
    namespace['redis'] = SimpleNamespace(Redis=SimpleNamespace(from_url=Mock(return_value=client)))
    configuration = Mock(return_value=('openai', 'configured-model', 'not-a-real-key'))
    generate = Mock(return_value=_answer())
    namespace['_configuration'], namespace['_generate'] = configuration, generate
    profile = {'channel_id': 'UC_Margin_channel', 'profile_revision': 'revision-one', 'default_language': 'en',
               'production_enabled': True, 'auto_publish': True, 'release_mode': 'public',
               'route_label': 'margin-verdict',
               'channel_identity': 'Evidence-led business history', 'series_name': 'Business Decisions',
               'production_topics': ['How grocery memberships work', 'Why a toy business lost focus',
                                     'The first supermarket barcode purchase', 'Why bank holidays close banks'],
               'description_footer': 'PRIVATE FOOTER NOT MODEL INPUT', 'default_tags': ['NOT_MODEL_INPUT']}
    channel = {'id': profile['channel_id'], 'connection_id': 'connection-generation-one', 'title': 'Margin Verdict',
               'description': 'Big companies. Bigger decisions. Follow the evidence.',
               'internal_field': 'NOT_MODEL_INPUT'}
    keys = {'profile': scheduler.PROFILE_PREFIX + channel['id'],
            'channel': scheduler.OAUTH_CHANNEL_PREFIX + channel['id'],
            'state': scheduler.CHANNEL_STATE_PREFIX + channel['id'],
            'pending': namespace['PENDING_PREFIX'] + channel['id'],
            'daily': namespace['DAILY_PREFIX'] + channel['id'] + ':2026-09-06'}
    result = SimpleNamespace(ns=namespace, client=client, profile=profile, channel=channel, keys=keys,
                             generate=generate, configuration=configuration)
    client.set(namespace['OAUTH_CREDENTIAL_PREFIX'] + channel['id'], 'opaque-test-credential')
    client.sadd(namespace['OAUTH_CHANNEL_INDEX'], channel['id'])
    client.set(namespace['AUTH_EPOCH_KEY'], '4')
    _save(result)
    client.set('youtube_studio:job:immutable-history', 'KEEP SOURCE JOB')
    client.set('youtube_studio:upload:immutable-history', 'KEEP PUBLICATION LEDGER')
    client.set('youtube_studio:series:counter', '4')
    client.set('youtube_studio:production:active', 'KEEP ACTIVE JOBS')
    return result


def _save(case, cursor=2):
    case.client.set(case.keys['profile'], json.dumps(case.profile))
    case.client.set(case.keys['channel'], json.dumps(case.channel))
    case.client.hset(case.keys['state'], mapping={'cursor': str(cursor), 'next_due': '9999999999',
        'profile_revision': case.profile['profile_revision'], 'connection_id': case.channel['connection_id'],
        'consumed_prefix': case.ns['_prefix_digest'](case.profile['production_topics'][:cursor])})
    case.bindings = {
        datetime.fromtimestamp(now, timezone.utc).date().isoformat(): _seed_preparation_binding(
            case.ns, case.client, case.profile, case.channel, now, replace=True)
        for now in (NOW, NOW + 86400)
    }


def _run(case, now=NOW):
    day = (datetime.fromtimestamp(now, timezone.utc).date().isoformat()
           if type(now) in (int, float) and math.isfinite(now) and now >= 0 else '2026-09-06')
    return case.ns['prepare_next_series'](case.profile, case.channel, now=now,
                                          execution_binding=case.bindings[day])


def _record(case, name='pending'):
    return json.loads(case.client.get(case.keys[name]))


def _snapshot(case):
    return {key: case.client.dump(key) for key in case.client.keys('*')}


def test_ready_is_separate_unapproved_pending_batch_and_reservation_precedes_request(case):
    before, profile, channel = _snapshot(case), deepcopy(case.profile), deepcopy(case.channel)

    def generate(context, configuration):
        assert _record(case)['status'] == _record(case, 'daily')['status'] == 'reserved'
        assert case.client.ttl(case.keys['pending']) == case.client.ttl(case.keys['daily']) == -1
        assert configuration == ('openai', 'configured-model', 'not-a-real-key')
        assert set(context) == {'language', 'channel_identity', 'channel_title', 'channel_bio',
                                'current_series_title', 'existing_topics'}
        assert all(term not in json.dumps(context) for term in ('NOT_MODEL_INPUT', 'PRIVATE FOOTER', 'connection-generation'))
        return _answer()

    case.generate.side_effect = generate
    result = _run(case)
    assert result['status'] == 'ready' and len(result['briefs']) == 1
    assert result['qa_approved'] is result['publish_eligible'] is result['media_budget_approved'] is False
    assert result['requires_full_research_and_critic'] is True
    assert result['evidence_validation'] == 'source_shape_only'
    assert case.generate.call_count == 1 and len(result['attempt_id']) == 32
    assert {key: case.client.dump(key) for key in before} == before
    assert set(case.client.keys('*')) - set(before) == {case.keys['pending'], case.keys['daily']}
    assert case.profile == profile and case.channel == channel
    assert 'not-a-real-key' not in case.client.get(case.keys['pending'])
    binding = case.bindings['2026-09-06']
    assert all(value not in case.client.get(case.keys['pending']) + json.dumps(result)
               for value in (binding['task_id'], binding['token'], 'opaque-test-credential'))
    assert 'provider' not in result and 'model' not in result and 'format' not in result


@pytest.mark.parametrize('cursor', [2, 3, 4])
def test_prepares_only_when_at_most_two_topics_remain(case, cursor):
    _save(case, cursor)
    assert _run(case)['status'] == 'ready'


@pytest.mark.parametrize('cursor', [0, 1])
def test_more_than_two_remaining_never_reserves_or_calls(case, cursor):
    _save(case, cursor)
    before = _snapshot(case)
    assert _run(case)['status'] == 'not_due'
    assert _snapshot(case) == before
    case.generate.assert_not_called()
    case.configuration.assert_not_called()


def test_ready_is_reused_across_days_without_new_model_or_queue_side_effects(case):
    first = _run(case)
    assert _run(case) == _run(case, NOW + 86400) == first
    assert case.generate.call_count == 1 and len(case.client.keys(case.ns['DAILY_PREFIX'] + '*')) == 1


def test_parallel_preparation_reserves_at_most_one_model_request(case):
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: _run(case), range(12)))
    assert case.generate.call_count == 1
    assert any(result['status'] == 'ready' for result in results)
    assert _record(case)['status'] == 'ready'


@pytest.mark.parametrize('mutation', ['absent', 'extra', 'version_bool', 'channel', 'revision',
                                     'day', 'task', 'token', 'missing_token'])
def test_only_exact_server_execution_binding_may_prepare(case, mutation):
    binding = deepcopy(case.bindings['2026-09-06'])
    if mutation == 'absent': binding = None
    elif mutation == 'extra': binding['approved'] = True
    elif mutation == 'version_bool': binding['version'] = True
    elif mutation == 'channel': binding['channel_id'] = 'UC_another_channel'
    elif mutation == 'revision': binding['profile_revision'] = 'another-revision'
    elif mutation == 'day': binding['day'] = '2026-09-07'
    elif mutation == 'task': binding['task_id'] = str(uuid5(NAMESPACE_URL, 'wrong-task'))
    elif mutation == 'token': binding['token'] = '0' * 32
    elif mutation == 'missing_token': binding.pop('token')
    before = _snapshot(case)
    result = case.ns['prepare_next_series'](case.profile, case.channel, now=NOW, execution_binding=binding)
    assert result['status'] == 'unavailable' and _snapshot(case) == before
    case.generate.assert_not_called()
    case.configuration.assert_not_called()


@pytest.mark.parametrize('mutation', ['dispatch_missing', 'execution_missing', 'execution_wrong',
    'credential_missing', 'credential_changed', 'membership_missing', 'epoch_changed',
    'dispatch_status', 'dispatch_profile_hash', 'dispatch_channel_hash', 'paused', 'private', 'scheduled'])
def test_current_authority_and_public_unpaused_state_are_required_before_reservation(case, mutation):
    binding = case.bindings['2026-09-06']
    dispatch, execution, credential, membership, epoch = case.ns['_execution_keys'](binding)
    if mutation == 'dispatch_missing': case.client.delete(dispatch)
    elif mutation == 'execution_missing': case.client.delete(execution)
    elif mutation == 'execution_wrong': case.client.set(execution, '0' * 32)
    elif mutation == 'credential_missing': case.client.delete(credential)
    elif mutation == 'credential_changed': case.client.set(credential, 'rotated-ciphertext')
    elif mutation == 'membership_missing': case.client.srem(membership, case.channel['id'])
    elif mutation == 'epoch_changed': case.client.set(epoch, '5')
    elif mutation.startswith('dispatch_'):
        record = json.loads(case.client.get(dispatch))
        field = {'dispatch_status': 'status', 'dispatch_profile_hash': 'profile_sha256',
                 'dispatch_channel_hash': 'channel_sha256'}[mutation]
        record[field] = 'complete' if field == 'status' else '0' * 64
        case.client.set(dispatch, json.dumps(record))
    elif mutation == 'paused': case.client.hset(case.keys['state'], 'paused_reason', 'previous_render_failed')
    else:
        case.profile['release_mode'] = mutation
        _save(case)
    before = _snapshot(case)
    assert _run(case)['status'] == 'unavailable' and _snapshot(case) == before
    case.generate.assert_not_called()
    case.configuration.assert_not_called()


@pytest.mark.parametrize('status', ['reserved', 'dispatched', 'uncertain'])
def test_exact_claimed_task_remains_valid_after_dispatch_reply_variants(case, status):
    dispatch = case.ns['_execution_keys'](case.bindings['2026-09-06'])[0]
    record = json.loads(case.client.get(dispatch))
    record['status'] = status
    case.client.set(dispatch, json.dumps(record))
    assert _run(case)['status'] == 'ready'
    assert case.generate.call_count == 1


@pytest.mark.parametrize('after_reservation', [False, True])
def test_revocation_racing_reservation_is_rechecked_before_any_model_call(case, monkeypatch, after_reservation):
    original = case.client.pipeline
    credential = case.ns['_execution_keys'](case.bindings['2026-09-06'])[2]
    calls = []
    def pipeline(*args, **kwargs):
        pipe = original(*args, **kwargs)
        execute = pipe.execute
        def racing(*args, **kwargs):
            first = not calls
            calls.append(True)
            if first and not after_reservation:
                case.client.set(credential, 'revoked-before-reservation-commit')
            result = execute(*args, **kwargs)
            if first and after_reservation:
                case.client.set(credential, 'revoked-after-reservation-commit')
            return result
        pipe.execute = racing
        return pipe
    monkeypatch.setattr(case.client, 'pipeline', pipeline)
    result = _run(case)
    case.generate.assert_not_called()
    if after_reservation:
        assert result['status'] == 'failed'
        assert _record(case)['status'] == _record(case, 'daily')['status'] == 'failed'
        assert case.client.ttl(case.keys['daily']) == -1
    else:
        assert result['status'] == 'unavailable'
        assert not case.client.exists(case.keys['pending'])
        assert not case.client.exists(case.keys['daily'])


@pytest.mark.parametrize('error', [TimeoutError('SECRET transport'), RuntimeError('SECRET error'),
                                 ValueError('SECRET ambiguous SDK response'), TypeError('SECRET SDK failure')])
def test_ambiguous_call_errors_fence_all_future_days_without_leaking_exception(case, error):
    case.generate.side_effect = error
    result = _run(case)
    assert result['status'] == 'uncertain'
    assert 'SECRET' not in json.dumps(result) + case.client.get(case.keys['pending'])
    assert _run(case, NOW + 86400) == result
    assert case.generate.call_count == 1


def test_lost_reservation_reply_never_reaches_model_and_leaves_durable_fence(case, monkeypatch):
    original = case.client.pipeline

    def pipeline(*args, **kwargs):
        pipe = original(*args, **kwargs)
        execute = pipe.execute

        def lost_reply(*a, **k):
            execute(*a, **k)
            raise TimeoutError('SECRET lost Redis reply')

        pipe.execute = lost_reply
        return pipe

    monkeypatch.setattr(case.client, 'pipeline', pipeline)
    assert _run(case)['status'] == 'unavailable'
    monkeypatch.setattr(case.client, 'pipeline', original)
    assert _run(case, NOW + 86400)['status'] == 'reserved'
    case.generate.assert_not_called()


@pytest.mark.parametrize('answer', [None, {}, {'can_prepare': False, 'language': 'en', 'series_title': '', 'briefs': []}])
def test_definitive_bad_or_negative_output_permits_only_new_day_attempt(case, answer):
    case.generate.return_value = answer
    failed = _run(case)
    assert failed['status'] == 'failed'
    assert _run(case) == failed and case.generate.call_count == 1
    case.generate.return_value = _answer()
    ready = _run(case, NOW + 86400)
    assert ready['status'] == 'ready' and ready['attempt_id'] != failed['attempt_id']
    assert case.generate.call_count == 2
    assert _record(case, 'daily') == {**_record(case, 'daily'), 'status': 'failed'}


def test_existing_daily_reservation_survives_missing_pending_key(case):
    case.client.set(case.keys['daily'], 'reserved')
    assert _run(case)['status'] == 'daily_attempt_already_reserved'
    case.generate.assert_not_called()


@pytest.mark.parametrize('field,value', [('profile_revision', 'changed'), ('connection_id', 'changed'),
    ('cursor', '-1'), ('cursor', '02'), ('cursor', '5'), ('cursor', 'nan'), ('consumed_prefix', 'wrong')])
def test_frozen_queue_state_must_match_before_any_reservation(case, field, value):
    case.client.hset(case.keys['state'], field, value)
    before = _snapshot(case)
    assert _run(case)['status'] == 'unavailable' and _snapshot(case) == before
    case.generate.assert_not_called()


@pytest.mark.parametrize('record,field,value', [('profile', 'auto_publish', False),
    ('profile', 'production_enabled', False), ('profile', 'default_language', 'xx'),
    ('profile', 'production_topics', []), ('profile', 'channel_id', 123456789),
    ('channel', 'id', 'UC_other_channel'), ('channel', 'connection_id', None),
    ('channel', 'requires_reconnect', True), ('channel', 'description', 'api_key=SECRET'),
    ('profile', 'channel_identity', 'See https://127.0.0.1/private')])
def test_bad_or_private_context_never_reaches_model(case, record, field, value):
    getattr(case, record)[field] = value
    assert _run(case)['status'] == 'unavailable'
    case.generate.assert_not_called()
    assert not case.client.exists(case.keys['pending'])


@pytest.mark.parametrize('record,field', [('profile', 'series_name'), ('channel', 'title'),
                                        ('profile', 'channel_identity'), ('channel', 'description')])
@pytest.mark.parametrize('url', ['https://10.0.0.1/private', 'HTTP://0x7f.0.0.1/private'])
def test_every_exported_context_string_rejects_private_urls_before_reservation(case, record, field, url):
    getattr(case, record)[field] = 'Public editorial title ' + url
    _save(case)
    before = _snapshot(case)
    assert _run(case)['status'] == 'unavailable' and _snapshot(case) == before
    case.generate.assert_not_called()
    case.configuration.assert_not_called()


@pytest.mark.parametrize('record,field', [('profile', 'series_name'), ('channel', 'title')])
def test_title_context_still_accepts_canonical_public_source(case, record, field):
    getattr(case, record)[field] = 'Public editorial title ' + URL
    _save(case)
    assert _run(case)['status'] == 'ready'
    assert case.generate.call_count == 1


def test_profile_current_language_is_authoritative_even_with_english_bio(case):
    case.profile['default_language'] = 'tr'
    _save(case)
    case.generate.return_value = _answer('tr')
    assert _run(case)['language'] == 'tr'
    assert case.generate.call_args.args[0]['channel_bio'] == case.channel['description']


def test_profile_or_channel_changed_while_model_runs_invalidates_result_without_mutation(case):
    def generate(*_):
        changed = {**case.profile, 'profile_revision': 'revision-two'}
        case.client.set(case.keys['profile'], json.dumps(changed))
        return _answer()
    case.generate.side_effect = generate
    result = _run(case)
    assert result['status'] == 'failed' and result['error_code'] == 'editorial_context_changed'
    assert json.loads(case.client.get(case.keys['profile']))['profile_revision'] == 'revision-two'
    assert 'briefs' not in result


@pytest.mark.parametrize('mutation', ['credential', 'membership', 'epoch', 'execution', 'paused', 'channel_bio'])
def test_authority_change_during_paid_call_cannot_become_a_ready_batch(case, mutation):
    _, execution, credential, membership, epoch = case.ns['_execution_keys'](case.bindings['2026-09-06'])
    def generate(*_):
        if mutation == 'credential': case.client.set(credential, 'rotated-ciphertext')
        elif mutation == 'membership': case.client.srem(membership, case.channel['id'])
        elif mutation == 'epoch': case.client.set(epoch, '5')
        elif mutation == 'execution': case.client.delete(execution)
        elif mutation == 'paused': case.client.hset(case.keys['state'], 'paused_reason', 'previous_render_failed')
        else: case.client.set(case.keys['channel'], json.dumps({**case.channel, 'description': 'Changed editorial bio'}))
        return _answer()
    case.generate.side_effect = generate
    result = _run(case)
    assert result['status'] == 'failed' and result['error_code'] == 'editorial_context_changed'
    assert 'briefs' not in result and case.generate.call_count == 1
    assert _record(case)['status'] == _record(case, 'daily')['status'] == 'failed'
    assert case.client.ttl(case.keys['daily']) == -1


def test_channel_telemetry_refresh_during_paid_call_does_not_discard_same_editorial_draft(case):
    def generate(*_):
        case.client.set(case.keys['channel'], json.dumps({**case.channel, 'view_count': 1234,
                                                        'statistics_refreshed_at': '2026-09-06T12:00:01Z'}))
        return _answer()
    case.generate.side_effect = generate
    assert _run(case)['status'] == 'ready' and case.generate.call_count == 1
    assert _record(case)['channel_sha256'] == case.ns['_digest'](case.ns['_planning_channel_identity'](case.channel))


def test_channel_telemetry_between_worker_read_and_preparation_does_not_stale_binding(case):
    case.client.set(case.keys['channel'], json.dumps({**case.channel, 'verified_at': '2026-09-06T12:00:00Z',
                                                     'view_count': 800, 'thumbnail': 'https://example.com/new.jpg'}))
    assert _run(case)['status'] == 'ready' and case.generate.call_count == 1
    assert _record(case)['channel_sha256'] == case.ns['_digest'](case.ns['_planning_channel_identity'](case.channel))


@pytest.mark.parametrize('field,value', [('title', 'New editorial title'), ('description', 'Changed channel bio'),
                                      ('connection_id', 'new-connection-generation'), ('requires_reconnect', True)])
def test_editorial_or_connection_change_before_preparation_is_not_telemetry(case, field, value):
    case.client.set(case.keys['channel'], json.dumps({**case.channel, field: value}))
    before = _snapshot(case)
    assert _run(case)['status'] == 'unavailable' and _snapshot(case) == before
    case.generate.assert_not_called()


def test_cursor_progress_does_not_rewrite_queue_or_lose_same_context_draft(case):
    def generate(*_):
        _save(case, 3)
        return _answer()
    case.generate.side_effect = generate
    assert _run(case)['status'] == 'ready'
    assert case.client.hget(case.keys['state'], 'cursor') == '3'


def test_new_profile_cannot_create_a_second_pending_batch(case):
    assert _run(case)['status'] == 'ready'
    case.profile['profile_revision'] = 'new-revision'
    _save(case)
    assert _run(case, NOW + 86400)['status'] == 'pending_context_changed'
    assert case.generate.call_count == 1


@pytest.mark.parametrize('mutate', [
    lambda answer: answer.update(language='tr'),
    lambda answer: answer.update(can_prepare=1),
    lambda answer: answer.update(series_title='Business Decisions'),
    lambda answer: answer.update(series_title='x' * 101),
    lambda answer: answer.update(briefs=[]),
    lambda answer: answer.update(briefs=answer['briefs'] * 5),
    lambda answer: answer.update(unexpected='field'),
    lambda answer: answer['briefs'][0].update(brief='x' * 241),
    lambda answer: answer['briefs'][0].update(brief='Why a toy business lost focus ' + URL),
    lambda answer: answer['briefs'].append(deepcopy(answer['briefs'][0])),
    lambda answer: answer['briefs'][0].update(brief='The same scene but no source URL'),
    lambda answer: answer['briefs'][0].update(sources=[]),
    lambda answer: answer['briefs'][0]['sources'][0].update(evidence='x' * 601),
    lambda answer: answer['briefs'][0]['sources'][0].update(evidence='too short'),
    lambda answer: answer['briefs'][0]['sources'][0].update(evidence='Bearer SECRET_NOT_ALLOWED'),
    lambda answer: answer['briefs'][0]['sources'][0].update(api_key='SECRET'),
])
def test_unfit_model_output_is_never_marked_ready(case, mutate):
    answer = _answer()
    mutate(answer)
    case.generate.return_value = answer
    result = _run(case)
    assert result['status'] == 'failed' and result['error_code'] == 'invalid_model_output'
    assert result['qa_approved'] is False and 'briefs' not in result


@pytest.mark.parametrize('url', ['https://127.0.0.1/doc', 'https://10.1.2.3/doc', 'https://localhost/doc',
    'https://user:pass@example.com/doc', 'https://www.ikea.com/doc?secret=x', 'https://www.ikea.com/doc#x',
    'https://media.storageapi.dev/video', 'https://project.up.railway.app/doc',
    'https://vertexaisearch.cloud.google.com/grounding-api-redirect/opaque',
    'file:///tmp/file', 'https://host.internal/doc', 'https://example.com:8080/doc',
    'http://0x7f.0.0.1/private', 'http://0x7f.0x0.0.01/private',
    'http://0177.0.0.1/private', 'http://2130706433/private',
    'http://0x7f000001/private', 'http://127.1/private'])
def test_source_urls_must_be_canonical_public_not_private_or_signed(case, url):
    answer = _answer()
    answer['briefs'][0]['brief'] = 'Why did removable legs change furniture delivery? ' + url
    answer['briefs'][0]['sources'][0]['url'] = url
    case.generate.return_value = answer
    assert _run(case)['status'] == 'failed'


@pytest.mark.parametrize('url', ['https://www.ikea.com/doc', 'https://0xdeadbeef.example.com/doc',
                                 'https://3m.com/doc', 'https://8.8.8.8/doc'])
def test_numeric_host_guard_keeps_ordinary_domains_and_canonical_public_ips(case, url):
    assert case.ns['_public_url'](url) == url


@pytest.mark.parametrize('field,value', [('version', True), ('qa_approved', 0), ('publish_eligible', True),
                                     ('requires_full_research_and_critic', 1)])
def test_tampered_pending_flags_never_become_authority(case, field, value):
    _run(case)
    record = _record(case)
    record[field] = value
    case.client.set(case.keys['pending'], json.dumps(record))
    assert _run(case)['status'] == 'unavailable' and case.generate.call_count == 1


def test_existing_ready_briefs_are_revalidated_before_readout(case):
    _run(case)
    record = _record(case)
    record['briefs'][0]['sources'][0]['url'] = 'https://127.0.0.1/private'
    case.client.set(case.keys['pending'], json.dumps(record))
    assert _run(case)['status'] == 'unavailable' and case.generate.call_count == 1


@pytest.mark.parametrize('now', [True, -1, float('nan'), float('inf'), '100'])
def test_invalid_clock_never_reserves(case, now):
    assert _run(case, now)['status'] == 'unavailable'
    case.generate.assert_not_called()


def _provider_generate(case, **bindings):
    """Execute exact adapter body, replacing imports locally, never sys.modules."""
    tree = ast.parse(FILE.read_text(encoding='utf-8'))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_generate')
    class Imports(ast.NodeTransformer):
        def visit_ImportFrom(self, node):
            return None
    function = Imports().visit(function)
    isolated = {**case.ns, **bindings}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), str(FILE), 'exec'), isolated)
    return isolated['_generate']


def test_openai_adapter_single_configured_call_strict_schema_no_retries_or_storage(case):
    api = Mock()
    api.responses.create.return_value = SimpleNamespace(status='completed', output_text=json.dumps(_answer()))
    factory = Mock(return_value=api)
    generate = _provider_generate(case, OpenAI=factory)
    result = generate(case.ns['_context'](case.profile, case.channel), ('openai', 'configured-model', 'NOT_REAL_KEY'))
    assert result == _answer()
    factory.assert_called_once_with(api_key='NOT_REAL_KEY', timeout=90.0, max_retries=0)
    api.responses.create.assert_called_once()
    kwargs = api.responses.create.call_args.kwargs
    assert kwargs['model'] == 'configured-model' and kwargs['store'] is False
    assert kwargs['max_tool_calls'] == 2 and kwargs['max_output_tokens'] == 3600
    assert kwargs['tool_choice'] == 'required' and kwargs['tools'][0]['type'] == 'web_search'
    assert kwargs['text']['format']['strict'] is True
    assert kwargs['text']['format']['schema']['properties']['language']['enum'] == ['en']
    assert 'NOT_REAL_KEY' not in kwargs['input']
    api.close.assert_called_once()


@pytest.mark.parametrize('raw', ['{"can_prepare":true,"can_prepare":false}', '{"x":NaN}', '[]', '{bad'])
def test_openai_completed_but_invalid_json_is_definitively_bad_output(case, raw):
    api = Mock()
    api.responses.create.return_value = SimpleNamespace(status='completed', output_text=raw)
    generate = _provider_generate(case, OpenAI=Mock(return_value=api))
    with pytest.raises(case.ns['_InvalidOutput']):
        generate({'language': 'en'}, ('openai', 'configured-model', 'NOT_REAL_KEY'))
    api.close.assert_called_once()


def test_incomplete_openai_response_remains_uncertain_not_definitively_failed(case):
    api = Mock()
    api.responses.create.return_value = SimpleNamespace(status='in_progress', output_text='')
    case.ns['_generate'] = _provider_generate(case, OpenAI=Mock(return_value=api))
    assert _run(case)['status'] == 'uncertain'
    assert _run(case, NOW + 86400)['status'] == 'uncertain'
    api.responses.create.assert_called_once()


def test_gemini_adapter_reuses_existing_grounded_helper_once_without_fallback(case):
    call = Mock(return_value=_answer())
    generate = _provider_generate(case, generate_gemini_json=call, GeminiProtocolError=type('Protocol', (Exception,), {}))
    assert generate({'language': 'en'}, ('gemini', 'configured-gemini', 'NOT_REAL_KEY')) == _answer()
    call.assert_called_once()
    kwargs = call.call_args.kwargs
    assert kwargs['model'] == 'configured-gemini' and kwargs['retry_once'] is False
    assert kwargs['google_search'] is True and kwargs['timeout'] == 90.0
    assert kwargs['json_schema']['properties']['briefs']['maxItems'] == 4


def test_no_integration_with_existing_scheduler_or_provider_writes_added():
    tree = ast.parse(FILE.read_text(encoding='utf-8'))
    called = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert not called & {'save_channel_profile', 'create_job', 'mark_success', 'dispatch_due_productions',
                         'synthesize_scene_sequence', 'upload_video', 'set_video_release'}
