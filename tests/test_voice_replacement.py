"""Real Redis Lua with no provider/config import or live state access."""
import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import UUID

import fakeredis
import pytest


ROOT = Path(__file__).resolve().parents[1]
CHANNEL = 'UC5v9AvNtD3PTLgo6m1jROOA'
CONNECTION = 'same-oauth-connection'
REVISION = 'same-profile-revision'
SOURCE = '18300000-0000-4000-8000-000000000003'
CHILD = '18400000-0000-4000-8000-000000000004'
TOKEN = 'one-shot-replacement-token-1234'
AUDIO = 'a' * 64


def _load(name, namespace):
    path = ROOT / 'app/services' / (name + '.py')
    tree = ast.parse(path.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom) and (node.module or '').startswith('app.'))]
    module = ModuleType(name)
    module.__dict__.update(namespace)
    exec(compile(tree, str(path), 'exec'), module.__dict__)
    return module


def _write(client, key, value):
    client.set(key, json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')))


def _edit(case, key, path, value):
    obj = json.loads(case.client.get(key))
    cursor = obj
    for field in path[:-1]:
        cursor = cursor[field]
    cursor[path[-1]] = value
    _write(case.client, key, obj)


def _all(client):
    return {key: (client.type(key), client.hgetall(key) if client.type(key) == 'hash'
                 else sorted(client.smembers(key)) if client.type(key) == 'set'
                 else client.get(key)) for key in client.scan_iter()}


@pytest.fixture
def case():
    client = fakeredis.FakeRedis(decode_responses=True)
    settings = SimpleNamespace(redis_url='redis://never-used')
    state = _load('studio_state', {'settings': settings})
    ns = {key: getattr(state, key) for key in (
        'JOB_PREFIX', 'JOB_TTL_SECONDS', 'RETRY_DISPATCH_PREFIX', 'RETRY_CHILD_CLAIM_PREFIX',
        'RETRY_CHILD_EXECUTION_PREFIX', 'REPAIR_CHECKPOINT_PREFIX', 'REPAIR_CHECKPOINT_CLAIM_PREFIX',
        'RETRY_DISPATCH_TTL_SECONDS', 'PAID_CREATE_BUDGET_PREFIX', '_CLAIM_RETRY_DISPATCH')}
    ns.update(settings=settings, PROFILE_PREFIX='youtube_studio:youtube_profile:v1:',
              OAUTH_CHANNEL_PREFIX='youtube_studio:oauth:channel:v3:',
              OAUTH_CREDENTIAL_PREFIX='youtube_studio:oauth:credential:v3:',
              OAUTH_CHANNEL_INDEX='youtube_studio:oauth:channels:v3',
              AUTH_EPOCH_KEY='youtube_studio:oauth:authorization_epoch:v2',
              UPLOAD_PREFIX='youtube_studio:youtube_upload:v2:',
              EXECUTION_LOCK_PREFIX='youtube_studio:youtube_upload_lock:v2:')
    module = _load('voice_replacement', ns)
    module._redis = lambda: client
    spec = {'topic': 'The first barcode scan, verified historical documentary',
            'duration_minutes': .5, 'language': 'tr', 'channel_id': 'capital-corrupt',
            'mode': 'production', 'format': 'shorts', 'music': 'off', 'workflow': 'auto',
            'publish_after_render': True, 'production_scheduled': True,
            'production_channel_id': CHANNEL, 'production_connection_id': CONNECTION,
            'production_profile_revision': REVISION, 'production_topic_index': 1}
    prefix = f'audio_candidates/{SOURCE}/{AUDIO}'
    pointer = {'version': 1, 'status': 'unapproved_candidate', 'qa_approved': False,
               'requires_full_qa': True, 'audio_key': prefix + '/candidate.mp3',
               'metadata_key': prefix + '/metadata-' + 'b' * 64 + '.json',
               'audio_sha256': AUDIO, 'metadata_sha256': 'b' * 64,
               'package_sha256': 'c' * 64, 'size': 4000}
    source = {'task_id': SOURCE, 'kind': 'render', 'state': 'FAILURE', 'stage': 'failed',
              'failure_stage': 'audio_qc_retry', 'spec': spec, 'parent_id': None,
              'result': None, 'audio_candidate_checkpoint': pointer,
              'error': 'Existing prosody rejection remains unchanged'}
    profile = {'channel_id': CHANNEL, 'route_label': 'capital-corrupt',
               'profile_revision': REVISION, 'release_mode': 'public',
               'auto_publish': True, 'production_enabled': True, 'production_interval_hours': 6}
    _write(client, module.JOB_PREFIX + SOURCE, source)
    _write(client, module.PROFILE_PREFIX + CHANNEL, profile)
    _write(client, module.OAUTH_CHANNEL_PREFIX + CHANNEL, {'id': CHANNEL, 'connection_id': CONNECTION})
    client.set(module.OAUTH_CREDENTIAL_PREFIX + CHANNEL, 'encrypted-credential-sentinel')
    client.set(module.AUTH_EPOCH_KEY, '9')
    client.sadd(module.OAUTH_CHANNEL_INDEX, CHANNEL)
    client.hset(module.PAID_CREATE_BUDGET_PREFIX + SOURCE, mapping={'cap': '4', 'used': '0'})
    return SimpleNamespace(client=client, module=module, state=state, source=source, spec=spec, pointer=pointer)


def _reserve(c, child=CHILD):
    return c.module.reserve_voice_replacement(SOURCE, child, TOKEN, AUDIO)


def _child(c):
    result = _reserve(c)
    assert result['claimed'] is True
    _write(c.client, c.module.JOB_PREFIX + CHILD, {
        'task_id': CHILD, 'parent_id': SOURCE, 'kind': 'render', 'state': 'PROGRESS',
        'spec': deepcopy(c.spec), 'result': None})
    c.client.hset(c.module.PAID_CREATE_BUDGET_PREFIX + CHILD, mapping={'cap': '4', 'used': '0'})
    c.client.set(c.module.RETRY_CHILD_EXECUTION_PREFIX + CHILD, TOKEN)
    return result


def _get(c):
    return c.module.get_voice_replacement_policy(CHILD, SOURCE, AUDIO, c.spec)


def _acquire(c):
    return c.module.acquire_voice_replacement_attempt(CHILD, SOURCE, AUDIO, c.spec, 'KnownElevenVoice12345')


def test_reservation_reuses_exact_claim_and_preserves_original_audio_spec_error_and_cap(case):
    c, m = case, case.module
    before = deepcopy(c.source)
    result = _reserve(c)
    assert result['claimed'] is True and result['mode'] == 'full' and result['checkpoint'] is None
    assert result['spec'] == before['spec']
    assert result['voice_replacement']['model_id'] == 'eleven_multilingual_v2'
    source = json.loads(c.client.get(m.JOB_PREFIX + SOURCE))
    for key in ('spec', 'audio_candidate_checkpoint', 'error', 'state', 'failure_stage', 'result'):
        assert source[key] == before[key]
    assert source['voice_replacement']['model'] == 'eleven_multilingual_v2'
    assert source['voice_replacement']['requires_full_qa'] is True
    assert c.client.hgetall(m.RETRY_CHILD_CLAIM_PREFIX + CHILD) == {'source_task_id': SOURCE, 'token': TOKEN}
    assert c.client.hgetall(m.RETRY_DISPATCH_PREFIX + SOURCE)['mode'] == 'full'
    assert c.client.hgetall(m.PAID_CREATE_BUDGET_PREFIX + SOURCE) == {'cap': '4', 'used': '0'}
    for key in (m.POLICY_PREFIX + CHILD, m.LINEAGE_PREFIX + SOURCE):
        assert c.client.ttl(key) == -1
    assert not c.client.exists(m.REPAIR_CHECKPOINT_CLAIM_PREFIX + SOURCE)
    assert not c.client.exists(m.JOB_PREFIX + CHILD)  # caller creates the one claimed child
    assert 'encrypted-credential-sentinel' not in json.dumps(result)


def test_repeat_reservation_returns_existing_child_without_new_authority(case):
    _reserve(case)
    before = _all(case.client)
    other = str(UUID(int=77))
    assert _reserve(case, other) == {'claimed': False, 'child_task_id': CHILD}
    assert _all(case.client) == before


@pytest.mark.parametrize('stage', ['audio_qc', 'audio_qc_retry', 'audio_pause_recheck'])
def test_only_explicit_audio_failure_stages_allow_the_bounded_replacement(case, stage):
    _edit(case, case.module.JOB_PREFIX + SOURCE, ('failure_stage',), stage)
    assert _reserve(case)['claimed'] is True


def test_read_only_policy_then_acquire_once_before_http_and_never_reopen(case):
    c, m = case, case.module
    _child(c)
    before = _all(c.client)
    policy = _get(c)
    assert policy['model'] == 'eleven_multilingual_v2' and policy['max_attempts'] == 1
    assert not any('credential' in key or 'authorization' in key or 'token' in key for key in policy)
    assert _all(c.client) == before
    assert 'encrypted-credential-sentinel' not in json.dumps(policy)
    acquired = _acquire(c)
    assert acquired['voice_id'] == 'KnownElevenVoice12345'
    attempt = json.loads(c.client.get(m.ATTEMPT_PREFIX + CHILD))
    assert attempt['status'] == 'reserved_before_http' and attempt['attempts'] == 1
    assert c.client.ttl(m.ATTEMPT_PREFIX + CHILD) == -1
    before = _all(c.client)
    for operation in (_get, _acquire):
        with pytest.raises(m.VoiceReplacementError):
            operation(c)
    assert _all(c.client) == before


@pytest.mark.parametrize('path,value', [
    (('state',), 'SUCCESS'), (('kind',), 'plan'), (('failure_stage',), 'final_visual_qc'),
    (('spec', 'mode'), 'preview'), (('spec', 'format'), 'landscape'),
    (('spec', 'duration_minutes'), True), (('spec', 'duration_minutes'), 1),
    (('spec', 'language'), 'en'), (('spec', 'publish_after_render'), False),
    (('spec', 'production_scheduled'), False), (('spec', 'production_connection_id'), 'other-connection'),
    (('spec', 'production_profile_revision'), 'other-profile'), (('spec', 'channel_id'), 'other-channel'),
    (('result',), {'youtube': {'video_id': 'existingvideo'}}),
    (('voice_replacement',), {'version': 1}), (('retry_claimed',), True),
    (('audio_candidate_checkpoint', 'qa_approved'), True),
    (('audio_candidate_checkpoint', 'version'), True),
    (('audio_candidate_checkpoint', 'audio_sha256'), 'd' * 64),
    (('audio_candidate_checkpoint', 'audio_key'), 'another/task/candidate.mp3'),
    (('audio_candidate_checkpoint', 'metadata_key'), 'another/task/metadata.json'),
    (('audio_candidate_checkpoint', 'size'), True),
    (('audio_candidate_checkpoint', 'size'), 14 * 1024 * 1024 + 1),
])
def test_ineligible_source_cannot_claim_or_change_anything(case, path, value):
    c = case
    _edit(c, c.module.JOB_PREFIX + SOURCE, path, value)
    before = _all(c.client)
    with pytest.raises(c.module.VoiceReplacementError):
        _reserve(c)
    assert _all(c.client) == before


@pytest.mark.parametrize('field,value', [('production_enabled', False), ('auto_publish', False),
                                       ('release_mode', 'private'), ('profile_revision', 'changed')])
def test_current_profile_must_preserve_public_production_binding(case, field, value):
    c = case
    _edit(c, c.module.PROFILE_PREFIX + CHANNEL, (field,), value)
    before = _all(c.client)
    with pytest.raises(c.module.VoiceReplacementError):
        _reserve(c)
    assert _all(c.client) == before


@pytest.mark.parametrize('route_label', ['capital-corrupt', ' capital-corrupt ', '', None])
def test_actual_scheduler_route_expression_is_not_confused_with_oauth_channel_id(case, route_label):
    c, m = case, case.module
    _edit(c, m.PROFILE_PREFIX + CHANNEL, ('route_label',), route_label)
    profile = json.loads(c.client.get(m.PROFILE_PREFIX + CHANNEL))
    tree = ast.parse((ROOT / 'app/services/channel_production.py').read_text(encoding='utf-8'))
    expression = next(node.value for node in ast.walk(tree) if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == 'route' for target in node.targets))
    actual_route = eval(compile(ast.Expression(expression), '<actual-scheduler-route>', 'eval'),
                        {'profile': profile, 'channel_id': CHANNEL})
    _edit(c, m.JOB_PREFIX + SOURCE, ('spec', 'channel_id'), actual_route)
    result = _reserve(c)
    assert result['claimed'] is True and result['spec']['channel_id'] == actual_route
    assert result['spec']['production_channel_id'] == CHANNEL


@pytest.mark.parametrize('wrong_route', [None, CHANNEL, 'margin-verdict', 'CAPITAL-CORRUPT'])
def test_route_must_equal_frozen_profile_route_without_cross_channel_or_fuzzy_aliases(case, wrong_route):
    c = case
    _edit(c, c.module.JOB_PREFIX + SOURCE, ('spec', 'channel_id'), wrong_route)
    before = _all(c.client)
    with pytest.raises(c.module.VoiceReplacementError): _reserve(c)
    assert _all(c.client) == before


def test_route_change_after_reservation_cannot_authorize_paid_http(case):
    c = case
    _child(c)
    _edit(c, c.module.PROFILE_PREFIX + CHANNEL, ('route_label',), 'margin-verdict')
    before = _all(c.client)
    with pytest.raises(c.module.VoiceReplacementError): _acquire(c)
    assert _all(c.client) == before


@pytest.mark.parametrize('prefix', ['UPLOAD_PREFIX', 'EXECUTION_LOCK_PREFIX', 'REPAIR_CHECKPOINT_PREFIX',
                                   'REPAIR_CHECKPOINT_CLAIM_PREFIX'])
def test_any_existing_or_ambiguous_publication_or_repair_record_rejects(case, prefix):
    c = case
    c.client.set(getattr(c.module, prefix) + SOURCE, 'existing-record')
    before = _all(c.client)
    with pytest.raises(c.module.VoiceReplacementError):
        _reserve(c)
    assert _all(c.client) == before


@pytest.mark.parametrize('change', ['missing_ledger', 'paid', 'invalid_cap', 'missing_credential',
                                   'missing_index', 'reconnect', 'wrong_connection'])
def test_authoritative_budget_and_oauth_are_fail_closed(case, change):
    c, m = case, case.module
    if change == 'missing_ledger': c.client.delete(m.PAID_CREATE_BUDGET_PREFIX + SOURCE)
    elif change == 'paid': c.client.hset(m.PAID_CREATE_BUDGET_PREFIX + SOURCE, 'used', '1')
    elif change == 'invalid_cap': c.client.hset(m.PAID_CREATE_BUDGET_PREFIX + SOURCE, 'cap', '4.0')
    elif change == 'missing_credential': c.client.delete(m.OAUTH_CREDENTIAL_PREFIX + CHANNEL)
    elif change == 'missing_index': c.client.srem(m.OAUTH_CHANNEL_INDEX, CHANNEL)
    elif change == 'reconnect': _edit(c, m.OAUTH_CHANNEL_PREFIX + CHANNEL, ('requires_reconnect',), True)
    else: _edit(c, m.OAUTH_CHANNEL_PREFIX + CHANNEL, ('connection_id',), 'other-connection')
    before = _all(c.client)
    with pytest.raises(m.VoiceReplacementError): _reserve(c)
    assert _all(c.client) == before


def _ancestor(c):
    m = c.module
    ancestor = str(UUID(int=100))
    parent = deepcopy(c.source)
    parent.update(task_id=ancestor, retry_claimed=True, retry_child_task_id=SOURCE)
    _write(c.client, m.JOB_PREFIX + ancestor, parent)
    _edit(c, m.JOB_PREFIX + SOURCE, ('parent_id',), ancestor)
    c.client.hset(m.PAID_CREATE_BUDGET_PREFIX + ancestor, mapping={'used': '0', 'cap': '4'})
    c.client.hset(m.RETRY_DISPATCH_PREFIX + ancestor, mapping={
        'token': 'ancestor-claim-token-1234', 'child_task_id': SOURCE, 'mode': 'full', 'state': 'dispatched'})
    c.client.hset(m.RETRY_CHILD_CLAIM_PREFIX + SOURCE, mapping={
        'source_task_id': ancestor, 'token': 'ancestor-claim-token-1234'})
    c.client.set(m.RETRY_CHILD_EXECUTION_PREFIX + SOURCE, 'ancestor-claim-token-1234')
    return ancestor


def test_reciprocal_audio_retry_lineage_gets_one_root_allowance(case):
    root = _ancestor(case)
    _reserve(case)
    assert case.client.exists(case.module.LINEAGE_PREFIX + root)
    assert not case.client.exists(case.module.LINEAGE_PREFIX + SOURCE)


@pytest.mark.parametrize('change', ['marker', 'private_marker', 'bad_claim', 'no_execution', 'different_spec', 'cycle'])
def test_prior_lineage_replacement_or_unproven_parent_is_rejected(case, change):
    c, m = case, case.module
    ancestor = _ancestor(c)
    if change == 'marker': _edit(c, m.JOB_PREFIX + ancestor, ('voice_replacement',), {'version': 1})
    elif change == 'private_marker': c.client.set(m.LINEAGE_PREFIX + ancestor, '{}')
    elif change == 'bad_claim': c.client.hset(m.RETRY_CHILD_CLAIM_PREFIX + SOURCE, 'token', 'wrong')
    elif change == 'no_execution': c.client.delete(m.RETRY_CHILD_EXECUTION_PREFIX + SOURCE)
    elif change == 'different_spec': _edit(c, m.JOB_PREFIX + ancestor, ('spec', 'topic'), 'different')
    else: _edit(c, m.JOB_PREFIX + ancestor, ('parent_id',), SOURCE)
    before = _all(c.client)
    with pytest.raises(m.VoiceReplacementError): _reserve(c)
    assert _all(c.client) == before


@pytest.mark.parametrize('change', ['spec', 'audio', 'profile', 'credential', 'epoch', 'claim', 'execution',
                                   'paid_child', 'paid_parent', 'publication', 'policy', 'root_policy'])
def test_changed_bindings_before_paid_http_never_consume_or_authorize(case, change):
    c, m = case, case.module
    _child(c)
    if change == 'spec': _edit(c, m.JOB_PREFIX + CHILD, ('spec', 'topic'), 'changed')
    elif change == 'audio': _edit(c, m.JOB_PREFIX + SOURCE, ('audio_candidate_checkpoint', 'audio_sha256'), 'd' * 64)
    elif change == 'profile': _edit(c, m.PROFILE_PREFIX + CHANNEL, ('production_interval_hours',), 12)
    elif change == 'credential': c.client.set(m.OAUTH_CREDENTIAL_PREFIX + CHANNEL, 'rotated-encrypted')
    elif change == 'epoch': c.client.incr(m.AUTH_EPOCH_KEY)
    elif change == 'claim': c.client.hset(m.RETRY_CHILD_CLAIM_PREFIX + CHILD, 'token', 'changed')
    elif change == 'execution': c.client.delete(m.RETRY_CHILD_EXECUTION_PREFIX + CHILD)
    elif change == 'paid_child': c.client.hset(m.PAID_CREATE_BUDGET_PREFIX + CHILD, 'used', '1')
    elif change == 'paid_parent': c.client.hset(m.PAID_CREATE_BUDGET_PREFIX + SOURCE, 'used', '1')
    elif change == 'publication': c.client.set(m.UPLOAD_PREFIX + CHILD, '{}')
    elif change == 'policy': _edit(c, m.POLICY_PREFIX + CHILD, ('max_attempts',), 2)
    else: c.client.set(m.LINEAGE_PREFIX + SOURCE, '{}')
    before = _all(c.client)
    with pytest.raises(m.VoiceReplacementError): _acquire(c)
    assert _all(c.client) == before


@pytest.mark.parametrize('operation', ['reserve', 'acquire'])
def test_atomic_snapshot_change_immediately_before_eval_is_rejected(case, monkeypatch, operation):
    c, m = case, case.module
    if operation == 'acquire': _child(c)
    real_eval = c.client.eval
    def race(*args):
        c.client.incr(m.AUTH_EPOCH_KEY)
        return real_eval(*args)
    monkeypatch.setattr(c.client, 'eval', race)
    with pytest.raises(m.VoiceReplacementError):
        (_reserve if operation == 'reserve' else _acquire)(c)
    assert not c.client.exists(m.ATTEMPT_PREFIX + CHILD)
    if operation == 'reserve':
        assert not c.client.exists(m.POLICY_PREFIX + CHILD)
        assert not c.client.exists(m.RETRY_DISPATCH_PREFIX + SOURCE)


@pytest.mark.parametrize('operation', ['reserve', 'acquire'])
def test_lost_redis_reply_preserves_one_shot_reservation_without_replay(case, monkeypatch, operation):
    c, m = case, case.module
    if operation == 'acquire': _child(c)
    real_eval = c.client.eval
    def lost_reply(*args):
        real_eval(*args)
        raise TimeoutError('credential-sentinel must not escape')
    monkeypatch.setattr(c.client, 'eval', lost_reply)
    with pytest.raises(m.VoiceReplacementError, match='^voice_replacement_unavailable$'):
        (_reserve if operation == 'reserve' else _acquire)(c)
    monkeypatch.setattr(c.client, 'eval', real_eval)
    if operation == 'reserve':
        assert _reserve(c)['claimed'] is False
    else:
        assert c.client.exists(m.ATTEMPT_PREFIX + CHILD)
        with pytest.raises(m.VoiceReplacementError): _acquire(c)


def test_concurrent_acquires_authorize_at_most_one_http_request(case):
    c = case
    _child(c)
    def attempt(_):
        try: return _acquire(c)
        except c.module.VoiceReplacementError: return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(attempt, range(6)))
    assert sum(value is not None for value in results) == 1


@pytest.mark.parametrize('voice_id', [None, '', 'https://provider.invalid/voice', '../voice', True])
def test_invalid_selected_voice_never_spends_attempt(case, voice_id):
    c = case
    _child(c)
    with pytest.raises(c.module.VoiceReplacementError):
        c.module.acquire_voice_replacement_attempt(CHILD, SOURCE, AUDIO, c.spec, voice_id)
    assert not c.client.exists(c.module.ATTEMPT_PREFIX + CHILD)
