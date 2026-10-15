from copy import deepcopy
from pathlib import Path

import pytest

from app.services import curated_stock_review as review, visual_allocation_checkpoint


@pytest.fixture
def case(tmp_path, monkeypatch):
    root = tmp_path/'youtube_factory'
    root.mkdir()
    work = root/'11111111-1111-4111-8111-111111111111_attempt_0'
    work.mkdir()
    monkeypatch.setattr(visual_allocation_checkpoint, '_WORK_ROOT', root)
    voice = work/'recovered_voice.mp3'
    voice.write_bytes(b'ID3-old-voice')
    scenes = [{'narration': str(i), 'transition': 'dip' if i == 1 else 'cut'} for i in range(6)]
    pools = []
    for i, fraction in enumerate((.5,.5,.82,0,.5,.82)):
        path = work/f'raw-{i}.mp4'
        path.write_bytes(b'original-' + bytes([i]))
        pools.append([{'path':str(path), 'curated_pinned':True, 'preserve_start_fraction':True,
                       'start_fraction':fraction, 'forbid_loop':i==3,
                       'source_type':'generated' if i==3 else 'stock'}])
    durations = [4.71,4.22,5.51,4.51,4.24,5.538]
    monkeypatch.setattr(review.render, 'media_duration', lambda path:28.728)
    calls = []
    def normalize(spec, path, duration, index, transition, resolution):
        calls.append((deepcopy(spec), path, duration,index,transition,resolution))
        path.write_bytes(b'exact-review-proxy')
    monkeypatch.setattr(review.render, 'normalize_clip', normalize)
    args = dict(scenes=scenes,scene_visuals=pools,scene_durations=durations,voice_path=voice,work_dir=work)
    return args,calls


def test_proxies_use_the_exact_real_renderer_frame_allocation_and_geometry(case):
    args,calls = case
    before = deepcopy(args)
    raw = {specs[0]['path']:Path(specs[0]['path']).read_bytes() for specs in args['scene_visuals']}
    expected = review.render._scene_timeline(args['scenes'],args['scene_visuals'],args['scene_durations'],28.728,
                                             [pool[0] for pool in args['scene_visuals']])
    counts = review.render._timeline_frame_counts(expected,28.728)
    result = review.exact_review_visuals(**args)
    assert len(result)==len(calls)==6
    assert sum(counts)==862
    for i,call in enumerate(calls):
        assert call[0] == args['scene_visuals'][i][0]
        assert call[2:] == (counts[i]/30,i,args['scenes'][i]['transition'],'1080x1920')
        assert result[i][0]['path'] == str(call[1])
        assert result[i][0]['start_fraction']==0 and result[i][0]['forbid_loop'] is True
        assert result[i][0]['source_type']==args['scene_visuals'][i][0]['source_type']
    assert args==before
    assert {path:Path(path).read_bytes() for path in raw}==raw


@pytest.mark.parametrize('damage', ['missing','unlocked','fraction','foreign','multiple','timing','voice','duplicate_run'])
def test_invalid_or_ambiguous_exact_cut_never_falls_back_to_raw_review(case, damage, tmp_path):
    args,calls = case
    if damage=='missing': args['scene_visuals'].pop()
    if damage=='unlocked': args['scene_visuals'][0][0]['curated_pinned']=False
    if damage=='fraction': args['scene_visuals'][0][0]['start_fraction']=float('nan')
    if damage=='foreign':
        path=tmp_path/'foreign.mp4'; path.write_bytes(b'x'); args['scene_visuals'][0][0]['path']=str(path)
    if damage=='multiple': args['scene_visuals'][0] *= 2
    if damage=='timing': args['scene_durations'][0]=True
    if damage=='voice': args['voice_path']=tmp_path/'foreign.mp3'
    if damage=='duplicate_run': (args['work_dir']/'curated_exact_review').mkdir()
    with pytest.raises(RuntimeError,match='^Curated exact-cut review preparation unavailable$'):
        review.exact_review_visuals(**args)
    assert calls==[]


def test_local_normalization_error_does_not_become_provider_or_raw_fallback(case, monkeypatch):
    args,calls=case
    def fail(*args): raise RuntimeError('private-path-secret')
    monkeypatch.setattr(review.render,'normalize_clip',fail)
    with pytest.raises(RuntimeError,match='^Curated exact-cut review preparation unavailable$'):
        review.exact_review_visuals(**args)


def test_natural_saved_short_keeps_all_its_audio_and_exact_scene_frames(case, monkeypatch):
    args, calls = case
    monkeypatch.setattr(review.render, 'media_duration', lambda path: 31.584)
    with pytest.raises(RuntimeError):
        review.exact_review_visuals(**args)
    assert not calls
    result = review.exact_review_visuals(**args, natural_short=True)
    assert len(result) == 6
    assert sum(round(call[2] * 30) for call in calls) == round(31.584 * 30)
