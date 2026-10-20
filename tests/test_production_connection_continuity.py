"""Real offline Redis CAS for one diagnostic-only legacy reconnect receipt."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import fakeredis
import pytest
from redis.exceptions import ConnectionError

from app.services import production_connection_continuity as continuity


REVISION = 'reviewed_profile_revision'
OLD = 'old_connection_bound_to_history'
NEW = 'new_connection_from_google'
CIPHER = 'private-encrypted-fixture-not-a-live-credential'
TOKEN = 'private-claim-fixture-' * 2
LEDGER = 'youtube_studio:{production_spend}:v1'


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _dump(client):
    return {key: client.dump(key) for key in client.scan_iter('*')}


@pytest.fixture
def case():
    client = fakeredis.FakeRedis(decode_responses=True)
    topics = [f'Exact original episode {index + 1}' for index in range(8)]
    profile = {'channel_id': continuity.CHANNEL_ID, 'profile_revision': REVISION,
        'production_topics': topics, 'production_enabled': True, 'auto_publish': True,
        'release_mode': 'public', 'default_language': 'en', 'route_label': 'capital',
        'channel_identity': 'Original channel identity'}
    client.set(continuity._PROFILE + continuity.CHANNEL_ID, _json(profile))
    client.set(continuity._CHANNEL + continuity.CHANNEL_ID, _json({
        'id': continuity.CHANNEL_ID, 'connection_id': NEW, 'requires_reconnect': False}))
    client.set(continuity._CREDENTIAL + continuity.CHANNEL_ID, CIPHER)
    client.set(continuity._AUTH_EPOCH, '12')
    client.sadd(continuity._CHANNEL_INDEX, continuity.CHANNEL_ID)
    client.hset(continuity._STATE + continuity.CHANNEL_ID, mapping={
        'connection_id': OLD, 'profile_revision': REVISION, 'cursor': '5',
        'last_task_id': continuity.ROOT_ID, 'last_result': 'FAILURE', 'dispatch_status': 'finished',
        'paused_reason': 'previous_render_failed', 'next_due': '1788940800',
        'consumed_prefix': _hash(json.dumps(topics[:5], ensure_ascii=False))})
    audio = {'version': 1, 'status': 'unapproved_candidate', 'qa_approved': False,
        'requires_full_qa': True, 'audio_sha256': 'a' * 64, 'metadata_sha256': 'b' * 64,
        'package_sha256': 'c' * 64, 'size': 2048,
        'audio_key': f'audio_candidates/{continuity.LEAF_ID}/' + 'a' * 64 + '/candidate.mp3',
        'metadata_key': f'audio_candidates/{continuity.LEAF_ID}/' + 'a' * 64 + '/metadata-' + 'b' * 64 + '.json'}
    entries = []
    for index in range(6):
        raw_sha, manifest_sha = _hash(f'actual-raw-{index}'), _hash(f'original-manifest-{index}')
        entries.append({**continuity._CANDIDATE_FLAGS, 'source_task_id': continuity.LEAF_ID,
            'status': 'preserved_candidate', 'scene_index': index, 'phase': 'initial_generation',
            'provider': 'runway', 'raw_key': f'generated_candidates/{continuity.LEAF_ID}/raw/{raw_sha}.mp4',
            'raw_sha256': raw_sha, 'raw_size': 4096, 'audio_sha256': 'a' * 64,
            'package_sha256': 'd' * 64,
            'manifest_key': f'generated_candidates/{continuity.LEAF_ID}/manifests/{manifest_sha}.json',
            'manifest_sha256': manifest_sha, 'manifest_size': 4096})
    journal = {**continuity._CANDIDATE_FLAGS, 'source_task_id': continuity.LEAF_ID,
        'status': 'candidate_journal', 'attempted_count': 6, 'preserved_count': 6, 'failed_count': 0,
        'entries': [entries[index] for index in (2, 5, 0, 3, 4, 1)]}
    spec = {'topic': topics[4] + '\n\nChannel editorial direction: Original channel identity',
        'channel_id': 'capital', 'mode': 'production', 'format': 'shorts', 'duration_minutes': .5,
        'language': 'en', 'music': 'off', 'subtitles': False, 'quality_threshold': 80,
        'production_channel_id': continuity.CHANNEL_ID, 'production_connection_id': OLD,
        'production_profile_revision': REVISION, 'production_topic_index': 4,
        'production_scheduled': True, 'publish_after_render': True, 'workflow': 'auto',
        'voice_name': 'Original liked voice', 'unrelated_future_field': {'preserved': ['all', 'values']}}
    for index, task in enumerate(continuity.LINEAGE):
        job = {'task_id': task, 'kind': 'render', 'state': 'FAILURE', 'spec': deepcopy(spec),
            'parent_id': continuity.LINEAGE[index - 1] if index else None,
            'retry_child_task_id': continuity.LINEAGE[index + 1] if index < 2 else None,
            'failure_stage': ('audio_qc_retry', 'audio_qc', 'final_visual_qc_rescue')[index],
            'retry_claimed': index < 2, 'repair_claimed': False, 'repair_available': False,
            'paid_create_slots_used': 6 if index == 2 else 0, 'preview_total_paid_create_cap': 6,
            'updated_at': '2026-09-09T01:46:00Z'}
        if index < 2:
            job['retry_dispatch_state'] = 'dispatched'
            child = continuity.LINEAGE[index + 1]
            token = TOKEN + str(index)
            client.hset(continuity._DISPATCH + task, mapping={
                'token': token, 'child_task_id': child, 'mode': 'full', 'state': 'dispatched'})
            client.hset(continuity._CHILD_CLAIM + child, mapping={'source_task_id': task, 'token': token})
            client.set(continuity._EXECUTION + child, token)
        else:
            job['audio_candidate_checkpoint'] = audio
            job['generated_asset_candidates'] = journal
        client.set(continuity._JOB + task, _json(job))
        client.hset(continuity._PAID_CAP + task, mapping={'cap': '6', 'used': str(6 if index == 2 else 0)})
    # Existing unrelated financial/deletion history must survive byte-for-byte.
    client.hset(LEDGER, mapping={'original_financial_evidence': 'do-not-reset'})
    client.hset('youtube_studio:visibility:old-history', 'old-video', 'owner-api-absent')
    return SimpleNamespace(client=client, profile=profile, spec=spec,
                           key=continuity.CONTINUITY_PREFIX + continuity.ROOT_ID)


def _call(case, stage=False, **kwargs):
    args = {'original_task_id': continuity.ROOT_ID, 'leaf_task_id': continuity.LEAF_ID,
            'expected_channel_id': continuity.CHANNEL_ID, 'expected_profile_revision': REVISION,
            'client': case.client, **kwargs}
    fn = continuity.stage_connection_continuity if stage else continuity.prepare_connection_continuity
    return fn(**args)


def _job(case, task=continuity.LEAF_ID, mutate=lambda job: None):
    value = json.loads(case.client.get(continuity._JOB + task))
    mutate(value)
    case.client.set(continuity._JOB + task, _json(value))


def test_prepare_is_read_only_preserves_full_spec_and_distinct_available_hashes(case):
    before = _dump(case.client)
    result = _call(case)
    assert _dump(case.client) == before and result['status'] == 'prepared'
    record = result['receipt']
    assert record['old_connection_id'] == OLD and record['current_connection_id'] == NEW
    assert record['credential_cipher_sha256'] == _hash(CIPHER) and record['authorization_epoch'] == '12'
    assert record['lineage'][0]['spec'] == case.spec
    candidate = record['legacy_candidates']
    assert candidate['original_full_package_sha256'] == 'd' * 64
    assert candidate['audio_candidate_package_sha256'] == 'c' * 64
    assert [row['scene_index'] for row in candidate['generated_pointers']] == list(range(6))
    assert candidate['media_bytes_verified'] is False and candidate['selected_edit_identity_verified'] is False
    assert candidate['manifest_content_verified'] is False
    assert 'candidate_package_sha256' not in candidate
    assert all('candidate_package_sha256' not in row for row in candidate['generated_pointers'])
    assert 'selected_visual_checkpoint' not in record and 'approved_package' not in record
    assert record['fresh_google_identity_verified'] is False
    assert record['diagnostic_only'] is True and record['runnable'] is False and record['qa_approved'] is False
    assert record['requires_new_qa'] is True and record['requires_funding'] is True
    assert CIPHER not in _json(record) and TOKEN not in _json(record)
    assert result['receipt_sha256'] == _hash(_json(record))


def test_stage_creates_only_one_permanent_canonical_receipt_without_claim_or_money(case):
    before = _dump(case.client)
    first = _call(case, stage=True)
    after = _dump(case.client)
    assert set(after) - set(before) == {case.key}
    assert {key: value for key, value in after.items() if key != case.key} == before
    assert case.client.get(case.key) == _json(first['receipt']) and case.client.ttl(case.key) == -1
    first['receipt']['lineage'][0]['spec']['topic'] = 'mutated private caller copy'
    second = _call(case, stage=True)
    assert second['status'] == 'already_staged'
    assert second['receipt']['lineage'][0]['spec']['topic'] == case.spec['topic']
    assert first['receipt_sha256'] == second['receipt_sha256'] and _dump(case.client) == after
    assert _call(case)['status'] == 'prepared' and _dump(case.client) == after


@pytest.mark.parametrize('argument,value', [('original_task_id', continuity.MIDDLE_ID),
    ('leaf_task_id', continuity.MIDDLE_ID), ('expected_channel_id', 'UCgvESYtYbn2w9R2ExBOF_cw'),
    ('expected_profile_revision', 'wrong_revision')])
def test_only_exact_capital_scope_and_revision_are_admitted(case, argument, value):
    before = _dump(case.client)
    with pytest.raises(continuity.ConnectionContinuityError):
        _call(case, stage=True, **{argument: value})
    assert _dump(case.client) == before


@pytest.mark.parametrize('item', ['channel', 'credential', 'epoch', 'membership', 'profile',
                                 'job', 'dispatch', 'claim', 'execution', 'paid_counter'])
def test_missing_authoritative_record_is_never_reconstructed(case, item):
    keys = {'channel':continuity._CHANNEL + continuity.CHANNEL_ID,
        'credential':continuity._CREDENTIAL + continuity.CHANNEL_ID, 'epoch':continuity._AUTH_EPOCH,
        'membership':continuity._CHANNEL_INDEX, 'profile':continuity._PROFILE + continuity.CHANNEL_ID,
        'job':continuity._JOB + continuity.MIDDLE_ID, 'dispatch':continuity._DISPATCH + continuity.ROOT_ID,
        'claim':continuity._CHILD_CLAIM + continuity.LEAF_ID, 'execution':continuity._EXECUTION + continuity.LEAF_ID,
        'paid_counter':continuity._PAID_CAP + continuity.ROOT_ID}
    case.client.delete(keys[item])
    before = _dump(case.client)
    with pytest.raises(continuity.ConnectionContinuityError):
        _call(case, stage=True)
    assert _dump(case.client) == before


@pytest.mark.parametrize('field,value', [('cursor','6'), ('paused_reason','owner_pause'),
    ('last_task_id',continuity.LEAF_ID), ('last_result','SUCCESS'), ('active_task_id',continuity.LEAF_ID),
    ('dispatch_status','reserved'), ('profile_revision','different_revision'),
    ('connection_id',NEW), ('consumed_prefix','0'*64), ('next_due','NaN')])
def test_changed_cursor_or_schedule_does_not_stage(case, field, value):
    case.client.hset(continuity._STATE + continuity.CHANNEL_ID, field, value)
    before = _dump(case.client)
    with pytest.raises(continuity.ConnectionContinuityError):
        _call(case, stage=True)
    assert _dump(case.client) == before


@pytest.mark.parametrize('patch', [{'id':'UCgvESYtYbn2w9R2ExBOF_cw'}, {'connection_id':OLD},
                                  {'requires_reconnect':True}, {'connection_id':'bad'}])
def test_other_channel_or_unreconnected_authority_is_rejected(case, patch):
    key = continuity._CHANNEL + continuity.CHANNEL_ID
    value = json.loads(case.client.get(key)); value.update(patch)
    case.client.set(key,_json(value))
    with pytest.raises(continuity.ConnectionContinuityError):
        _call(case, stage=True)
    assert not case.client.exists(case.key)


@pytest.mark.parametrize('patch', [{'production_enabled':False}, {'auto_publish':False},
    {'release_mode':'private'}, {'production_topics':['replacement']*8},
    {'production_topics':['fewer']*7}, {'channel_identity':'Different identity'}, {'route_label':'other'},
    {'default_language':'tr'}])
def test_profile_order_and_owner_disable_fences_are_preserved(case, patch):
    profile = deepcopy(case.profile); profile.update(patch)
    case.client.set(continuity._PROFILE+continuity.CHANNEL_ID,_json(profile))
    before = _dump(case.client)
    with pytest.raises(continuity.ConnectionContinuityError):
        _call(case, stage=True)
    assert _dump(case.client) == before


@pytest.mark.parametrize('field,value', [('youtube_channel_id','UCgvESYtYbn2w9R2ExBOF_cw'),
    ('youtube_connection_id',NEW), ('production_topic_index',5), ('duration_minutes',1),
    ('music','automatic'), ('publish_after_render',False), ('quality_threshold',1),
    ('repair_source_task_id',continuity.ROOT_ID), ('api_key','private-not-allowed')])
def test_alias_and_frozen_spec_conflicts_block_without_exposing_values(case, field, value):
    _job(case, mutate=lambda job:job['spec'].update({field:value}))
    before = _dump(case.client)
    with pytest.raises(continuity.ConnectionContinuityError) as error:
        _call(case, stage=True)
    assert str(value) not in str(error.value)
    assert _dump(case.client) == before


@pytest.mark.parametrize('prefix', continuity._ABSENT_PREFIXES + (continuity._REPAIR_CLAIM,))
def test_all_existing_owner_upload_or_checkpoint_fences_block_even_empty_values(case, prefix):
    case.client.set(prefix + continuity.MIDDLE_ID, '')
    before = _dump(case.client)
    with pytest.raises(continuity.ConnectionContinuityError):
        _call(case, stage=True)
    assert _dump(case.client) == before


@pytest.mark.parametrize('change', ['owner_field','cancel_field','new_child','state','kind','final_output','selected_empty','selected_v1','claimed_leaf'])
def test_changed_source_does_not_gain_diagnostic_transition(case, change):
    patches = {'owner_field':{'publication_hold':None}, 'cancel_field':{'owner_cancellation':False},
        'new_child':{'retry_child_task_id':continuity.ROOT_ID}, 'state':{'state':'PROGRESS'}, 'kind':{'kind':'plan'},
        'final_output':{'result':{'video_key':'videos/other/final.mp4'}},
        'selected_empty':{'selected_visual_checkpoint':{}}, 'selected_v1':{'selected_visual_checkpoint':{'version':1}},
        'claimed_leaf':{'retry_claimed':True}}
    _job(case, mutate=lambda job:job.update(patches[change]))
    with pytest.raises(continuity.ConnectionContinuityError):
        _call(case, stage=True)
    assert not case.client.exists(case.key)


@pytest.mark.parametrize('change', ['missing','wrong_count','wrong_phase','duplicate','audio_mismatch','package_mismatch',
    'untrusted_key','manifest_key','unknown_provider','bad_hash','metadata_missing','audio_path','extra_pointer_field'])
def test_original_journal_and_voice_metadata_are_bound_without_fetching_blobs(case, change):
    def mutate(job):
        journal=job['generated_asset_candidates']; row=journal['entries'][0]
        if change=='missing':job.pop('generated_asset_candidates')
        elif change=='wrong_count':journal['preserved_count']=5
        elif change=='wrong_phase':row['phase']='final_repair'
        elif change=='duplicate':journal['entries'][1]=deepcopy(row)
        elif change=='audio_mismatch':row['audio_sha256']='f'*64
        elif change=='package_mismatch':row['package_sha256']='f'*64
        elif change=='untrusted_key':row['raw_key']='https://private.invalid/file'
        elif change=='manifest_key':row['manifest_key']=row['manifest_key'].replace(continuity.LEAF_ID,continuity.ROOT_ID)
        elif change=='unknown_provider':row['provider']='unknown'
        elif change=='bad_hash':row['raw_sha256']='not-a-hash'
        elif change=='metadata_missing':job['audio_candidate_checkpoint'].pop('metadata_sha256')
        elif change=='audio_path':job['audio_candidate_checkpoint']['audio_key']='../other'
        else:row['candidate_package_sha256']='f'*64
    _job(case, mutate=mutate)
    before=_dump(case.client)
    with pytest.raises(continuity.ConnectionContinuityError):
        _call(case,stage=True)
    assert _dump(case.client)==before


@pytest.mark.parametrize('key,kind', [(continuity._ACTIVE,'string'), ('celery','list'),
    ('celery'+chr(6)+chr(22)+'3','list'),('unacked','hash'),('unacked_index','zset')])
def test_idle_requires_no_existing_claim_or_broker_work(case,key,kind):
    if kind=='string':case.client.set(key,'{}')
    elif kind=='list':case.client.rpush(key,'pending')
    elif kind=='hash':case.client.hset(key,'one','pending')
    else:case.client.zadd(key,{'pending':1})
    with pytest.raises(continuity.ConnectionContinuityError):_call(case,stage=True)
    assert not case.client.exists(case.key)


def _pipeline_hook(case, monkeypatch, callback):
    original = case.client.pipeline
    def pipeline(*args,**kwargs):
        pipe=original(*args,**kwargs)
        execute=pipe.execute
        def run(*a,**kw):return callback(pipe,execute,a,kw)
        monkeypatch.setattr(pipe,'execute',run)
        return pipe
    monkeypatch.setattr(case.client,'pipeline',pipeline)


@pytest.mark.parametrize('change',['hold','cancellation','membership','epoch','credential','new_child','active','queue'])
def test_actual_watch_cas_rejects_owner_or_authorization_race_before_stage(case,monkeypatch,change):
    injected=False
    def callback(pipe,execute,args,kwargs):
        nonlocal injected
        if not injected:
            injected=True
            if change=='hold':case.client.set(continuity._ABSENT_PREFIXES[0]+continuity.ROOT_ID,'owner-held')
            elif change=='cancellation':case.client.set(continuity._ABSENT_PREFIXES[1]+continuity.LEAF_ID,'cancelled')
            elif change=='membership':case.client.srem(continuity._CHANNEL_INDEX,continuity.CHANNEL_ID)
            elif change=='epoch':case.client.set(continuity._AUTH_EPOCH,'13')
            elif change=='credential':case.client.set(continuity._CREDENTIAL+continuity.CHANNEL_ID,'rotated-cipher')
            elif change=='new_child':_job(case,mutate=lambda job:job.update(retry_child_task_id=continuity.ROOT_ID))
            elif change=='active':case.client.set(continuity._ACTIVE,'{}')
            else:case.client.rpush('celery','new-work')
        return execute(*args,**kwargs)
    _pipeline_hook(case,monkeypatch,callback)
    with pytest.raises(continuity.ConnectionContinuityError):_call(case,stage=True)
    assert not case.client.exists(case.key)


@pytest.mark.parametrize('change',['epoch','credential','new_connection','source_hash'])
def test_existing_receipt_cannot_be_replaced_by_later_authority_or_source(case,change):
    _call(case,stage=True)
    original=case.client.get(case.key)
    if change=='epoch':case.client.set(continuity._AUTH_EPOCH,'13')
    elif change=='credential':case.client.set(continuity._CREDENTIAL+continuity.CHANNEL_ID,'rotated-cipher')
    elif change=='new_connection':case.client.set(continuity._CHANNEL+continuity.CHANNEL_ID,_json({'id':continuity.CHANNEL_ID,'connection_id':'third_connection'}))
    else:_job(case,mutate=lambda job:job.update(updated_at='2026-09-09T14:00:00Z'))
    with pytest.raises(continuity.ConnectionContinuityError,match='continuity_receipt_conflict'):
        _call(case,stage=True)
    assert case.client.get(case.key)==original


@pytest.mark.parametrize('commit',['yes','no','changed_after_commit'])
def test_lost_exec_reply_never_issues_a_second_set_or_recovery_claim(case,monkeypatch,commit):
    writes=0
    def callback(pipe,execute,args,kwargs):
        nonlocal writes
        is_set=any(command[0][0]=='SET' for command in pipe.command_stack)
        if is_set:
            writes+=1
            assert writes==1
            if commit!='no':execute(*args,**kwargs)
            if commit=='changed_after_commit':case.client.set(continuity._AUTH_EPOCH,'13')
            raise ConnectionError('private transport failure')
        return execute(*args,**kwargs)
    _pipeline_hook(case,monkeypatch,callback)
    if commit=='yes':
        assert _call(case,stage=True)['status']=='already_staged'
    else:
        with pytest.raises(continuity.ConnectionContinuityError,match='^continuity_write_uncertain$'):
            _call(case,stage=True)
    assert writes==1
    assert bool(case.client.exists(case.key)) is (commit!='no')
    assert not case.client.exists(continuity._DISPATCH+continuity.LEAF_ID)


def test_two_independent_clients_race_to_the_same_exact_create_only_record(case,monkeypatch):
    other=fakeredis.FakeRedis(connection_pool=case.client.connection_pool)
    raced=False
    def callback(pipe,execute,args,kwargs):
        nonlocal raced
        if not raced:
            raced=True
            result=_call(case,stage=True,client=other)
            assert result['status']=='staged'
        return execute(*args,**kwargs)
    _pipeline_hook(case,monkeypatch,callback)
    result=_call(case,stage=True)
    assert result['status']=='already_staged'
    assert case.client.get(case.key)==_json(result['receipt'])


def test_membership_integer_one_and_read_only_prepare_watch_race(case,monkeypatch):
    original=case.client.pipeline
    def pipeline(*a,**kw):
        pipe=original(*a,**kw)
        member=pipe.sismember
        monkeypatch.setattr(pipe,'sismember',lambda *args:int(member(*args)))
        return pipe
    monkeypatch.setattr(case.client,'pipeline',pipeline)
    assert _call(case)['status']=='prepared'
    mutated=False
    def callback(pipe,execute,args,kwargs):
        nonlocal mutated
        if not mutated:
            mutated=True
            case.client.srem(continuity._CHANNEL_INDEX,continuity.CHANNEL_ID)
        return execute(*args,**kwargs)
    _pipeline_hook(case,monkeypatch,callback)
    with pytest.raises(continuity.ConnectionContinuityError):_call(case)
    assert not case.client.exists(case.key)


def test_bad_existing_receipt_and_wrong_key_type_are_not_deleted_or_overwritten(case):
    case.client.set(case.key,'{}')
    before=_dump(case.client)
    with pytest.raises(continuity.ConnectionContinuityError,match='continuity_receipt_conflict'):_call(case,stage=True)
    assert _dump(case.client)==before
    case.client.delete(case.key)
    case.client.hset(case.key,'wrong','type')
    before=_dump(case.client)
    with pytest.raises(continuity.ConnectionContinuityError):_call(case,stage=True)
    assert _dump(case.client)==before


def test_no_financial_state_is_required_or_initialized_to_stage_private_evidence(case):
    case.client.delete(LEDGER)
    assert _call(case,stage=True)['receipt']['requires_funding'] is True
    assert not case.client.exists(LEDGER)


@pytest.mark.parametrize('stage',[False,True])
def test_existing_receipt_with_ttl_is_not_accepted_or_silently_made_permanent(case,stage):
    _call(case,stage=True)
    original=case.client.get(case.key)
    case.client.pexpire(case.key,60000)
    with pytest.raises(continuity.ConnectionContinuityError,match='^continuity_receipt_not_durable$'):
        _call(case,stage=stage)
    assert case.client.get(case.key)==original
    assert 0 < case.client.pttl(case.key) <= 60000


def test_lost_write_reply_with_expiring_receipt_cannot_confirm_durable_success(case,monkeypatch):
    writes=0
    def callback(pipe,execute,args,kwargs):
        nonlocal writes
        if any(command[0][0]=='SET' for command in pipe.command_stack):
            writes+=1
            assert writes==1
            execute(*args,**kwargs)
            case.client.pexpire(case.key,60000)
            raise ConnectionError('private lost acknowledgement')
        return execute(*args,**kwargs)
    _pipeline_hook(case,monkeypatch,callback)
    with pytest.raises(continuity.ConnectionContinuityError,match='^continuity_write_uncertain$'):
        _call(case,stage=True)
    assert writes==1 and 0 < case.client.pttl(case.key) <= 60000
    assert not case.client.exists(continuity._DISPATCH+continuity.LEAF_ID)
