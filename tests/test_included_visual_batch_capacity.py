"""Oversized Short candidate pools retain all evidence and adjacent pairs."""
from pathlib import Path

import pytest

from app.services import visual_qc, commissioning_longform
from app.services.abacus_router_adapter import MAX_IMAGES
from test_visual_qc import _review, JPEG_BYTES


@pytest.mark.parametrize('missing_last', [False, True])
def test_six_scenes_with_three_choices_fit_transport_without_dropping_frames(monkeypatch, tmp_path, missing_last):
    frame = tmp_path / 'sample.jpg'; frame.write_bytes(JPEG_BYTES)
    sampled, windows, all_samples = set(), [], set()
    def extract(source, *args, **kwargs):
        identity = tuple(int(n) for n in Path(source).stem.split('-')[1:])
        sampled.add(identity); all_samples.add(identity); return frame
    def response(provider, strict, system, content, parts, indices, *args, **kwargs):
        assert provider == 'abacus_included' and strict is True
        assert sum(v.get('type') == 'input_image' for v in content) <= MAX_IMAGES
        current = sorted({v[0] for v in sampled}); sampled.clear(); windows.append(current)
        return {'reviews': [_review(i) for i in indices if not (missing_last and current[i] == 5)]}
    monkeypatch.setattr(visual_qc, '_frame', extract)
    monkeypatch.setattr(visual_qc, '_request_visual_review', response)
    monkeypatch.setattr(visual_qc.settings, 'studio_abacus_included_production', True, raising=False)
    monkeypatch.setattr(commissioning_longform, 'active', lambda: False)
    scenes = [{'narration': 'A banknote rests on a counter.'} for _ in range(6)]
    pools = [[f'clip-{i}-{j}.mp4' for j in range(3)] for i in range(6)]
    result = visual_qc.review_scene_visuals(scenes, pools, tmp_path / 'reviews', max_scenes=6,
        _missing_review_attempts=0, _score_reason_consistency_attempts=0, _temporal_response_repair_attempts=0)
    assert all_samples == {(i, j) for i in range(6) for j in range(3)}
    assert all(any(i in w and i+1 in w for w in windows) for i in range(5))
    assert (5 in result['missing_review_indices']) is missing_last
