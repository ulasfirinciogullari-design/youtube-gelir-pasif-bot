from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.services import content_plan as plan, content_plan_recovery as recovery
from app.services import content_plan_stock_repair as repair, render, storage, visual_qc
from test_content_plan_recovery import case, failed
from test_generated_asset_checkpoint import case as asset_case, preserve, MP4
from test_recovered_media_v3 import boundary


def test_final_visual_rejection_can_reselect_saved_stock_but_cannot_skip_publication(case):
    source = failed(case)
    source.update(failure_stage='final_visual_qc_rescue',
        failure_classification={'category':'content_rejected', 'code':'visual_quality_exhausted'},
        paid_create_slots_used=6,
        generated_asset_candidates={'attempted_count':6,'preserved_count':1,'failed_count':5})
    case.client.set(plan.jobs.JOB_PREFIX + source['task_id'], plan._raw(source))
    assert recovery.eligible(source)
    assert recovery._source(case.client, source['task_id']) == source
    enqueue = Mock(); normal = Mock()
    plan.maintain([case.profile], normal, repair_enqueue=enqueue)
    enqueue.assert_called_once(); normal.assert_not_called()
    assert not case.client.exists(plan.COMPLETION_PREFIX + case.a['id'])


@pytest.mark.parametrize('damage', [None, 'unknown', 'critic_rejected', 'ready_record'])
def test_v2_upgrade_requires_exact_observed_integrity_error_before_child_dispatch(case, damage):
    source = failed(case); task = source['task_id']; op = str(uuid4()); prefix=recovery.LEGACY_PREFIX+'v2:'
    case.client.set(prefix+'dispatch:'+task, plan._raw({'task_id':op,'source_sha256':recovery._fingerprint(source)}))
    case.client.set(prefix+'execution:'+task, op)
    case.client.set(prefix+'status:'+task, plan._raw({'state':'stopped','error_type':'RuntimeError'}))
    if damage != 'unknown':
        case.client.set('celery-task-meta-'+op, plan._raw({'status':'FAILURE','result':{
            'exc_type':'RuntimeError', 'exc_message':['Critic rejected the content' if damage=='critic_rejected'
                else 'Fresh immutable story approval failed its final integrity check']},
            'traceback':'File "/app/app/services/director.py", line 3376, in revalidate_immutable_short_story'}))
    if damage == 'ready_record': case.client.set(prefix+'record:'+task, 'private record')
    before={k:case.client.dump(k)for k in case.client.scan_iter()}
    enqueue=Mock(); recovery.schedule(source,enqueue)
    assert enqueue.call_count == (1 if damage is None else 0)
    assert all(case.client.dump(k)==v for k,v in before.items())


def test_stock_only_contract_cannot_purchase_any_scene(boundary):
    raw={'version':7,'recovery_only':True,'source_task_id':str(uuid4()),'package_sha256':'a'*64,'scenes':{}}
    media=boundary['_validated_recovered_generated_media'](raw,6,'a'*64)
    assert media['version']==3 and media['scenes']=={}
    boundary['_require_recovered_media_coverage'](media,[])
    with pytest.raises(Exception): boundary['_require_recovered_media_coverage'](media,[3])
    with pytest.raises(plan.ContentPlanError): repair.validate_media({**raw,'scenes':{'3':['new']}},6,'a'*64)
    with pytest.raises(plan.ContentPlanError): repair.validate_media(raw,6,'b'*64)


def test_later_correction_filename_preserves_its_own_raw_asset(asset_case):
    first=preserve(asset_case)
    path=asset_case.work/'runway_repair_r2_s00.mp4';path.write_bytes(MP4+b'second distinct correction')
    second=preserve(asset_case,phase='final_repair',visual_spec={**asset_case.args['visual_spec'],'path':str(path)})
    assert first['raw_sha256']!=second['raw_sha256'] and first['audio_sha256']==second['audio_sha256']
    assert first['raw_key'] in asset_case.client.objects and second['raw_key'] in asset_case.client.objects


@pytest.mark.parametrize('reject_all',[False,True])
def test_exact_stock_reselection_preserves_good_shots_and_never_promotes_negative_reviews(monkeypatch,tmp_path,reject_all):
    scenes=[{'narration':f'One exact banknote action {i}.','transition':'cut'}for i in range(6)]
    package={'scenes':scenes,'sources':[]};voice={'path':str(tmp_path/'voice.mp3'),'scene_durations':[5.]*6}
    saved={}
    for phase,offset in [('budget_rescue',0),('before_generation',3)]:
        saved[phase]={'credits':[],'pools':[[{'key':f'{i}-{j+offset}','sha256':f'{i*10+j+offset+1:064x}',
            'size':2048,'spec':{'pexels_id':1000+i*10+j+offset,'source_type':'stock','start_fraction':.5,
                'source_duration':10.}} for j in range(3)] for i in range(6)]}
    original=deepcopy((package,voice,saved));calls=[]
    def stored(client,key,sha,size,destination,maximum):destination.write_text(key);return destination
    def normalize(spec,target,duration,index,transition,size):
        assert duration==5 and size=='1080x1920' and spec['preserve_start_fraction'] is True
        target.write_bytes(Path(spec['path']).read_bytes())
    def review(scenes,inputs,*args,**kwargs):
        assert len(inputs)==6 and all(v['start_fraction']==0 for row in inputs for v in row)
        calls.append(deepcopy(inputs))
        return {'reviews':[{'scene_index':i,'best_candidate_index':0,
            'score':25 if reject_all or len(calls)==1 and i==3 else 92,
            'evidence_gate_passed':not reject_all,'identity_gate_passed':True,'editorial_gate_passed':True}
            for i in range(6)],'missing_review_indices':[],'unreviewable_scene_indices':[]}
    monkeypatch.setattr(recovery,'_stored',stored);monkeypatch.setattr(storage,'_client',lambda:object())
    monkeypatch.setattr(render,'media_duration',lambda _:30.)
    monkeypatch.setattr(render,'_scene_timeline',lambda scenes,pools,*args:[(pool[0],5.,'cut',i)for i,pool in enumerate(pools)])
    monkeypatch.setattr(render,'_timeline_frame_counts',lambda *args:[5*render.FPS]*6)
    monkeypatch.setattr(render,'normalize_clip',normalize);monkeypatch.setattr(visual_qc,'review_scene_visuals',review)
    spec={'quality_threshold':86,'topic':'Banknote','content_style':'documentary'}
    if reject_all:
        with pytest.raises(RuntimeError,match='did not pass'):repair.select(package,voice,saved,tmp_path,spec)
        assert len(calls)==3
    else:
        selected,credits,evidence=repair.select(package,voice,saved,tmp_path,spec)
        assert len(calls)==2 and all(len(calls[1][i])==1 for i in (0,1,2,4,5))
        assert selected['3']['key']=='3-3' and len(evidence['observations'])==2
        assert selected['0']['key']=='0-0' and selected['0']['spec']['start_fraction']==.5
    assert (package,voice,saved)==original
