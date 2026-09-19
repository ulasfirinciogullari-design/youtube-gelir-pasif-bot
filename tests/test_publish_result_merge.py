import ast
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from typing import Any

import fakeredis
import pytest


def _merge_function(client):
    source = Path(__file__).resolve().parents[1] / 'app' / 'services' / 'studio_state.py'
    constants = {'JOB_PREFIX', 'JOB_TTL_SECONDS', '_MERGE_YOUTUBE_RESULT_FIELD',
                 'RETAINED_DELIVERY_CHILD_PREFIX', '_RETAINED_LINEAGE', '_RETAINED_ROOT_KEYS'}
    functions = {'_job_key', '_json_default', 'merge_youtube_result_field',
                 'retained_delivery_fence_keys'}
    definitions = [node for node in ast.parse(source.read_text(encoding='utf-8')).body
                   if (isinstance(node, ast.Assign) and any(
                       isinstance(target, ast.Name) and target.id in constants for target in node.targets
                   )) or (isinstance(node, ast.FunctionDef) and node.name in functions)]
    namespace = {
        'json': json, 'Any': Any, '_client': lambda: client,
        '_now_iso': lambda: '2026-09-05T00:00:00+00:00',
    }
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(source), 'exec'), namespace)
    return namespace['merge_youtube_result_field']


@pytest.mark.parametrize('order', [('youtube', 'youtube_automation'), ('youtube_automation', 'youtube')])
def test_publisher_and_dispatcher_preserve_each_others_result_fields(order):
    client = fakeredis.FakeRedis(decode_responses=True)
    merge = _merge_function(client)
    key = 'youtube_studio:job:source-publish'
    client.set(key, json.dumps({'state': 'SUCCESS', 'result': {
        'video_key': 'videos/source/final.mp4',
        'publish_metadata': {'title': 'Original title'},
    }}))
    fields = {
        'youtube': {'video_id': 'uploaded', 'privacy_status': 'private'},
        'youtube_automation': {'status': 'queued', 'publish_task_id': 'publish-child'},
    }
    for field in order:
        assert merge('source-publish', field, fields[field]) is True
    result = json.loads(client.get(key))['result']
    assert result['youtube'] == fields['youtube']
    assert result['youtube_automation'] == fields['youtube_automation']
    assert result['video_key'] == 'videos/source/final.mp4'
    assert result['publish_metadata'] == {'title': 'Original title'}
    assert client.ttl(key) > 0


def test_simultaneous_nested_publisher_updates_do_not_lose_fields():
    client = fakeredis.FakeRedis(decode_responses=True)
    merge = _merge_function(client)
    key = 'youtube_studio:job:source-concurrent'
    client.set(key, json.dumps({'result': {'video_key': 'videos/source/final.mp4'}}))
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda index: merge('source-concurrent', 'youtube_automation', {f'field{index}': index}),
            range(24),
        ))
    assert all(results)
    assert json.loads(client.get(key))['result']['youtube_automation'] == {
        f'field{index}': index for index in range(24)
    }


def test_result_merge_never_creates_a_missing_render_or_accepts_unowned_fields():
    client = fakeredis.FakeRedis(decode_responses=True)
    merge = _merge_function(client)
    assert merge('missing', 'youtube', {'video_id': 'uploaded'}) is False
    assert client.get('youtube_studio:job:missing') is None
    with pytest.raises(ValueError):
        merge('missing', 'quality_disposition', {})


@pytest.mark.parametrize('published', [
    {'youtube_automation': {'status': 'queued'}},
    {'youtube_automation': {'publish_task_id': 'child-id'}},
    {'youtube': {'video_id': 'uploaded'}},
    {'youtube_url': 'https://www.youtube.com/watch?v=uploaded'},
])
def test_missing_outcome_fill_atomically_preserves_status_child_and_video(published):
    client = fakeredis.FakeRedis(decode_responses=True)
    merge = _merge_function(client)
    key = 'youtube_studio:job:source-fill'
    result = {'video_key': 'videos/master.mp4', **published}
    client.set(key, json.dumps({'result': result}))
    assert merge('source-fill', 'youtube_automation', {'status': 'queue_error'}, only_if_missing=True) is False
    assert json.loads(client.get(key))['result'] == result


def test_missing_outcome_fill_keeps_existing_metadata_and_real_publisher_can_advance():
    client = fakeredis.FakeRedis(decode_responses=True)
    merge = _merge_function(client)
    key = 'youtube_studio:job:source-fill'
    client.set(key, json.dumps({'result': {
        'video_key': 'videos/master.mp4', 'audio_qc': {'score': 100},
        'youtube_automation': {'profile_revision': 'approved'},
    }}))
    assert merge('source-fill', 'youtube_automation', {'status': 'queue_error'}, only_if_missing=True)
    assert merge('source-fill', 'youtube_automation', {'status': 'queued', 'publish_task_id': 'child'})
    result = json.loads(client.get(key))['result']
    assert result['youtube_automation'] == {
        'status': 'queued', 'publish_task_id': 'child', 'profile_revision': 'approved',
    }
    assert result['audio_qc'] == {'score': 100}
    assert result['video_key'] == 'videos/master.mp4'


def test_fallback_callback_racing_with_publisher_never_erases_real_outcome():
    client = fakeredis.FakeRedis(decode_responses=True)
    merge = _merge_function(client)
    key = 'youtube_studio:job:source-race'
    client.set(key, json.dumps({'result': {'video_key': 'videos/master.mp4'}}))
    with ThreadPoolExecutor(max_workers=2) as pool:
        fallback = pool.submit(merge, 'source-race', 'youtube_automation', {'status': 'queue_error'}, only_if_missing=True)
        publisher = pool.submit(merge, 'source-race', 'youtube_automation', {'status': 'queued', 'publish_task_id': 'child'})
        fallback.result()
        publisher.result()
    assert json.loads(client.get(key))['result']['youtube_automation'] == {
        'status': 'queued', 'publish_task_id': 'child',
    }
