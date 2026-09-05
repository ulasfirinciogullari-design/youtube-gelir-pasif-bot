from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import audio_checkpoint, curated_stock, paid_render_recovery


LEAF = '8096689f-67cd-4ec0-972c-2da2cf56cc70'
ORIGIN = '11111111-1111-4111-8111-111111111111'
CHILD = '22222222-2222-4222-8222-222222222222'
PREP = '33333333-3333-4333-8333-333333333333'
OTHER = '44444444-4444-4444-8444-444444444444'
IDS = {0: 34839495, 1: 6700265, 2: 6841995, 4: 7829486, 5: 6266251}
FRACTIONS = {0: 0.5, 1: 0.5, 2: 0.82, 3: 0, 4: 0.5, 5: 0.82}
NEW_ID = 10440850
MP4 = b'\x00\x00\x00\x18ftypmp42' + b'original-pexels-source' * 80


def encode(value):
    return curated_stock._bytes(value)


def sha(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_checkpoint, '_AUDIO_TEMP_ROOT', tmp_path)
    factory = tmp_path / 'youtube_factory'
    factory.mkdir()
    prep, child = factory / f'{PREP}_attempt_0', factory / f'{CHILD}_attempt_0'
    prep.mkdir()
    child.mkdir()
    clips = {identifier: MP4 + str(identifier).encode() for identifier in [*IDS.values(), NEW_ID]}
    paid = MP4 + b'paid-existing-source'
    voice_sha = sha(b'already-approved-existing-voice')
    spec = {'topic': 'Doların kâğıdı', 'duration_minutes': 0.5, 'language': 'tr',
            'mode': 'production', 'format': 'shorts', 'music': 'off'}
    package = {'title': 'Doların kâğıdı', 'scenes': [
        {'index': index, 'narration': f'Sahne {index}: doların kâğıdı pamuk ve keten içerir.',
         'ai_prompt': None, 'visual_queries': ['banknote close up']} for index in range(6)
    ], 'sources': []}
    package_sha = curated_stock._digest(package)
    package['_recovered_voice'] = {'version': 1, 'source_task_id': ORIGIN, 'package_sha256': package_sha,
                                   'sha256': voice_sha, 'size': 250000, 'scene_durations': [4.9] * 6}
    package['_recovered_generated_media'] = {'version': 3, 'recovery_only': True, 'source_task_id': ORIGIN,
                                            'package_sha256': package_sha,
                                            'scenes': {'3': [{'sha256': sha(paid), 'size': len(paid)}]}}
    metadata = {'version': 1, 'status': 'qa_workprint', 'task_id': LEAF, 'qa_approved': False,
                'publish_eligible': False, 'reusable': False, 'voice': {'sha256': voice_sha},
                'video_sha256': sha(MP4), 'video_size': len(MP4), 'scenes': []}
    for index in range(6):
        raw = paid if index == 3 else clips[IDS[index]]
        selected = {'sha256': sha(raw), 'size': len(raw), 'start_fraction': FRACTIONS[index]}
        if index != 3:
            selected.update(pexels_id=IDS[index], source_type='stock', stock_provider='pexels')
        metadata['scenes'].append({'scene_index': index, 'narration': package['scenes'][index]['narration'],
                                   'duration_seconds': 4.9, 'selection': selected,
                                   'review': {'score': 38 if index == 4 else 95}})
    payload = encode(metadata)
    metadata_key = f'qa_workprints/{LEAF}/{sha(payload)}.json'
    workprint = {'version': 1, 'status': 'qa_workprint', 'qa_approved': False, 'publish_eligible': False,
                 'reusable': False, 'task_id': LEAF, 'key': f'qa_workprints/{LEAF}/{sha(MP4)}.mp4',
                 'sha256': sha(MP4), 'size': len(MP4), 'etag': '"original-etag"',
                 'duration_seconds': 30.0, 'frame_count': 900, 'width': 1080, 'height': 1920,
                 'metadata_key': metadata_key, 'metadata_sha256': sha(payload), 'metadata_size': len(payload)}
    leaf = {'task_id': LEAF, 'state': 'FAILURE', 'kind': 'render', 'failure_stage': 'final_visual_qc_rescue',
            'paid_create_slots_used': 0, 'spec': spec, 'qa_workprint': workprint,
            'audio_candidate_checkpoint': {'audio_sha256': voice_sha, 'size': 250000,
                                           'package_sha256': curated_stock._digest(audio_checkpoint._candidate_package(package))}}
    objects, writes, reads, downloads = {metadata_key: payload}, [], [], []

    def get(**kwargs):
        key = kwargs['Key']
        reads.append(key)
        raw = objects[key]
        return {'ContentLength': len(raw), 'Body': io.BytesIO(raw)}

    def put(**kwargs):
        writes.append({**kwargs, 'Body': kwargs['Body'].read()})
        assert kwargs['IfNoneMatch'] == '*'
        assert kwargs['Key'] not in objects
        objects[kwargs['Key']] = writes[-1]['Body']
        return {'ETag': '"immutable-etag"'}

    client = SimpleNamespace(get_object=get, put_object=put)
    monkeypatch.setattr(curated_stock.storage, '_client', lambda: client)
    monkeypatch.setattr(curated_stock.studio_state, '_client', lambda: object())
    receipt = {'source_task_id': ORIGIN, 'approved_package': package}
    monkeypatch.setattr(paid_render_recovery, '_load_original_receipt', lambda pointer: receipt)
    monkeypatch.setattr(paid_render_recovery, '_continuation_state', lambda *args: ({'task_id': ORIGIN}, leaf, 'fixed-fingerprint'))
    monkeypatch.setattr(paid_render_recovery, '_validate_continuation_package', lambda *args: None)

    def by_id(identifier, output):
        downloads.append(identifier)
        output.write_bytes(clips[identifier])
        return ({'pexels_id': identifier, 'sha256': sha(clips[identifier]), 'size': len(clips[identifier]),
                 'width': 1080, 'height': 1920, 'source_duration': 12.0},
                {'source': 'Pexels', 'creator_name': 'Real Creator', 'creator_url': 'https://www.pexels.com/@creator/',
                 'page_url': f'https://www.pexels.com/video/{identifier}/', 'license_url': 'https://www.pexels.com/license/'})

    monkeypatch.setattr(curated_stock, '_pexels_by_id', by_id)
    monkeypatch.setattr(curated_stock, '_probe', lambda path: {'width': 1080, 'height': 1920, 'source_duration': 12.0})
    return SimpleNamespace(leaf=leaf, package=package, metadata=metadata, objects=objects, writes=writes, reads=reads,
                           downloads=downloads, prep=prep, child=child, client=client, clips=clips, by_id=by_id, receipt=receipt)


def prepare(case, **changes):
    return curated_stock.prepare_curated_stock(LEAF, {'operator': 'trusted receipt'},
                                               changes.get('replacements', {4: {'pexels_id': NEW_ID, 'start_fraction': 0.5}}),
                                               changes.get('work_dir', case.prep))['curated_stock_manifest']


def validate(case, pointer):
    return curated_stock.validate_curated_stock_manifest(pointer, source_job=case.leaf, approved_package=case.package)


def load(case, pointer, **changes):
    return curated_stock.load_curated_stock_manifest(pointer, source_job=case.leaf, approved_package=case.package,
                                                     child_task_id=changes.get('child_task_id', CHILD),
                                                     work_dir=changes.get('work_dir', case.child))


def resign(case, pointer, mutate):
    manifest = json.loads(case.objects[pointer['key']])
    mutate(manifest)
    raw = encode(manifest)
    pointer.update(sha256=sha(raw), size=len(raw), key=f'curated_stock/{LEAF}/{sha(raw)}.json')
    case.objects[pointer['key']] = raw


def test_prepare_preserves_four_exact_good_sources_and_only_replaces_four(case):
    before = deepcopy((case.leaf, case.package))
    pointer = prepare(case)
    manifest = validate(case, pointer)
    assert set(pointer) == {'version', 'source_task_id', 'key', 'sha256', 'size'}
    assert pointer['source_task_id'] == LEAF
    assert pointer['key'] == f'curated_stock/{LEAF}/{pointer["sha256"]}.json'
    assert manifest['qa_approved'] is False and manifest['requires_full_qa'] is True
    assert manifest['paid_scene_indices'] == [3] and manifest['replacement_scene_indices'] == [4]
    assert manifest['asset_source_task_id'] == ORIGIN
    assert manifest['source_workprint_metadata_sha256'] == case.leaf['qa_workprint']['metadata_sha256']
    assert case.downloads == [IDS[0], IDS[1], IDS[2], NEW_ID, IDS[5]]
    assert len(case.writes) == 6
    assert (case.leaf, case.package) == before
    for index in (0, 1, 2, 5):
        entry = manifest['scenes'][str(index)]
        assert entry['sha256'] == sha(case.clips[IDS[index]])
        assert entry['start_fraction'] == FRACTIONS[index]
    assert manifest['scenes']['4']['pexels_id'] == NEW_ID
    assert all(call['CacheControl'] == 'private, no-store' and 'ACL' not in call for call in case.writes)
    encoded = json.dumps(manifest)
    for forbidden in ('review', 'score', 'Authorization', 'download_url', 'videos.pexels.com', str(case.prep)):
        assert forbidden not in encoded


def test_reader_loads_five_hash_bound_candidates_without_approval_or_pexels_requests(case):
    pointer = prepare(case)
    calls = list(case.downloads)
    result = load(case, pointer)
    assert set(result) == {'scene_visuals', 'credits'}
    assert set(result['scene_visuals']) == {0, 1, 2, 4, 5}
    assert len(result['credits']) == 5 and case.downloads == calls
    for index, pool in result['scene_visuals'].items():
        assert len(pool) == 1
        spec = pool[0]
        assert spec['preserve_start_fraction'] is True and spec['generated'] is False
        assert spec['source_type'] == 'stock' and spec['stock_provider'] == 'pexels'
        assert Path(spec['path']).parent == case.child
        assert Path(spec['path']).read_bytes() == case.clips[NEW_ID if index == 4 else IDS[index]]
        assert not {'score', 'pass', 'qa_approved'} & set(spec)
    assert {row['selected_by'] for row in result['credits']} == {'curated_stock_manifest'}


def test_consumed_parent_claim_flags_are_allowed_but_not_modified(case):
    pointer = prepare(case)
    case.leaf.update(retry_claimed=True, repair_claimed=True, retry_child_task_id=CHILD, repair_available=False)
    before = deepcopy(case.leaf)
    assert validate(case, pointer)
    assert case.leaf == before


@pytest.mark.parametrize('replacement', [{}, {0: {'pexels_id': NEW_ID, 'start_fraction': 0.5}},
    {4: {'pexels_id': True, 'start_fraction': 0.5}}, {4: {'pexels_id': NEW_ID, 'start_fraction': float('nan')}},
    {4: {'pexels_id': NEW_ID, 'start_fraction': 1.0}}, {4: {'pexels_id': NEW_ID, 'start_fraction': 0.5, 'score': 99}}])
def test_invalid_replacement_request_never_downloads_or_writes(case, replacement):
    with pytest.raises(curated_stock.CuratedStockError):
        prepare(case, replacements=replacement)
    assert not case.downloads and not case.writes


def test_changed_good_raw_bytes_abort_before_any_storage_write(case):
    case.clips[IDS[1]] += b'changed-variant'
    with pytest.raises(curated_stock.CuratedStockError):
        prepare(case)
    assert not case.writes


@pytest.mark.parametrize('mutation', [
    lambda leaf: leaf.update(state='SUCCESS'), lambda leaf: leaf.update(kind='publish'),
    lambda leaf: leaf.update(paid_create_slots_used=1), lambda leaf: leaf['spec'].update(mode='preview'),
    lambda leaf: leaf['spec'].update(format='landscape'), lambda leaf: leaf['spec'].update(music='ambient'),
    lambda leaf: leaf['spec'].update(duration_minutes=1.0), lambda leaf: leaf['qa_workprint'].update(task_id=OTHER),
    lambda leaf: leaf['audio_candidate_checkpoint'].update(audio_sha256='f' * 64),
])
def test_wrong_source_binding_fails_without_raw_download(case, mutation):
    pointer = prepare(case)
    before = len(case.reads)
    mutation(case.leaf)
    with pytest.raises(curated_stock.CuratedStockError):
        validate(case, pointer)
    assert not any('/raw/' in key for key in case.reads[before:])


@pytest.mark.parametrize('mutation', [
    lambda package: package['scenes'][0].update(narration='Changed narration'),
    lambda package: package['_recovered_voice'].update(sha256='e' * 64),
    lambda package: package['_recovered_voice'].update(scene_durations=[4.8, 5.0, 4.9, 4.9, 4.9, 4.9]),
    lambda package: package['_recovered_generated_media'].update(version=1),
    lambda package: package['_recovered_generated_media'].update(recovery_only=False),
    lambda package: package['_recovered_generated_media'].update(source_task_id=OTHER),
    lambda package: package['_recovered_generated_media']['scenes'].update({'4': []}),
])
def test_changed_story_or_paid_voice_contract_fails(case, mutation):
    pointer = prepare(case)
    mutation(case.package)
    with pytest.raises(curated_stock.CuratedStockError):
        validate(case, pointer)


@pytest.mark.parametrize('mutation', [
    lambda raw: raw.update(qa_approved=True), lambda raw: raw.update(requires_full_qa=False),
    lambda raw: raw.update(source_task_id=OTHER), lambda raw: raw.update(asset_source_task_id=OTHER),
    lambda raw: raw.update(package_sha256='f' * 64), lambda raw: raw.update(source_spec_sha256='f' * 64),
    lambda raw: raw.update(source_workprint_metadata_sha256='f' * 64),
    lambda raw: raw.update(replacement_scene_indices=[0]), lambda raw: raw.update(replacement_scene_indices=[4.0]),
    lambda raw: raw['scenes'].pop('5'), lambda raw: raw['scenes'].update({'3': raw['scenes']['4']}),
    lambda raw: raw['scenes']['0'].update(start_fraction=0.82),
    lambda raw: raw['scenes']['0'].update(pexels_id=NEW_ID),
    lambda raw: raw['scenes']['4'].update(key='secret/arbitrary-object'),
    lambda raw: raw['scenes']['4'].update(score=100),
    lambda raw: raw['scenes']['4']['attribution'].update(creator_url='https://secret.test?token=SECRET'),
])
def test_manifest_resigned_tampering_still_cannot_change_authoritative_bindings(case, mutation):
    pointer = prepare(case)
    resign(case, pointer, mutation)
    with pytest.raises(curated_stock.CuratedStockError):
        validate(case, pointer)


def test_raw_storage_corruption_cannot_reach_visual_qa(case):
    pointer = prepare(case)
    manifest = validate(case, pointer)
    key = manifest['scenes']['0']['key']
    case.objects[key] = b'X' * len(case.objects[key])
    with pytest.raises(curated_stock.CuratedStockError):
        load(case, pointer)


@pytest.mark.parametrize('point', ['builder', 'manifest', 'actual_probe'])
def test_short_source_does_not_invent_repetition_to_cover_narration(case, monkeypatch, point):
    if point == 'builder':
        def too_short(identifier, output):
            entry, attribution = case.by_id(identifier, output)
            entry['source_duration'] = 5.0
            return entry, attribution
        monkeypatch.setattr(curated_stock, '_pexels_by_id', too_short)
        with pytest.raises(curated_stock.CuratedStockError):
            prepare(case)
        assert not case.writes
        return
    pointer = prepare(case)
    if point == 'manifest':
        resign(case, pointer, lambda raw: raw['scenes']['4'].update(source_duration=5.0))
        with pytest.raises(curated_stock.CuratedStockError):
            validate(case, pointer)
    else:
        monkeypatch.setattr(curated_stock, '_probe', lambda path: {'width': 1080, 'height': 1920, 'source_duration': 5.0})
        with pytest.raises(curated_stock.CuratedStockError):
            load(case, pointer)


def test_manifest_or_workprint_hash_mismatch_fails(case):
    pointer = prepare(case)
    case.objects[pointer['key']] += b' '
    with pytest.raises(curated_stock.CuratedStockError):
        validate(case, pointer)


@pytest.mark.parametrize('kind', ['parent', 'outside', 'existing'])
def test_child_work_scope_and_no_overwrite(case, kind):
    pointer = prepare(case)
    changes = {}
    if kind == 'parent':
        changes['child_task_id'] = LEAF
    elif kind == 'outside':
        changes['work_dir'] = case.prep
    else:
        (case.child / 'curated_stock_00.mp4').write_bytes(b'USER DATA')
    with pytest.raises(curated_stock.CuratedStockError):
        load(case, pointer, **changes)
    if kind == 'existing':
        assert (case.child / 'curated_stock_00.mp4').read_bytes() == b'USER DATA'


def test_lineage_change_before_write_aborts(case, monkeypatch):
    checks = []
    def changed(*args):
        checks.append(1)
        return {'task_id': ORIGIN}, case.leaf, 'first' if len(checks) == 1 else 'changed'
    monkeypatch.setattr(paid_render_recovery, '_continuation_state', changed)
    with pytest.raises(curated_stock.CuratedStockError):
        prepare(case)
    assert not case.writes


def test_all_errors_are_fixed_and_secret_free(case, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError('https://private.test/?SECRET Authorization: KEY')
    monkeypatch.setattr(curated_stock, '_pexels_by_id', broken)
    with pytest.raises(curated_stock.CuratedStockError) as error:
        prepare(case)
    assert str(error.value) == 'Curated stock preparation unavailable; no replacement was generated'


@pytest.fixture
def pexels_http(monkeypatch, tmp_path):
    requests = []
    data = {'id': NEW_ID, 'user': {'name': 'Creator', 'url': 'https://www.pexels.com/@creator/'},
            'video_files': [{'width': 1080, 'height': 1920, 'file_type': 'video/mp4',
                             'link': f'https://videos.pexels.com/video-files/{NEW_ID}/source-hd.mp4'}]}
    class Response:
        status_code = 200
        def __init__(self, raw):
            self.raw = raw
            self.headers = {'Content-Length': str(len(raw))}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def iter_bytes(self):
            yield self.raw
    overrides = {}
    def stream(method, url, **kwargs):
        requests.append((method, url, kwargs))
        response = Response(encode(data) if 'api.pexels.com' in url else MP4)
        for key, value in overrides.items():
            setattr(response, key, value)
        return response
    monkeypatch.setattr(curated_stock.httpx, 'stream', stream)
    monkeypatch.setattr(curated_stock.pexels, '_headers', lambda: {'Authorization': 'TEST-PRIVATE-API-KEY'})
    monkeypatch.setattr(curated_stock, '_probe', lambda path: {'width': 1080, 'height': 1920, 'source_duration': 12.0})
    return SimpleNamespace(data=data, requests=requests, output=tmp_path / 'clip.mp4', overrides=overrides)


def test_official_id_endpoint_and_cdn_receive_no_cross_host_authorization(pexels_http):
    data = pexels_http
    entry, attribution = curated_stock._pexels_by_id(NEW_ID, data.output)
    assert data.requests[0][1] == f'https://api.pexels.com/v1/videos/videos/{NEW_ID}'
    assert data.requests[0][2]['headers']['Authorization'] == 'TEST-PRIVATE-API-KEY'
    assert 'Authorization' not in data.requests[1][2]['headers']
    assert all(request[2]['follow_redirects'] is False for request in data.requests)
    assert all(request[2]['headers']['Accept-Encoding'] == 'identity' for request in data.requests)
    assert entry['sha256'] == sha(MP4)
    assert attribution['license_url'] == 'https://www.pexels.com/license/'
    assert 'download_url' not in entry


@pytest.mark.parametrize('url', ['http://videos.pexels.com/video-files/a.mp4',
    'https://evil.test/video-files/a.mp4', 'https://videos.pexels.com.evil.test/video-files/a.mp4',
    'https://user:secret@videos.pexels.com/video-files/a.mp4', 'https://videos.pexels.com:8443/video-files/a.mp4',
    'https://videos.pexels.com/video-files/a.mp4?token=SECRET', 'https://videos.pexels.com/video-files/%2e%2e/a.mp4',
    'https://videos.pexels.com/video-files/a.m3u8'])
def test_api_cannot_redirect_credentials_or_downloads_to_arbitrary_urls(pexels_http, url):
    pexels_http.data['video_files'][0]['link'] = url
    with pytest.raises(ValueError):
        curated_stock._pexels_by_id(NEW_ID, pexels_http.output)
    assert len(pexels_http.requests) == 1


@pytest.mark.parametrize('override', [{'status_code': 302}, {'status_code': 404},
    {'headers': {'Content-Length': str(curated_stock.MAX_METADATA_BYTES + 1)}},
    {'headers': {'Content-Encoding': 'gzip'}}, {'headers': {'Content-Length': '1'}}])
def test_http_redirect_size_and_encoding_are_fail_closed(pexels_http, override):
    pexels_http.overrides.update(override)
    with pytest.raises(ValueError):
        curated_stock._pexels_by_id(NEW_ID, pexels_http.output)
    assert not pexels_http.output.exists()


@pytest.mark.parametrize('raw', [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}'])
def test_strict_json_rejects_ambiguous_protocol_values(raw):
    with pytest.raises(ValueError):
        curated_stock._json(raw)
