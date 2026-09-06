"""Only the observed Redis cjson float spelling is normalized in a hash copy."""
from copy import deepcopy
import json
import math

import pytest

from app.services import external_editorial_review as review, studio_state
from test_external_editorial_review import case, raw, saved
from test_editorial_evidence_v2 import v2


def _observed_wire_roundtrip(c):
    # fakeredis's Python-backed cjson keeps binary-float precision, unlike the
    # production Redis cjson default. Replay the actual observed 14-digit wire
    # number explicitly, then use the real publisher merge against fake Redis.
    source = saved(c)
    assert source['spec']['duration_minutes'] == 35833 / 60000
    wire = raw(source).replace('"duration_minutes":0.5972166666666666',
                               '"duration_minutes":0.59721666666667')
    assert wire != raw(source)
    c.client.set(review.JOB_PREFIX + c.task, wire)
    return json.loads(wire)


def test_actual_14_digit_wire_and_publisher_merge_keep_existing_receipt(v2, monkeypatch):
    c = v2
    receipt = review.create_editorial_review(c.task, c.pack)
    receipt_raw = c.client.get(review.EDITORIAL_RECEIPT_PREFIX + c.task)
    original_source = saved(c)
    original_projection = {'task_id': c.task, 'spec': original_source['spec'],
        'result': {key: original_source['result'].get(key) for key in review._RESULT_BINDING}}
    assert review._digest(original_projection) == receipt['source_sha256']
    rounded = _observed_wire_roundtrip(c)
    assert rounded['spec']['duration_minutes'] == 0.59721666666667
    assert rounded['spec']['duration_minutes'] != 35833 / 60000
    monkeypatch.setattr(studio_state, '_client', lambda: c.client)
    assert studio_state.merge_youtube_result_field(c.task, 'youtube_automation',
        {'status': 'failed', 'reason': 'existing_preflight_failure'})
    current = saved(c)
    before = deepcopy(current)
    assert review.validate_editorial_publication(current) == receipt
    assert review._projection(current) == original_projection
    assert current == before and current['spec']['duration_minutes'] == 0.59721666666667
    assert c.client.get(review.EDITORIAL_RECEIPT_PREFIX + c.task) == receipt_raw
    assert review.create_editorial_review(c.task, c.pack) == receipt
    assert len(c.calls) == 1  # Idempotent replay performs no new media proof.


@pytest.mark.parametrize('value', [
    math.nextafter(35833 / 60000, math.inf),
    math.nextafter(35833 / 60000, -math.inf),
    math.nextafter(0.59721666666667, math.inf),
    math.nextafter(0.59721666666667, -math.inf),
    35834 / 60000, '0.59721666666667', True, float('nan'), float('inf'),
])
def test_adjacent_or_arbitrary_close_values_do_not_gain_duration_authority(v2, value):
    source = deepcopy(v2.source)
    source['spec']['duration_minutes'] = value
    with pytest.raises(review.EditorialReviewError):
        review._projection(source)


@pytest.mark.parametrize('damage', ['millisecond', 'language', 'profile', 'caption', 'metadata'])
def test_canonical_duration_does_not_hide_any_other_receipt_change(v2, damage):
    c = v2
    receipt = review.create_editorial_review(c.task, c.pack)
    source = _observed_wire_roundtrip(c)
    if damage == 'millisecond':
        source['result']['external_provenance']['duration_ms'] += 1
        source['spec']['duration_minutes'] = 35834 / 60000
    elif damage == 'language': source['spec']['language'] = 'en'
    elif damage == 'profile': source['spec']['production_profile_revision'] = 'different-profile-1234'
    elif damage == 'caption': source['result']['expected_caption_sha256'] = '0' * 64
    else: source['result']['publish_metadata']['title'] = 'Different editorial title'
    c.client.set(review.JOB_PREFIX + c.task, raw(source))
    with pytest.raises(review.EditorialReviewError): review.validate_editorial_publication(source)
    assert json.loads(c.client.get(review.EDITORIAL_RECEIPT_PREFIX + c.task)) == receipt


def test_v1_duration_and_projection_remain_exact(case):
    c = case
    original = deepcopy(c.source)
    assert review._projection(c.source)['spec'] == original['spec']
    c.source['spec']['duration_minutes'] = math.nextafter(.5, math.inf)
    with pytest.raises(review.EditorialReviewError): review._contract(c.source)


def test_v1_projection_keeps_preexisting_absent_provenance_behavior(case):
    source = deepcopy(case.source)
    source['result'].pop('external_provenance')
    assert review._projection(source) == {'task_id': source['task_id'], 'spec': source['spec'],
        'result': {key: source['result'].get(key) for key in review._RESULT_BINDING}}
