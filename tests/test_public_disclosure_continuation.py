"""Public disclosure may increase; no publication or uncertainty is rewritten."""
import json
from unittest.mock import Mock

import pytest

from test_production_continuous_public import case, CHANNEL, CONNECTION, _completed_replay
from test_channel_production import production


def edit(client, key, function):
    value=json.loads(client.get(key));function(value);client.set(key,json.dumps(value))


def upgrade(case):
    module,client,profile,first,keys=case
    edit(client,keys['ledger'],lambda row:row['publish_plan'].update(contains_synthetic_media=False))
    return module,client,profile,first,keys


def pause(case):
    module,client,profile,first,keys=case
    assert module.reconcile_active_production(now=2000)=='completed'
    state=module.CHANNEL_STATE_PREFIX+CHANNEL
    client.hset(state,mapping={'paused_reason':'previous_publication_blocked','next_due':'87400'})
    client.hdel(state,'last_public_task_id','last_public_continued_at')
    return state


def test_stronger_committed_disclosure_continues_without_rewriting_receipts(case):
    module,client,profile,first,keys=upgrade(case)
    before={k:client.get(v)for k,v in keys.items()}
    assert module.reconcile_active_production(now=2000)=='completed'
    assert module.get_production_state(CHANNEL)['last_public_task_id']==first['task_id']
    assert {k:client.get(v)for k,v in keys.items()}==before


@pytest.mark.parametrize('recorded', ['omitted', True, False])
def test_completed_upload_replay_uses_actual_disclosure(case, recorded):
    module,client,profile,first,keys=case
    state=pause(case);upgrade(case)
    replay=_completed_replay(case)
    if recorded != 'omitted':
        replay['result']['contains_synthetic_media']=recorded
        client.set(keys['publisher'],json.dumps(replay))
    before={k:client.get(v)for k,v in keys.items()}
    result=module.reconcile_publication_holds([profile],now=2100)
    assert result=={CHANNEL:'publication_still_unverified' if recorded is False else 'public_hold_cleared'}
    assert bool(client.hget(state,'paused_reason')) is (recorded is False)
    assert {k:client.get(v)for k,v in keys.items()}==before


def test_finished_public_hold_rechecks_full_proof_then_normal_dispatches_once(case):
    module,client,profile,first,keys=case
    state=pause(case);upgrade(case)
    before={k:client.get(v)for k,v in keys.items()}
    assert module.reconcile_publication_holds([profile],now=2100)=={CHANNEL:'public_hold_cleared'}
    current=client.hgetall(state)
    assert not current.get('paused_reason') and current['next_due']=='2100'
    assert current['last_public_task_id']==first['task_id'] and current['cursor']=='1'
    assert client.get(module.ACTIVE_KEY) is None
    assert {k:client.get(v)for k,v in keys.items()}==before
    assert module.reconcile_publication_holds([profile],now=2101)=={}
    enqueue=Mock()
    result=module.dispatch_due_productions([profile],[CONNECTION],enqueue,now=2102)
    assert result['status']=='queued' and result['task_id']!=first['task_id']
    enqueue.assert_called_once()
    assert module.reconcile_publication_holds([profile],now=2103)=={}
    assert {k:client.get(v)for k,v in keys.items()}==before


@pytest.mark.parametrize('damage', ['child_false','child_null','attribution_false','plan_null',
    'child_failed','upload_uncertain','wrong_connection','changed_revision','caption_missing',
    'thumbnail_missing','schedule_changed','source_failed','private','owner_disabled'])
def test_recheck_cannot_release_inconsistent_or_nonpublic_records(case,damage):
    module,client,profile,first,keys=case
    state=pause(case)
    if damage=='child_false':edit(client,keys['publisher'],lambda r:r['result'].update(contains_synthetic_media=False))
    elif damage=='child_null':edit(client,keys['publisher'],lambda r:r['result'].update(contains_synthetic_media=None))
    elif damage=='attribution_false':edit(client,keys['source'],lambda r:r['result']['youtube'].update(contains_synthetic_media=False))
    elif damage=='plan_null':edit(client,keys['ledger'],lambda r:r['publish_plan'].update(contains_synthetic_media=None))
    elif damage=='child_failed':edit(client,keys['publisher'],lambda r:r.update(state='FAILURE'))
    elif damage=='upload_uncertain':edit(client,keys['ledger'],lambda r:r.update(status='uncertain'))
    elif damage=='wrong_connection':edit(client,keys['channel'],lambda r:r.update(connection_id='changed'))
    elif damage=='changed_revision':edit(client,keys['profile'],lambda r:r.update(profile_revision='changed'))
    elif damage=='caption_missing':edit(client,keys['source'],lambda r:r['result']['youtube'].update(caption_uploaded=False))
    elif damage=='thumbnail_missing':edit(client,keys['source'],lambda r:r['result']['youtube'].update(thumbnail_uploaded=False))
    elif damage=='schedule_changed':client.hset(state,'last_task_id','00000000-0000-4000-8000-000000000000')
    elif damage=='source_failed':edit(client,keys['source'],lambda r:r.update(state='FAILURE'))
    elif damage=='private':edit(client,keys['publisher'],lambda r:r['result'].update(release_status='private',privacy_status='private'))
    elif damage=='owner_disabled':edit(client,keys['profile'],lambda r:r.update(production_enabled=False))
    before_state=client.hgetall(state);before={k:client.get(v)for k,v in keys.items()}
    module.reconcile_publication_holds([profile],now=2100)
    assert client.hgetall(state)==before_state and client.get(module.ACTIVE_KEY) is None
    assert {k:client.get(v)for k,v in keys.items()}==before


@pytest.mark.parametrize('reason', ['operator_review_required','previous_render_failed','owner_paused'])
def test_other_pause_reasons_are_never_cleared(case,reason):
    module,client,profile,first,keys=case;state=pause(case)
    client.hset(state,'paused_reason',reason);before=client.hgetall(state)
    assert module.reconcile_publication_holds([profile],now=2100)=={}
    assert client.hgetall(state)==before


def test_sibling_active_claim_survives_public_hold_recheck(case):
    module,client,profile,first,keys=case;state=pause(case);upgrade(case)
    sibling={'channel_id':'UC_other_channel','task_id':'22222222-2222-4222-8222-222222222222'}
    raw=json.dumps({'version':2,'claims':[sibling]});client.set(module.ACTIVE_KEY,raw)
    assert module.reconcile_publication_holds([profile],now=2100)=={CHANNEL:'public_hold_cleared'}
    assert client.get(module.ACTIVE_KEY)==raw


def test_same_channel_active_claim_never_loses_ownership(case):
    module,client,profile,first,keys=case;state=pause(case)
    raw=json.dumps({'channel_id':CHANNEL,'task_id':'22222222-2222-4222-8222-222222222222'})
    client.set(module.ACTIVE_KEY,raw);before=client.hgetall(state)
    assert module.reconcile_publication_holds([profile],now=2100)=={CHANNEL:'active'}
    assert client.hgetall(state)==before and client.get(module.ACTIVE_KEY)==raw
