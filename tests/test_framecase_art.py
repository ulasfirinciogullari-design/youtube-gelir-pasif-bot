from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
import base64
import hashlib
import json
import subprocess

import fakeredis
import httpx
import pytest

from app.services import framecase_art as art, commissioning_video as video
from app.services import production_spend_runtime as runtime, content_plan as plan
from app.services.production_spend import SpendBlocked

ROOT = 'c01c0de0-7dbe-4444-b9f1-000000000002'
REQUEST = '123e4567-e89b-12d3-a456-426614174000'


@pytest.fixture
def scope(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    config = SimpleNamespace(fal_key='test-fal-secret', studio_commissioning_video_generation=True,
                             studio_production_short_paid_create_cap=6)
    monkeypatch.setattr(art, 'settings', config)
    monkeypatch.setattr(video, 'settings', config)
    monkeypatch.setattr(video, '_authority', lambda *args: 'original-owner-authority')
    monkeypatch.setattr(art, '_verify_current_price', lambda *args: None)
    from app.services import youtube_auth
    monkeypatch.setattr(youtube_auth, '_encrypt_json', lambda v: base64.b64encode(json.dumps(v).encode()).decode())
    monkeypatch.setattr(youtube_auth, '_decrypt_json', lambda v: json.loads(base64.b64decode(v)))
    return {'foundation': SimpleNamespace(client=client),
            'context': {'lineage_id': ROOT, 'channel_id': art.CHANNEL_ID, 'kind': 'shorts'},
            'package_sha256': 'a'*64, 'scene_index': 0, 'narration_millis': 5400,
            'generation_seconds': 6, 'aspect_ratio': '9:16'}


def transport(monkeypatch, handler):
    real = httpx.Client
    monkeypatch.setattr(art.httpx, 'Client', lambda **kw: real(transport=httpx.MockTransport(handler)))


def test_approved_art_is_decodable_and_bound_to_owner_accepted_film():
    raw = art.approved_reference()
    assert len(raw) > 1000
    broken = bytearray(raw); broken[16:24] = b'\xff' * 8
    with pytest.raises(SpendBlocked): art.image_bytes(bytes(broken))
    with pytest.raises(SpendBlocked): art.image_bytes(raw[:100])


def test_image_and_motion_bodies_have_separate_fixed_quality_and_cost_inputs():
    raw = art.approved_reference(); prompt = 'Mira turns toward the door and raises her watch in a continuous drawn shot.'
    image = art.image_body(prompt, [raw], '16:9')
    assert image['num_images'] == 1 and image['resolution'] == '2K'
    assert image['enable_web_search'] is False and 'thinking_level' not in image
    motion = art.motion_body(prompt, raw, 6, '9:16')
    assert motion['auto_fix'] is False and motion['generate_audio'] is True
    assert 'safety_tolerance' not in image and 'safety_tolerance' not in motion
    assert motion['duration'] == '6s'
    with pytest.raises(SpendBlocked): art.motion_body(prompt, raw, 7, '9:16')


def test_native_images_reuse_actual_receipt_and_leave_video_history_unchanged(scope, monkeypatch):
    client = scope['foundation'].client; old = '{"original":"paid video history"}'
    client.set(video.PREFIX+ROOT, old); requests = []
    route = 'https://queue.fal.run/' + art.IMAGE_MODEL + '/requests/' + REQUEST
    def handler(request):
        requests.append(request.method)
        if request.method == 'POST':
            assert client.get(art.IMAGE_PREFIX+ROOT)  # Intent precedes native POST.
            return httpx.Response(200,json={'request_id':REQUEST,'status_url':route+'/status','response_url':route})
        if request.url.path.endswith('/status'): return httpx.Response(200,json={'status':'COMPLETED'})
        return httpx.Response(200,json={'images':[{'url':'https://v3.fal.media/files/test/scene.png'}]})
    transport(monkeypatch,handler)
    for _ in range(2):
        result, identity = art._submit(scope, art.IMAGE_MODEL, {'prompt':'one immutable image'},120000,image=True)
        assert identity == REQUEST and len(result['images'])==1
    assert requests == ['POST','GET','GET']
    assert client.get(video.PREFIX+ROOT)==old
    journal=json.loads(client.get(art.IMAGE_PREFIX+ROOT))
    assert len(journal['requests'])==1 and client.pttl(art.IMAGE_PREFIX+ROOT)==-1
    assert next(iter(journal['requests'].values()))['request']['max_list_cost_micro_usd']==120000


def test_unknown_image_create_cannot_repeat_or_switch_body(scope, monkeypatch):
    requests=[]
    def handler(request):
        requests.append(request.method); raise httpx.ReadTimeout('acceptance unknown')
    transport(monkeypatch,handler)
    with pytest.raises(SpendBlocked,match='create_outcome_unknown'):
        art._submit(scope,art.IMAGE_MODEL,{'prompt':'original'},120000,image=True)
    with pytest.raises(SpendBlocked,match='previous_outcome_unknown'):
        art._submit(scope,art.IMAGE_MODEL,{'prompt':'original'},120000,image=True)
    with pytest.raises(SpendBlocked,match='existing_request_pinned'):
        art._submit(scope,art.IMAGE_MODEL,{'prompt':'changed'},120000,image=True)
    assert requests==['POST']


def test_terminal_image_refusal_is_preserved_and_never_reposted(scope, monkeypatch):
    requests=[];route='https://queue.fal.run/'+art.IMAGE_MODEL+'/requests/'+REQUEST
    def handler(request):
        requests.append(request.method)
        if request.method=='POST':return httpx.Response(200,json={'request_id':REQUEST,'status_url':route+'/status','response_url':route})
        if request.url.path.endswith('/status'):return httpx.Response(200,json={'status':'COMPLETED'})
        return httpx.Response(422,json={'detail':[{'type':'content_policy_violation','msg':'blocked'}]})
    transport(monkeypatch,handler)
    for _ in range(2):
        with pytest.raises(art.fal.FalVideoPolicyError):
            art._submit(scope,art.IMAGE_MODEL,{'prompt':'one'},120000,image=True)
    assert requests==['POST','GET','GET']
    row=next(iter(json.loads(scope['foundation'].client.get(art.IMAGE_PREFIX+ROOT))['requests'].values()))
    assert row['result']['http_status']==422


def test_image_cap_never_uses_or_resets_motion_capacity(scope):
    for i in range(6):
        selected={**scope,'scene_index':i}
        descriptor={'package_sha256':'a'*64,'scene_index':i,'payload':i}
        art._reserve_image(selected,descriptor)
    with pytest.raises(SpendBlocked,match='episode_capacity'):
        art._reserve_image({**scope,'scene_index':6},{'package_sha256':'a'*64,'scene_index':6})
    assert not scope['foundation'].client.exists(video.PREFIX+ROOT)


@pytest.mark.parametrize('flag',['plan','hold','publish','channel'])
def test_live_owner_hold_rejects_before_native_requests(scope,monkeypatch,flag):
    from app.services import studio_state as jobs
    source={'spec':{'production_channel_id':art.CHANNEL_ID,'framecase_animation':True,'publish_after_render':True}}
    document={'enabled':True}
    if flag=='plan':document['enabled']=False
    if flag=='hold':source['publication_hold']={'owner':True}
    if flag=='publish':source['spec']['publish_after_render']=False
    if flag=='channel':scope['context']['channel_id']='another-channel'
    monkeypatch.setattr(video,'enabled_for_task',lambda:True)
    monkeypatch.setattr(jobs,'get_job',lambda _:source)
    monkeypatch.setattr(plan,'read',lambda *a,**kw:document)
    token=video._SCENE.set(scope)
    try:
        with pytest.raises(SpendBlocked):art._scope()
    finally:video._SCENE.reset(token)
    assert not list(scope['foundation'].client.scan_iter())


def test_actual_veo_duration_enum_covers_tail_without_stretching_voice(monkeypatch):
    monkeypatch.setattr(runtime,'enforcement_enabled',lambda:True)
    budget=art.scene_budget('a'*64,[3.6,5.5,7.4,5.7],'9:16',22.75)
    assert budget.generation_seconds==(4,6,8,8)
    with pytest.raises(SpendBlocked,match='scene_duration'):
        art.scene_budget('a'*64,[3.6,5.5,7.4,7.8],'9:16',24.85)


@pytest.mark.parametrize('model,unit,price',[(art.IMAGE_MODEL,'images',.08),(art.MOTION_MODEL,'seconds',.05)])
def test_fresh_native_price_is_verified_and_read_cached_without_generation(monkeypatch,model,unit,price):
    art._PRICES.clear();calls=[]
    def handler(request):
        calls.append(request)
        assert request.method=='GET' and request.url.host=='api.fal.ai'
        return httpx.Response(200,json={'prices':[{'endpoint_id':model,'unit':unit,'unit_price':price,'currency':'USD'}]})
    transport(monkeypatch,handler)
    art._verify_current_price(model,'pricing-test');art._verify_current_price(model,'pricing-test')
    assert len(calls)==1


@pytest.mark.parametrize('unit,price,currency',[('images',.081,'USD'),('images',.08,'EUR'),('tokens',.08,'USD')])
def test_price_increase_or_changed_billing_unit_never_opens_generation(monkeypatch,unit,price,currency):
    art._PRICES.clear()
    transport(monkeypatch,lambda request:httpx.Response(200,json={'prices':[{'endpoint_id':art.IMAGE_MODEL,
        'unit':unit,'unit_price':price,'currency':currency}]}))
    with pytest.raises(SpendBlocked):art._verify_current_price(art.IMAGE_MODEL,'changed-pricing-test')
    assert not art._PRICES


def test_native_ambience_keeps_real_final_frame_windows(tmp_path):
    from app.services.framecase_sound import add_native_ambience
    from app.services import render
    movie=tmp_path/'original.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=s=180x320:r=30:d=1',
        '-f','lavfi','-i','sine=frequency=440:duration=1','-c:v','libx264','-threads','1',
        '-c:a','aac','-shortest',str(movie)],check=True,capture_output=True)
    windows=[{'scene_index':0,'start_frame':0,'end_frame':30}]
    rendered={'path':str(movie),'fps':30,'frame_count':30,'duration':1.,'scene_windows':windows,'max_freeze_seconds':0.}
    def frames(path):
        return subprocess.run(['ffmpeg','-v','error','-i',str(path),'-map','0:v:0','-f','md5','-'],check=True,capture_output=True).stdout
    before=frames(movie)
    result=add_native_ambience(rendered,[[{'path':str(movie)}]],tmp_path)
    assert result['scene_windows']==windows and render.video_frame_count(result['path'])==30
    assert abs(result['duration']-1)<.06 and result['sound_design']=='native_scene_ambience_ducked_below_narration'
    assert before==frames(result['path']) and result['path']==str(movie)


@pytest.fixture
def keyframes(monkeypatch,tmp_path):
    from app.services import framecase_pipeline as pipeline
    saved={}; generated=[]; reviews=[]; editorial=[]
    scene={'narration':'The tower clock had been turned back sixty seconds.',
        'ai_prompt':'The original clock shot with physical staging described for the starting image.',
        'motion_prompt':'The minute hand moves back one notch as Mira notices the clock from below.'}
    corrected={**scene,'ai_prompt':'Mira stands on the street, naturally looking up at the intact clock tower.'}
    monkeypatch.setattr(art,'approved_reference',lambda:b'approved Mira')
    monkeypatch.setattr(art,'_scope',lambda:{'context':{'lineage_id':ROOT}})
    def generate(prompt,references,ratio,path):
        generated.append(prompt);path.write_bytes(('candidate '+str(len(generated))).encode())
        return {'provider_request_id':str(len(generated))}
    def keep(path,owner,name):
        raw=path.read_bytes();saved[name]=raw
        return {'key':name,'sha256':hashlib.sha256(raw).hexdigest()}
    def restore(asset,path):
        path.write_bytes(saved[asset['key']]);return str(path)
    def repair(*args):editorial.append(args);return deepcopy(corrected)
    monkeypatch.setattr(art,'generate_image',generate)
    monkeypatch.setattr(pipeline,'_store_asset',keep)
    monkeypatch.setattr(pipeline,'_restore_asset',restore)
    monkeypatch.setattr(art,'_repair_scene',repair)
    monkeypatch.setattr(art,'review_keyframe',lambda *args:reviews.pop(0))
    return SimpleNamespace(scene=scene,corrected=corrected,checkpoint={},story={'bible':'The original story bible.'},
        generated=generated,reviews=reviews,editorial=editorial,path=tmp_path)


def test_visible_keyframe_defect_is_repaired_once_and_old_negative_is_immutable(keyframes):
    k=keyframes
    k.reviews.extend([{'pass':False,'report':{'findings':['Disconnected hand through clock face.']}},
                      {'pass':True,'report':{'findings':[]}}])
    saved=[]
    for _ in range(2):
        art.keyframe(0,k.scene,k.story,b'cast','9:16',k.path,k.checkpoint,lambda:saved.append(deepcopy(k.checkpoint)))
    assert len(k.generated)==2 and len(k.editorial)==1 and not k.reviews
    rows=k.checkpoint['keyframes'];assert len(rows)==2
    original=next(v for v in rows.values()if v['parent_identity']is None)
    assert original['review']['pass']is False
    assert art.accepted_scene(0,k.scene,k.checkpoint)==k.corrected
    assert any(v.get('keyframe_repairs')and len(v['keyframes'])==1 for v in saved)
    assert original==saved[1]['keyframes'][original['identity']]


def test_failed_correction_cannot_buy_more_opinions_or_reset_its_allowance(keyframes):
    k=keyframes;k.reviews.extend([{'pass':False,'report':{'findings':['Bad hand.']}},
                                 {'pass':False,'report':{'findings':['Still bad hand.']}}])
    for _ in range(2):
        with pytest.raises(SpendBlocked,match='keyframe_quality_rejected'):
            art.keyframe(0,k.scene,k.story,b'cast','9:16',k.path,k.checkpoint,lambda:None)
    assert len(k.generated)==2 and len(k.editorial)==1 and not k.reviews
    assert not k.checkpoint.get('keyframe_selections')


def test_provider_unknown_or_refusal_never_enters_visual_repair(keyframes,monkeypatch):
    k=keyframes
    def failed(*args,**kwargs):raise SpendBlocked('framecase_art_create_outcome_unknown')
    monkeypatch.setattr(art,'generate_image',failed)
    with pytest.raises(SpendBlocked,match='create_outcome_unknown'):
        art.keyframe(0,k.scene,k.story,b'cast','9:16',k.path,k.checkpoint,lambda:None)
    assert not k.editorial and not k.checkpoint.get('keyframe_repairs')


def test_repaired_scene_selection_rejects_narration_changes(keyframes):
    k=keyframes;k.reviews.extend([{'pass':True,'report':{'findings':[]}}])
    art.keyframe(0,k.scene,k.story,b'cast','9:16',k.path,k.checkpoint,lambda:None)
    identity=k.checkpoint['keyframe_selections']['0']
    k.checkpoint['keyframes'][identity]['scene']['narration']='A different story.'
    with pytest.raises(SpendBlocked):art.accepted_scene(0,k.scene,k.checkpoint)
