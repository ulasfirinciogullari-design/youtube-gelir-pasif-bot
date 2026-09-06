"""Real scheduler/preparation wire records and Redis transactions; no providers."""
import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

from test_channel_production import production, _profile, _save, CHANNEL, CONNECTION, ROOT
from test_production_continuous_public import _public_finish
from test_production_next_series import _seed_preparation_binding


NOW = datetime(2026, 9, 6, 12, tzinfo=timezone.utc).timestamp()
URL = 'https://www.ikea.com/global/en/our-business/how-we-work/'


class _NoAppImports(ast.NodeTransformer):
    def visit_ImportFrom(self, node):
        return None if (node.module or '').startswith('app.') else node


def _load(relative, namespace, *, functions=None):
    path = ROOT / relative
    tree = _NoAppImports().visit(ast.parse(path.read_text(encoding='utf-8')))
    if functions is not None:
        tree.body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in functions]
    exec(compile(ast.fix_missing_locations(tree), str(path), 'exec'), namespace)
    return namespace


def _answer(number=0):
    choices = [
        ('Dağıtımı Değiştiren Fikirler', 'Sökülebilen masa ayakları mobilya taşımacılığını nasıl değiştirdi? '),
        ('Mağazalarda Yeni Dönem', 'Müşterinin ürünü raftan alması perakende maliyetlerini nasıl dönüştürdü? '),
        ('Küresel Ticaretin Kutuları', 'Standart yük konteyneri limanlarda beklenen zamanı neden azalttı? '),
    ]
    title, brief = choices[number]
    return {'can_prepare': True, 'language': 'tr', 'series_title': title,
            'briefs': [{'brief': brief + URL, 'sources': [{'url': URL,
                        'evidence': 'The cited source describes the business decision; full factual review is still required.'}]}]}


def _write(client, key, value):
    client.set(key, json.dumps(value, ensure_ascii=False))


def _change(case, key, path, value):
    record = json.loads(case.client.get(key))
    row = record
    for part in path[:-1]:
        row = row[part]
    row[path[-1]] = value
    _write(case.client, key, record)


def _snapshot(case):
    return {key: case.client.dump(key) for key in case.client.keys('*')}


def _finish_series(case, profile, now=NOW - 1000):
    last = None
    for index in range(len(profile['production_topics'])):
        result = case.scheduler.dispatch_due_productions([profile], [CONNECTION], Mock(), now=now + index * 10)
        assert result['status'] == 'queued'
        last = result['task_id']
        keys = _public_finish(case.scheduler, case.client, last)
        ledger = json.loads(case.client.get(keys['ledger']))
        ledger['publish_plan']['series'] = {'id': profile['series_id'], 'name': profile['series_name'],
                                          'number': index + 1, 'total': profile['series_total']}
        _write(case.client, keys['ledger'], ledger)
        scope = CHANNEL + ':' + profile['series_id']
        case.client.set(case.ns['SERIES_COUNTER_PREFIX'] + scope, str(index + 1))
        case.client.set(case.ns['SERIES_ASSIGNMENT_PREFIX'] + scope + ':' + last, str(index + 1))
        assert case.scheduler.reconcile_active_production(now=now + index * 10 + 1) == 'completed'
    return last, keys


def _prepare(case, number=0, now=NOW - 100):
    case.prep['_generate'] = Mock(return_value=_answer(number))
    profile = json.loads(case.client.get(case.profile_key))
    binding = _seed_preparation_binding(case.prep, case.client, profile, CONNECTION, now)
    result = case.prep['prepare_next_series'](profile, CONNECTION, now=now, execution_binding=binding)
    if result['status'] == 'ready':
        case.attempt = result['attempt_id']
        case.revision = profile['profile_revision']
    return result


@pytest.fixture
def case(production):
    scheduler, client = production
    ns = {'settings': SimpleNamespace(redis_url='redis://not-used'), **{
        key: getattr(scheduler, key) for key in ('ACTIVE_KEY', 'CHANNEL_STATE_PREFIX', 'OAUTH_CHANNEL_INDEX',
            'OAUTH_CHANNEL_PREFIX', 'OAUTH_CREDENTIAL_PREFIX', 'PROFILE_PREFIX', 'PRODUCTION_PREFIX',
            '_decode_active_claims', '_prefix_digest', 'JOB_PREFIX', 'JOB_INDEX')},
        'MAX_INDEXED_JOBS': 500, 'AUTH_EPOCH_KEY': 'youtube_studio:oauth:authorization_epoch:v2',
        'SERIES_COUNTER_PREFIX': 'youtube_studio:youtube_series_counter:v1:',
        'SERIES_ASSIGNMENT_PREFIX': 'youtube_studio:youtube_series_assignment:v1:',
        'UPLOAD_PREFIX': scheduler.PUBLICATION_UPLOAD_PREFIX,
        'RETRY_CHILD_CLAIM_PREFIX': 'youtube_studio:retry_child_claim:',
        'RETRY_DISPATCH_PREFIX': 'youtube_studio:retry_dispatch:',
        'RETRY_CHILD_EXECUTION_PREFIX': 'youtube_studio:retry_child_execution:',
        'REPAIR_CHECKPOINT_CLAIM_PREFIX': 'youtube_studio:repair_checkpoint_claim:',
    }
    quality = _load('app/services/youtube_automation.py', {'Any': object},
                    functions={'automated_quality_approved', 'contains_synthetic_media'})
    ns.update({k: quality[k] for k in ('automated_quality_approved', 'contains_synthetic_media')})
    recovery = _load('app/services/production_recovery.py', {**ns})
    ns['_audit_result'] = recovery['_audit_result']
    _load('app/services/production_series_promotion.py', ns)
    ns['redis'] = SimpleNamespace(Redis=SimpleNamespace(from_url=Mock(return_value=client)))
    evidence = _load('app/services/source_evidence.py', {})
    prep = _load('app/services/production_next_series.py', {
        **ns, 'normalize_evidence_sources': evidence['normalize_evidence_sources']})
    ns['_planning_channel_identity'] = prep['_planning_channel_identity']
    prep['redis'] = ns['redis']
    prep['_configuration'] = Mock(return_value=('openai', 'configured-model', 'opaque-fixture-key'))
    profile = _profile(release_mode='public', require_thumbnail=True, series_id='first-series',
                       series_name='Gündelik Bilimin İzleri', series_total=2)
    _save(scheduler, client, profile)
    client.set(ns['AUTH_EPOCH_KEY'], '4')
    result = SimpleNamespace(ns=ns, prep=prep, client=client, scheduler=scheduler, profile=profile,
                             profile_key=scheduler.PROFILE_PREFIX + CHANNEL,
                             state_key=scheduler.CHANNEL_STATE_PREFIX + CHANNEL,
                             pending_key=ns['PENDING_PREFIX'] + CHANNEL)
    result.source_id, result.keys = _finish_series(result, profile)
    assert _prepare(result)['status'] == 'ready'
    result.daily_key = ns['DAILY_PREFIX'] + CHANNEL + ':2026-09-06'
    return result


def _run(case, now=NOW):
    return case.ns['promote_ready_series'](CHANNEL, case.revision, case.attempt, now=now)


def test_real_ready_batch_rotates_explicit_epoch_archives_and_ordinary_tick_starts(case):
    before = _snapshot(case)
    old_state = case.client.hgetall(case.state_key)
    result = _run(case)
    assert result['status'] == 'promoted' and result['epoch'] == 1
    assert all(result[k] is False for k in ('qa_approved', 'publish_eligible', 'media_budget_approved'))
    assert result['requires_full_research_and_critic'] is True
    profile = json.loads(case.client.get(case.profile_key))
    assert profile['production_topics'] == [_answer()['briefs'][0]['brief']]
    assert profile['series_id'] != case.profile['series_id'] and profile['series_total'] == 1
    assert profile['profile_revision'] == result['new_profile_revision'] and profile['series_epoch'] == 1
    changed = {'series_id', 'series_name', 'series_total', 'production_topics', 'profile_revision', 'series_epoch', 'updated_at'}
    assert {k: v for k, v in profile.items() if k not in changed} == {
        k: v for k, v in case.profile.items() if k not in changed}
    archive = json.loads(case.client.get(result['archive_key']))
    assert archive['profile'] == case.profile and archive['state'] == old_state
    assert archive['pending_batch']['status'] == 'ready'
    assert archive['public_proof']['source_task_id'] == case.source_id
    assert archive['public_proof']['privacy_status'] == 'public'
    assert result['archive_sha256'] == case.ns['_digest'](archive)
    assert 'opaque-test-credential' not in json.dumps(archive)
    assert case.client.get(case.pending_key) is None
    assert case.client.dump(case.daily_key) == before[case.daily_key] and case.client.ttl(case.daily_key) == -1
    mutable = {case.profile_key, case.state_key, case.pending_key}
    assert all(case.client.dump(k) == v for k, v in before.items() if k not in mutable)
    assert case.client.get(case.ns['SERIES_COUNTER_PREFIX'] + CHANNEL + ':' + profile['series_id']) is None
    assert case.client.hget(case.state_key, 'cursor') == '0'
    queued = case.scheduler.dispatch_due_productions([profile], [CONNECTION], Mock(), now=NOW)
    job = json.loads(case.client.get(case.ns['JOB_PREFIX'] + queued['task_id']))
    assert queued['status'] == 'queued' and job['spec']['production_topic_index'] == 0
    assert job['spec']['production_profile_revision'] == profile['profile_revision']
    assert job['spec']['production_scheduled'] is True and job['spec']['publish_after_render'] is True
    assert 'qa_approved' not in job and job.get('result') is None
    assert case.prep['_generate'].call_count == 1


def test_duplicate_and_concurrent_calls_promote_once_without_resetting_next_job(case):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: _run(case), range(12)))
    assert sum(r['status'] == 'promoted' for r in results) == 1
    assert len({r['new_profile_revision'] for r in results}) == 1
    profile = json.loads(case.client.get(case.profile_key))
    case.scheduler.dispatch_due_productions([profile], [CONNECTION], Mock(), now=NOW)
    before = _snapshot(case)
    assert _run(case, NOW + 60)['status'] == 'already_promoted'
    assert _snapshot(case) == before and case.client.hget(case.state_key, 'cursor') == '1'


def test_second_epoch_preserves_history_and_daily_budget_fence(case):
    first = _run(case)
    profile = json.loads(case.client.get(case.profile_key))
    _finish_series(case, profile, now=NOW + 10)
    before = _snapshot(case)
    result = _prepare(case, number=1, now=NOW + 100)
    # The day's durable worker reservation still belongs to the prior profile;
    # rotation cannot grant another task or repurpose that execution token.
    assert result['status'] == 'unavailable'
    assert _snapshot(case) == before
    case.prep['_generate'].assert_not_called()
    assert _prepare(case, number=1, now=NOW + 86400)['status'] == 'ready'
    second = _run(case, NOW + 86410)
    assert second['epoch'] == 2 and second['new_series_id'] != first['new_series_id']
    assert case.client.exists(first['archive_key']) and case.client.exists(second['archive_key'])
    assert case.client.get(case.ns['SERIES_COUNTER_PREFIX'] + CHANNEL + ':' + profile['series_id']) == '1'


@pytest.mark.parametrize('name,path,value', [
    ('source', ['result', 'manual_qa_required'], True),
    ('source', ['result', 'quality_disposition'], 'manual_qa_preview'),
    ('source', ['result', 'youtube', 'privacy_status'], 'private'),
    ('source', ['result', 'youtube', 'caption_uploaded'], False),
    ('source', ['result', 'youtube', 'contains_synthetic_media'], False),
    ('source', ['spec', 'production_topic_index'], 0),
    ('source', ['spec', 'topic'], 'Changed old episode'),
    ('source', ['result', 'caption_key'], 'videos/other/captions.tr.srt'),
    ('publisher', ['state'], 'STARTED'),
    ('publisher', ['result', 'release_status'], 'uncertain'),
    ('publisher', ['result', 'caption_error_code'], 'not-uploaded'),
    ('publisher', ['result', 'scheduled_publish_at'], '2099-01-01'),
    ('publisher', ['result', 'youtube_video_id'], 'Different11'),
    ('ledger', ['release_side_effect_possible'], False),
    ('ledger', ['release_completed_at'], None),
    ('ledger', ['publish_plan', 'series', 'number'], 1),
    ('ledger', ['publish_plan', 'series', 'total'], True),
    ('ledger', ['publish_plan', 'contains_synthetic_media'], False),
    ('profile', ['profile_revision'], 'changed-revision'),
    ('profile', ['series_total'], 3),
    ('profile', ['production_enabled'], False),
    ('channel', ['connection_id'], 'new-connection'),
    ('channel', ['requires_reconnect'], True),
])
def test_changed_or_nonpublic_last_episode_never_rotates(case, name, path, value):
    _change(case, case.keys[name], path, value)
    before = _snapshot(case)
    with pytest.raises(case.ns['SeriesPromotionError']):
        _run(case)
    assert _snapshot(case) == before


@pytest.mark.parametrize('field,value', [('cursor', '1'), ('cursor', '02'), ('consumed_prefix', 'wrong'),
    ('paused_reason', 'previous_render_failed'), ('active_task_id', str(UUID(int=40))),
    ('last_public_task_id', str(UUID(int=50))), ('last_result', 'FAILURE'),
    ('dispatch_status', 'reserved'), ('profile_revision', 'different-revision')])
def test_only_exact_exhausted_idle_receipt_can_rotate(case, field, value):
    case.client.hset(case.state_key, field, value)
    before = _snapshot(case)
    with pytest.raises(case.ns['SeriesPromotionError']):
        _run(case)
    assert _snapshot(case) == before


@pytest.mark.parametrize('field,value', [('status', 'reserved'), ('status', 'uncertain'),
    ('qa_approved', True), ('publish_eligible', True), ('media_budget_approved', True),
    ('requires_full_research_and_critic', False), ('evidence_validation', 'verified'),
    ('context_sha256', '0' * 64), ('profile_sha256', '1' * 64), ('channel_sha256', '2' * 64),
    ('attempt_id', 'a' * 32), ('version', True), ('language', 'en'), ('provider', 'invented'),
    ('day', '2099-01-01'), ('extra', 'unrecognized'), ('series_title', 'secret=private')])
def test_ready_contract_cannot_be_forged_by_loose_flags_or_stale_context(case, field, value):
    for key in (case.pending_key, case.daily_key):
        _change(case, key, [field], value)
    before = _snapshot(case)
    with pytest.raises(case.ns['SeriesPromotionError']):
        _run(case)
    assert _snapshot(case) == before


@pytest.mark.parametrize('url', ['http://127.0.0.1/a', 'http://0x7f.0.0.1/a',
    'https://a.storageapi.dev/private', 'https://example.com/a?token=value',
    'https://user:password@example.com/a', 'http://localhost/a'])
def test_nonpublic_source_or_embedded_credential_is_never_promoted(case, url):
    for key in (case.pending_key, case.daily_key):
        record = json.loads(case.client.get(key))
        record['briefs'][0]['sources'][0]['url'] = url
        record['briefs'][0]['brief'] = 'Sökülen ayaklar ürünün paketini nasıl küçülttü? ' + url
        _write(case.client, key, record)
    before = _snapshot(case)
    with pytest.raises(case.ns['SeriesPromotionError']):
        _run(case)
    assert _snapshot(case) == before


@pytest.mark.parametrize('missing', ['daily', 'credential', 'membership', 'counter', 'assignment', 'ledger'])
def test_missing_durable_proof_does_not_change_any_state(case, missing):
    scope = CHANNEL + ':' + case.profile['series_id']
    if missing == 'membership':
        case.client.srem(case.ns['OAUTH_CHANNEL_INDEX'], CHANNEL)
    else:
        keys = {'daily': case.daily_key, 'credential': case.ns['OAUTH_CREDENTIAL_PREFIX'] + CHANNEL,
                'counter': case.ns['SERIES_COUNTER_PREFIX'] + scope,
                'assignment': case.ns['SERIES_ASSIGNMENT_PREFIX'] + scope + ':' + case.source_id,
                'ledger': case.keys['ledger']}
        case.client.delete(keys[missing])
    before = _snapshot(case)
    with pytest.raises(case.ns['SeriesPromotionError']):
        _run(case)
    assert _snapshot(case) == before


@pytest.mark.parametrize('same_channel', [True, False])
def test_same_channel_active_blocks_but_other_channel_is_untouched(case, same_channel):
    channel = CHANNEL if same_channel else 'UC_other_channel'
    task_id = str(UUID(int=998))
    claim = json.dumps({'version': 2, 'claims': [{'channel_id': channel, 'task_id': task_id}]})
    case.client.set(case.ns['ACTIVE_KEY'], claim)
    _write(case.client, case.ns['JOB_PREFIX'] + task_id, {'task_id': task_id, 'state': 'STARTED',
        'kind': 'render', 'spec': {'production_channel_id': channel}})
    case.client.zadd(case.ns['JOB_INDEX'], {task_id: NOW})
    before = _snapshot(case)
    if same_channel:
        with pytest.raises(case.ns['SeriesPromotionError']):
            _run(case)
        assert _snapshot(case) == before
    else:
        assert _run(case)['status'] == 'promoted'
        for key in (case.ns['ACTIVE_KEY'], case.ns['JOB_INDEX'], case.ns['JOB_PREFIX'] + task_id):
            assert case.client.dump(key) == before[key]


@pytest.mark.parametrize('target', ['profile', 'state', 'credential', 'epoch', 'pending', 'daily',
                                    'source', 'publisher', 'ledger', 'membership', 'active', 'job_index', 'history'])
def test_watch_compare_rejects_concurrent_proof_or_authority_change(case, monkeypatch, target):
    original = case.ns['_Snapshot'].compare
    def racing(snapshot, pipe):
        original(snapshot, pipe)
        if target == 'state': case.client.hset(case.state_key, 'cursor', '1')
        elif target == 'membership': case.client.srem(case.ns['OAUTH_CHANNEL_INDEX'], CHANNEL)
        elif target == 'job_index': case.client.zadd(case.ns['JOB_INDEX'], {str(UUID(int=600)): NOW})
        elif target == 'history':
            case.client.sadd(case.ns['TOPIC_HISTORY_PREFIX'] + CHANNEL,
                            case.ns['_digest'](case.ns['_text_key'](_answer()['briefs'][0]['brief'])))
        else:
            key = {'profile': case.profile_key, 'credential': case.ns['OAUTH_CREDENTIAL_PREFIX'] + CHANNEL,
                   'epoch': case.ns['AUTH_EPOCH_KEY'], 'pending': case.pending_key, 'daily': case.daily_key,
                   'source': case.keys['source'], 'publisher': case.keys['publisher'], 'ledger': case.keys['ledger'],
                   'active': case.ns['ACTIVE_KEY']}[target]
            case.client.set(key, 'changed-after-watch')
    monkeypatch.setattr(case.ns['_Snapshot'], 'compare', racing)
    with pytest.raises(case.ns['SeriesPromotionError']):
        _run(case)
    assert not case.client.keys(case.ns['RECEIPT_PREFIX'] + '*')
    assert not case.client.keys(case.ns['ARCHIVE_PREFIX'] + '*')


def test_committed_lost_reply_is_recovered_without_second_transaction(case, monkeypatch):
    pipeline = case.client.pipeline
    count = []
    def wrapper(*args, **kwargs):
        pipe = pipeline(*args, **kwargs)
        execute = pipe.execute
        def lost(*args, **kwargs):
            result = execute(*args, **kwargs)
            count.append(result)
            raise ConnectionError('simulated committed response loss')
        pipe.execute = lost
        return pipe
    monkeypatch.setattr(case.client, 'pipeline', wrapper)
    assert _run(case)['status'] == 'already_promoted'
    before = _snapshot(case)
    assert _run(case)['status'] == 'already_promoted'
    assert _snapshot(case) == before and len(count) == 1


def test_full_retry_receipt_requires_actual_claimed_public_descendant(case):
    ns = case.ns
    original = json.loads(case.client.get(case.keys['source']))
    child_id, token = str(UUID(int=900)), 'durable-retry-token'
    child = deepcopy(original)
    child.update(task_id=child_id, parent_id=case.source_id)
    child['result'] = None
    _write(case.client, ns['JOB_PREFIX'] + child_id, child)
    keys = _public_finish(case.scheduler, case.client, child_id)
    old_ledger = json.loads(case.client.get(case.keys['ledger']))
    _change(case, keys['ledger'], ['publish_plan', 'series'], old_ledger['publish_plan']['series'])
    case.client.delete(case.keys['ledger'])
    original.update(state='FAILURE', result=None, retry_claimed=True, retry_child_task_id=child_id,
                    retry_dispatch_state='dispatched')
    _write(case.client, case.keys['source'], original)
    case.client.hset(ns['RETRY_DISPATCH_PREFIX'] + case.source_id,
        mapping={'child_task_id': child_id, 'token': token, 'mode': 'full', 'state': 'dispatched'})
    case.client.hset(ns['RETRY_CHILD_CLAIM_PREFIX'] + child_id,
        mapping={'source_task_id': case.source_id, 'token': token})
    case.client.set(ns['RETRY_CHILD_EXECUTION_PREFIX'] + child_id, token)
    case.client.set(ns['SERIES_ASSIGNMENT_PREFIX'] + CHANNEL + ':' + case.profile['series_id'] + ':' + child_id, '2')
    case.client.hset(case.state_key, mapping={'last_result': 'FAILURE', 'next_due': str(NOW - 1)})
    source = json.loads(case.client.get(keys['source']))
    publisher_id = source['result']['youtube_automation']['publish_task_id']
    audit = {'version': 1, 'channel_id': CHANNEL, 'original_task_id': case.source_id,
        'recovered_task_id': child_id, 'publish_task_id': publisher_id, 'youtube_video_id': 'Public00000',
        'profile_revision': case.revision, 'connection_id': CONNECTION['connection_id'],
        'cursor': 2, 'previous_paused_reason': 'previous_render_failed', 'resumed_at': NOW - 1,
        'next_due': NOW - 1, 'release_mode': 'public', 'release_status': 'public',
        'caption_uploaded': True, 'contains_synthetic_media': True, 'continue_immediately': True}
    _write(case.client, ns['_PUBLIC_RESUME_PREFIX'] + CHANNEL + ':' + case.source_id, audit)
    before = _snapshot(case)
    case.client.delete(ns['RETRY_CHILD_EXECUTION_PREFIX'] + child_id)
    with pytest.raises(ns['SeriesPromotionError']):
        _run(case)
    case.client.set(ns['RETRY_CHILD_EXECUTION_PREFIX'] + child_id, token)
    assert _snapshot(case) == before
    result = _run(case)
    archive = json.loads(case.client.get(result['archive_key']))
    assert archive['public_proof']['lineage'] == [case.source_id, child_id]


def test_only_pure_channel_identity_helper_is_imported_from_preparation():
    path = ROOT / 'app/services/production_series_promotion.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    imports = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
               and n.module == 'app.services.production_next_series']
    assert len(imports) == 1
    assert [(name.name, name.asname) for name in imports[0].names] == [('_planning_channel_identity', None)]
    assert not any(name in path.read_text(encoding='utf-8') for name in ('apply_async(', 'videos.insert(', 'OpenAI('))


@pytest.mark.parametrize('mutation', ['missing_epoch', 'delete_all_visible_markers', 'missing_history',
    'history_loss', 'prior_receipt', 'prior_archive', 'profile_epoch_bool', 'history_count_bool',
    'prior_old_revision_missing', 'prior_old_series_changed', 'prior_pending_hash_changed'])
def test_later_epoch_cannot_downgrade_or_lose_archived_lineage(case, mutation):
    first = _run(case)
    profile = json.loads(case.client.get(case.profile_key))
    _finish_series(case, profile, now=NOW + 10)
    assert _prepare(case, number=1, now=NOW + 86400)['status'] == 'ready'
    epoch_key, history_key = case.ns['EPOCH_PREFIX'] + CHANNEL, case.ns['TOPIC_HISTORY_PREFIX'] + CHANNEL
    if mutation in {'missing_epoch', 'delete_all_visible_markers'}:
        case.client.delete(epoch_key)
        if mutation == 'delete_all_visible_markers':
            profile.pop('series_epoch')
            _write(case.client, case.profile_key, profile)
            case.client.hdel(case.state_key, 'series_epoch', 'last_series_promotion')
            # Rebind the ready bytes as a malicious downgrade attempt, so this
            # reaches the private history/epoch fence instead of hash mismatch.
            for key in (case.pending_key, case.ns['DAILY_PREFIX'] + CHANNEL + ':2026-09-07'):
                _change(case, key, ['profile_sha256'], case.ns['_digest'](profile))
    elif mutation == 'missing_history': case.client.delete(history_key)
    elif mutation == 'history_loss':
        case.client.srem(history_key, case.ns['_digest'](case.ns['_text_key'](case.profile['production_topics'][0])))
    elif mutation == 'prior_receipt':
        _change(case, case.ns['RECEIPT_PREFIX'] + CHANNEL + ':' + first['attempt_id'], ['new_profile_sha256'], '0' * 64)
    elif mutation == 'prior_archive': _change(case, first['archive_key'], ['epoch'], 99)
    elif mutation == 'profile_epoch_bool':
        _change(case, case.profile_key, ['series_epoch'], True)
        current = json.loads(case.client.get(case.profile_key))
        for key in (case.pending_key, case.ns['DAILY_PREFIX'] + CHANNEL + ':2026-09-07'):
            _change(case, key, ['profile_sha256'], case.ns['_digest'](current))
    elif mutation == 'history_count_bool': _change(case, epoch_key, ['history_count'], True)
    elif mutation.startswith('prior_'):
        key = case.ns['RECEIPT_PREFIX'] + CHANNEL + ':' + first['attempt_id']
        receipt = json.loads(case.client.get(key))
        if mutation == 'prior_old_revision_missing': receipt.pop('old_profile_revision')
        elif mutation == 'prior_old_series_changed': receipt['old_series_id'] = 'different-valid-old-series'
        elif mutation == 'prior_pending_hash_changed': receipt['pending_sha256'] = '0' * 64
        _write(case.client, key, receipt)
    before = _snapshot(case)
    with pytest.raises(case.ns['SeriesPromotionError']):
        _run(case, NOW + 86410)
    assert _snapshot(case) == before


def test_prior_epoch_topic_is_not_reintroduced_with_a_different_source_url(case):
    _run(case)
    profile = json.loads(case.client.get(case.profile_key))
    _finish_series(case, profile, now=NOW + 10)
    assert _prepare(case, number=1, now=NOW + 86400)['status'] == 'ready'
    old_topic = case.profile['production_topics'][0] + ' ' + URL
    for key in (case.pending_key, case.ns['DAILY_PREFIX'] + CHANNEL + ':2026-09-07'):
        _change(case, key, ['briefs', 0, 'brief'], old_topic)
    before = _snapshot(case)
    with pytest.raises(case.ns['SeriesPromotionError'], match='series_topic_already_used'):
        _run(case, NOW + 86410)
    assert _snapshot(case) == before


def test_nonoverlapping_history_insert_before_watch_cannot_fake_first_epoch(case, monkeypatch):
    original = case.ns['_Snapshot'].compare
    def racing(snapshot, pipe):
        original(snapshot, pipe)
        case.client.sadd(case.ns['TOPIC_HISTORY_PREFIX'] + CHANNEL, 'unrelated-history-item')
    monkeypatch.setattr(case.ns['_Snapshot'], 'compare', racing)
    with pytest.raises(case.ns['SeriesPromotionError']):
        _run(case)
    assert not case.client.keys(case.ns['ARCHIVE_PREFIX'] + '*')


@pytest.mark.parametrize('missing', ['old_profile_revision', 'old_series_id', 'pending_sha256', 'archive_key',
                                   'promoted_at', 'new_profile_sha256', 'requires_full_research_and_critic'])
def test_corrupt_idempotency_receipt_is_not_reported_as_valid_success(case, missing):
    result = _run(case)
    key = case.ns['RECEIPT_PREFIX'] + CHANNEL + ':' + result['attempt_id']
    receipt = json.loads(case.client.get(key))
    receipt.pop(missing)
    _write(case.client, key, receipt)
    before = _snapshot(case)
    with pytest.raises(case.ns['SeriesPromotionError'], match='series_receipt_conflict'):
        _run(case)
    assert _snapshot(case) == before
