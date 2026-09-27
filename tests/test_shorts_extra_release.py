from copy import deepcopy
from datetime import datetime, timezone
import json
from uuid import uuid4

import pytest

from app.services import shorts_extra_release as extra, shorts_experiment as batch
from app.services import content_plan as plan, channel_cadence as cadence, studio_state as jobs
from app.services import youtube_auth as auth, youtube_automation as profiles
from test_shorts_experiment import setup, C, M, NOW, DAY, ordinary


def configured(setup):
    client, _, previous = setup
    entries = [plan.item('New physical puzzle '+str(n), 'Independent verified source '+str(n))for n in range(4)]
    revision = str(uuid4()); profile_revision = str(uuid4())
    client.set(plan.PLAN_PREFIX+C, plan._raw({'version':1,'channel_id':C,'revision':revision,
        'enabled':True,'after_queue':'auto_shorts','items':entries,'updated_at':entries[0]['created_at']}))
    client.set(auth.CHANNEL_PREFIX+C, plan._raw({'id':C,'connection_id':'new_connection_123'}))
    client.set(auth.CREDENTIAL_PREFIX+C,'synthetic-credential');client.sadd(auth.CHANNEL_INDEX_KEY,C)
    client.set(profiles.PROFILE_PREFIX+C,plan._raw({'channel_id':C,'profile_revision':profile_revision,
        'production_enabled':True,'auto_publish':True}))
    value={'version':1,'purpose':'owner_extra_shorts','channel_id':C,'day':DAY,
        'connection_id':'new_connection_123','profile_revision':profile_revision,
        'items':[{'item_id':row['id'],'item_sha256':plan._sha(row)}for row in entries],
        'owner_evidence_sha256':'b'*64,'authorized_at':datetime.fromtimestamp(NOW,timezone.utc).isoformat()}
    return client, entries, revision, value, previous


def admitted(client, item, grant):
    task=batch.root_id(C,item['id']);spec={'format':'shorts','production_channel_id':C,
        'content_plan_item_id':item['id'],'production_connection_id':grant['connection_id'],
        'production_profile_revision':grant['profile_revision']}
    source={'task_id':task,'parent_id':None,'spec':spec}
    with client.pipeline()as pipe:
        key=cadence.production_slot(pipe,C,'shorts',task,now=NOW,item=item)
        assert key==extra.keys(C,DAY)[2]
        pipe.multi();pipe.hset(key,task,'shorts');pipe.execute()
    client.set(jobs.JOB_PREFIX+task,plan._raw(source))
    client.set(plan.DISPATCH_PREFIX+item['id'],plan._raw({'task_id':task,'channel_id':C,'item':item,'spec_sha256':plan._sha(spec)}))
    return source


def test_four_additional_releases_preserve_original_experiment_and_tomorrow_limit(setup):
    client, entries, revision, value, previous=configured(setup)
    existing=client.hgetall(cadence.keys(C,now=NOW)[1])
    assert extra.install(client,value,expected_plan_revision=revision,now=NOW)['extra_shorts']==4
    for entry in entries:
        source=admitted(client,entry,value)
        assert cadence.publication_slot(source,client=client,now=NOW)
        assert cadence.publication_slot(source,client=client,now=NOW)
        cadence.publication_completed(source,client=client,now=NOW)
    assert client.get(batch.approval_key(DAY))==plan._raw(previous)
    assert existing.items()<=client.hgetall(cadence.keys(C,now=NOW)[1]).items()
    assert not cadence.publication_slot(ordinary(C),client=client,now=NOW)
    assert not cadence.publication_slot(ordinary(M),client=client,now=NOW)
    view=cadence.snapshot(C,client=client,now=NOW)
    assert view['extra_release']=={'date':DAY,'limit':4,'produced':4,'published':4,'pending':0}
    assert view['limits']=={'shorts':4,'long':0}
    for _ in range(4):assert cadence.publication_slot(ordinary(C),client=client,now=NOW+86400)
    assert not cadence.publication_slot(ordinary(C),client=client,now=NOW+86400)
    assert 'extra_release'not in cadence.snapshot(C,client=client,now=NOW+86400)


@pytest.mark.parametrize('change',['brief','task','format','connection','anchor'])
def test_extra_admission_is_bound_to_exact_item_and_current_channel(setup,change):
    client,entries,revision,value,_=configured(setup)
    extra.install(client,value,expected_plan_revision=revision,now=NOW)
    item=deepcopy(entries[0]);task=batch.root_id(C,item['id'])
    if change=='brief':item['brief']+=' Changed'
    if change=='task':task=str(uuid4())
    if change=='format':item['format']='long'
    if change=='connection':client.set(auth.CHANNEL_PREFIX+C,plan._raw({'id':C,'connection_id':'another_connection'}))
    if change=='anchor':client.set(extra.keys(C,DAY)[1],'x')
    with client.pipeline()as pipe,pytest.raises(ValueError):extra.production_slot(pipe,C,item,task,now=NOW)
    assert not client.exists(extra.keys(C,DAY)[2])


def test_pending_extra_is_still_counted_after_midnight(setup):
    client,entries,revision,value,_=configured(setup)
    extra.install(client,value,expected_plan_revision=revision,now=NOW)
    source=admitted(client,entries[0],value)
    assert cadence.publication_slot(source,client=client,now=NOW)
    for _ in range(3):assert cadence.publication_slot(ordinary(C),client=client,now=NOW+86400)
    assert not cadence.publication_slot(ordinary(C),client=client,now=NOW+86400)
    assert cadence.publication_slot(source,client=client,now=NOW+86400)


def test_extra_does_not_reauthorize_an_already_started_root(setup):
    client,entries,revision,value,_=configured(setup)
    client.set(jobs.JOB_PREFIX+batch.root_id(C,entries[0]['id']),'existing root')
    with pytest.raises(ValueError):extra.install(client,value,expected_plan_revision=revision,now=NOW)
    assert not client.exists(extra.keys(C,DAY)[0])


def test_extra_cannot_be_used_without_paid_production_receipt(setup):
    client,entries,revision,value,_=configured(setup)
    extra.install(client,value,expected_plan_revision=revision,now=NOW)
    source=admitted(client,entries[0],value)
    client.delete(extra.keys(C,DAY)[2])
    with pytest.raises(ValueError):cadence.publication_slot(source,client=client,now=NOW)
