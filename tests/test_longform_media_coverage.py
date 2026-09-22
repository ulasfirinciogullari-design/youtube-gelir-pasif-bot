"""Actual batch mapping and private preservation through the last long scene."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.services import visual_qc
from test_commissioning_longform import long_case, setup, commissioned, client
from test_generated_asset_checkpoint import case as asset_case, preserve, MP4
from test_visual_qc import _review, JPEG_BYTES


@pytest.mark.parametrize('last_missing', [False, True])
def test_native_documentary_review_covers_all_scenes_and_adjacent_boundaries(long_case, monkeypatch, tmp_path, last_missing):
    frame = tmp_path / 'sample.jpg'; frame.write_bytes(JPEG_BYTES)
    sampled, windows = set(), []
    def extract(source, *args, **kwargs):
        sampled.add(int(Path(source).stem.split('-')[-1])); return frame
    def response(provider, strict, system, content, parts, indices, *args, **kwargs):
        assert provider == 'abacus_included' and strict is True
        current = sorted(sampled); sampled.clear(); windows.append(current)
        assert 1 <= len(current) <= visual_qc.GEMINI_QC_BATCH_SCENES
        return {'reviews': [_review(local) for local in indices
            if not (last_missing and current[local] == 29)]}
    monkeypatch.setattr(visual_qc, '_frame', extract)
    monkeypatch.setattr(visual_qc, '_request_visual_review', response)
    monkeypatch.setattr(visual_qc.settings, 'studio_abacus_included_production', True, raising=False)
    scenes = [{'narration': 'A banknote rests on a counter.', 'visual_queries':['banknote on counter']}
              for _ in range(30)]
    result = visual_qc.review_scene_visuals(scenes, [[f'clip-{i}.mp4'] for i in range(30)],
        tmp_path / 'reviews', max_scenes=30, _missing_review_attempts=0,
        _score_reason_consistency_attempts=0, _temporal_response_repair_attempts=0)
    assert set().union(*(set(w) for w in windows)) == set(range(30))
    assert all(any(i in w and i + 1 in w for w in windows) for i in range(29))
    if last_missing:
        assert 29 in result['missing_review_indices']
    else:
        assert result['missing_review_indices'] == []
        assert {row['scene_index'] for row in result['reviews']} == set(range(30))


def test_final_long_generated_scene_preserves_exact_voice_and_raw_without_approval(long_case, asset_case):
    args = deepcopy(asset_case.args)
    args['package']['scenes'] *= 5
    voice = args['voice_result']
    voice['spoken_texts'] *= 5; voice['scene_durations'] = [6.] * 30
    voice.update(duration_before_fit=180., duration_after_fit=180., content_target_seconds=180., reserved_tail_seconds=0.)
    path = asset_case.work / 'runway_s29.mp4'; path.write_bytes(MP4)
    args['visual_spec']['path'] = str(path)
    args.update(scene_index=29, duration_minutes=3., options={'mode':'production',
        'format':'landscape','content_plan_item_id':long_case[4]['id']})
    pointer = preserve(asset_case, **args)
    manifest = json.loads(asset_case.client.objects[pointer['manifest_key']][0])
    assert manifest['scene_index'] == 29 and len(manifest['package']['scenes']) == 30
    assert manifest['voice']['spoken_texts'] == voice['spoken_texts']
    assert manifest['qa_approved'] is False and manifest['requires_full_qa'] is True
