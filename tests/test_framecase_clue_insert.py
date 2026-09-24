import hashlib
import json
import math
import subprocess

import pytest

from app.services import framecase_clue_insert as clue, content_plan as plan
from app.services.framecase_cadence import CHANNEL_ID


def test_only_captured_terminal_policy_refusal_admits_authored_insert(monkeypatch):
    from app.services import youtube_auth
    monkeypatch.setattr(youtube_auth, '_decrypt_json', lambda value: {'response': value})
    package = {'narration': 'The canonical story.'}
    dispatch = {'channel_id': CHANNEL_ID, 'task_id': 'root',
        'item': {'series': {'id': 'framecase_clock_that_lied_s01', 'number': 1}}}
    checkpoint = {'package': package, 'clips': {'1': {'asset': 'retained-gallery'}}}
    raw = json.dumps({'detail': [{'type': 'content_policy_violation'}]})
    result = {'http_status': 422, 'encrypted_response': raw,
              'response_sha256': hashlib.sha256(raw.encode()).hexdigest()}
    row = {'request': {'scene_index': 2, 'package_sha256': plan._sha(package)}, 'result': result}
    journal = {'context': {'lineage_id': 'root'}, 'requests': {'original-denied-request': row}}
    assert clue.eligible(dispatch, checkpoint, journal, 2)
    assert not clue.eligible(dispatch, checkpoint, journal, 1)
    for status in (200, 401, 429, 500):
        result['http_status'] = status
        assert not clue.eligible(dispatch, checkpoint, journal, 2)
    result['http_status'] = 422
    row['result'] = None
    assert not clue.eligible(dispatch, checkpoint, journal, 2)
    row['result'] = {**result, 'response_sha256': '0' * 64}
    with pytest.raises(ValueError): clue.eligible(dispatch, checkpoint, journal, 2)


def test_actual_authored_insert_uses_retained_footage_without_provider_calls(tmp_path):
    gallery = tmp_path / 'gallery.mp4'; output = tmp_path / 'insert.mp4'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
        'testsrc2=size=360x640:rate=30:duration=5', '-c:v', 'libx264',
        '-threads', '1', '-preset', 'ultrafast', str(gallery)], check=True, capture_output=True, timeout=30)
    before = gallery.read_bytes()
    proof = clue.render(gallery, output, 5)
    assert proof['new_provider_requests'] == 0
    assert proof['source_sha256'] == hashlib.sha256(before).hexdigest()
    assert gallery.read_bytes() == before
    from app.services.render import video_frame_count, media_duration
    assert video_frame_count(output) == 150
    assert media_duration(output) == pytest.approx(5, abs=.001)
    assert 'qa_approved' not in proof and 'publish_eligible' not in proof
    # Decode actual pixels: equal second hands must agree despite a 24-hour
    # wrap and different minute hands. This caught FFmpeg's huge-angle error.
    pixels = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', '2', '-i', str(output),
        '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'], timeout=30)
    assert len(pixels) == 720 * 1280 * 3
    angle = 2 * math.pi * 37 / 60
    for cx in (190, 530):
        x, y = round(cx + 88 * math.sin(angle)), round(1038 - 88 * math.cos(angle))
        colors = [pixels[(yy * 720 + xx) * 3:(yy * 720 + xx) * 3 + 3]
                  for yy in range(y-3, y+4) for xx in range(x-3, x+4)]
        assert any(r > 110 and r-g > 40 and g-b > 15 for r, g, b in colors)
