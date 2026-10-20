"""Fresh review frames fit transport while every selected moment stays bound."""
import base64
from copy import deepcopy
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest

from app.services import abacus_router_schema_compat as compat
from app.services import abacus_router_adapter as adapter
from test_visual_cross_provider_review import _namespace, _review, EVIDENCE


def run(ns, tmp_path):
    scenes = [{'index': 0, 'narration': 'Dollar notes contain cotton and linen.',
               'visual_queries': ['dollar cotton linen'], 'ai_prompt': None}]
    return ns['review_scene_visuals'](scenes, [[{'path': 'original-stock.mp4'}]], tmp_path, 1,
        _missing_review_attempts=0, topic='Currency materials', story_scenes=scenes,
        content_style='documentary', evidence_sources=EVIDENCE)


@pytest.fixture
def frames(tmp_path):
    # Real high-detail JPEG, larger than the adapter ceiling. No network/media
    # service is involved; the production FFmpeg re-encoder runs below.
    source = tmp_path / 'detailed.jpg'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'nullsrc=s=640x1138,geq=random(1)*255:128:128', '-frames:v', '1',
        '-q:v', '2', '-threads', '1', str(source)], check=True, timeout=15)
    assert source.stat().st_size > adapter.MAX_IMAGE_BYTES
    return source


def test_real_oversized_frames_keep_all_moments_and_pass_original_adapter(frames, tmp_path):
    ns = _namespace()
    ns['settings'].studio_abacus_included_production = True
    ns['subprocess'] = subprocess
    original = frames.read_bytes()
    ns['_frame'] = Mock(return_value=frames)
    captured = []
    def inspect(provider, strict, instruction, content, gemini, indices, moments, *args, **kwargs):
        assert provider == 'abacus_included' and strict is True
        parts = [{'type': 'text', 'text': block['text']} if block['type'] == 'input_text' else
                 {'type': 'image_url', 'image_url': {'url': block['image_url']}}
                 for block in content[1:]]
        prepared = compat.prepare_json_object_router_request(parts, api_key='test-key',
            system_instruction=instruction, json_schema=ns['_review_json_schema'](indices, moments))
        captured.append(prepared)
        assert indices == [0] and moments == {0: {0: {0, 1, 2, 3, 4}}}
        return {'reviews': [_review(ns, score=35, subject_visible=False,
            reason='Unrelated machinery instead of a currency note.')]}
    ns['_request_visual_review'] = Mock(side_effect=inspect)
    result = run(ns, tmp_path / 'review')
    assert len(captured) == 1 and ns['_frame'].call_count == 5
    assert result['reviews'][0]['score'] <= 35  # Compression never implies QA approval.
    images = [base64.b64decode(p['image_url']['url'].split(',', 1)[1])
        for p in captured[0].payload['messages'][1]['content'] if p['type'] == 'image_url']
    assert len(images) == 5 and all(0 < len(raw) <= adapter.MAX_IMAGE_BYTES for raw in images)
    assert all(raw != original for raw in images) and frames.read_bytes() == original
    assert not list(frames.parent.glob('*.gemini_*.jpg'))


def test_already_bounded_bytes_are_identical_and_never_reencoded(tmp_path):
    ns = _namespace()
    ns['settings'].studio_abacus_included_production = True
    ns['subprocess'] = Mock(side_effect=AssertionError('Unnecessary re-encoding'))
    raw = b'\xff\xd8\xfforiginal-small-frame'
    path = tmp_path / 'small.jpg'; path.write_bytes(raw)
    ns['_frame'] = Mock(return_value=path)
    ns['_request_visual_review'] = Mock(return_value={'reviews': [_review(ns)]})
    run(ns, tmp_path / 'review')
    payload = ns['_request_visual_review'].call_args.args[3]
    images = [base64.b64decode(p['image_url'].split(',', 1)[1]) for p in payload if p['type'] == 'input_image']
    assert images == [raw] * 5 and path.read_bytes() == raw
    ns['subprocess'].run.assert_not_called()


def test_unreadable_frames_fail_closed_without_provider_call(frames, tmp_path):
    ns = _namespace()
    ns['settings'].studio_abacus_included_production = True
    ns['_frame'] = Mock(return_value=frames)
    ns['subprocess'] = Mock()
    ns['subprocess'].run.side_effect = subprocess.TimeoutExpired('ffmpeg', 10)
    ns['_request_visual_review'] = Mock(side_effect=AssertionError('No valid image to review'))
    result = run(ns, tmp_path / 'review')
    assert result['reviews'] == [] and result['missing_review_indices'] == [0]
    assert result['unreviewable_scene_indices'] == [0]
    ns['_request_visual_review'].assert_not_called()
    assert ns['subprocess'].run.call_count == 15  # Five moments, three bounded local encodes each.
