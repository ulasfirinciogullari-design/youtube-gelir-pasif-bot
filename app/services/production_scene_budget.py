"""Pure validation for immutable, server-owned text-to-video scene allowances.

This module neither initializes storage nor grants transport permission. The
ledger persists a plan before the first video intent, then admits and debits
each actual quoted request in the same transaction as all other spend caps.
Only verified server code may choose a plan; provider/LLM payloads carry no
authority to create, replace or increase one.
"""
from __future__ import annotations

import hashlib
import re

from app.services.production_spend import (
    SpendBlocked, SpendQuote, _identifier, _integer, _json,
)


_RUNWAY_MODELS = {'gen4.5', 'seedance2_fast'}
_VEO_MODELS = {
    'veo-3.1-lite-generate-preview',
    'veo-3.1-fast-generate-preview',
    'veo-3.1-generate-preview',
}
_DESCRIPTOR_FIELDS = {
    'provider', 'model', 'duration_seconds', 'aspect_ratio', 'resolution',
    'audio', 'sample_count', 'price_revision',
}
_SCENE_FIELDS = {
    'scene_index', 'narration_millis', 'generation_seconds',
    'max_request_micro', 'total_micro', 'allowed_requests',
}
_PLAN_FIELDS = {
    'version', 'channel_id', 'lineage_id', 'kind', 'connection_id',
    'package_sha256', 'scenes',
}


def _require(condition, reason='spend_scene_plan_invalid'):
    if not condition:
        raise SpendBlocked(reason)


def _bounded_int(value, minimum, maximum):
    _require(type(value) is int and minimum <= value <= maximum)
    return value


def _digest(value):
    _require(type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None)
    return value


def video_quote(quote):
    """Conservative detection of historical video intents, including unpriced routes."""
    quote.validate()
    return (quote.provider in {'runway', 'fal'}
            or (quote.provider == 'gemini'
                and quote.model.startswith(('veo-', 'gemini-omni-'))))


def validate_scene_descriptor(descriptor):
    """Return a detached, bounded description of an actual priced video request."""
    _require(type(descriptor) is dict and set(descriptor) == _DESCRIPTOR_FIELDS)
    provider, model = descriptor['provider'], descriptor['model']
    _require(type(provider) is str and type(model) is str)
    _require(type(descriptor['aspect_ratio']) is str
             and descriptor['aspect_ratio'] in {'9:16', '16:9'})
    _require(type(descriptor['resolution']) is str)
    _require(type(descriptor['audio']) is bool)
    _bounded_int(descriptor['sample_count'], 1, 1)
    _require(type(descriptor['price_revision']) is str
             and re.fullmatch(r'[A-Za-z0-9._/-]{1,100}', descriptor['price_revision']) is not None)
    seconds = _bounded_int(descriptor['duration_seconds'], 2, 10)
    if provider == 'runway':
        _require(model in _RUNWAY_MODELS and descriptor['resolution'] == '720p'
                 and descriptor['audio'] is False)
    elif provider == 'gemini':
        _require(model in _VEO_MODELS and seconds in {4, 6, 8}
                 and descriptor['resolution'] in {'720p', '1080p'}
                 and descriptor['audio'] is True)
    elif provider == 'fal':
        from app.services.fal_video_catalog import duration_for
        _require(descriptor['resolution'] == '720p' and descriptor['audio'] is False
                 and duration_for(model, seconds) == seconds)
    else:
        raise SpendBlocked('spend_scene_plan_invalid')
    return dict(descriptor)


def make_scene_plan(*, channel_id, lineage_id, kind, connection_id, package_sha256, scenes):
    """Validate a complete frozen plan; never derive limits from a request or model."""
    for value in (channel_id, lineage_id, connection_id):
        _identifier(value)
    _require(type(kind) is str and kind in {'shorts', 'long', 'derived'})
    _digest(package_sha256)
    _require(type(scenes) is list and 1 <= len(scenes) <= 512)
    result, seen = [], set()
    for scene in scenes:
        _require(type(scene) is dict and set(scene) == _SCENE_FIELDS)
        index = _bounded_int(scene['scene_index'], 0, 511)
        _require(index not in seen)
        seen.add(index)
        _bounded_int(scene['narration_millis'], 1, 7_200_000)
        seconds = _bounded_int(scene['generation_seconds'], 2, 10)
        maximum, total = scene['max_request_micro'], scene['total_micro']
        _integer(maximum)
        _integer(total)
        _require(maximum <= total)
        requests = scene['allowed_requests']
        _require(type(requests) is list and 1 <= len(requests) <= 5)
        allowed, request_keys = [], set()
        for request in requests:
            descriptor = validate_scene_descriptor(request)
            if descriptor['provider'] == 'fal':
                from app.services.fal_video_catalog import duration_for
                billed = duration_for(descriptor['model'], seconds)
            else:
                billed = (seconds if descriptor['provider'] == 'runway'
                          else next((value for value in (4, 6, 8) if value >= seconds), None))
            _require(descriptor['duration_seconds'] == billed)
            key = _json(descriptor)
            _require(key not in request_keys)
            request_keys.add(key)
            allowed.append(descriptor)
        result.append({**scene, 'allowed_requests': sorted(allowed, key=_json)})
    return {
        'version': 1, 'channel_id': channel_id, 'lineage_id': lineage_id,
        'kind': kind, 'connection_id': connection_id, 'package_sha256': package_sha256,
        'scenes': sorted(result, key=lambda scene: scene['scene_index']),
    }


def scene_plan_digest(plan):
    return hashlib.sha256(_json(plan).encode()).hexdigest()


def scene_fields(lineage_id):
    _identifier(lineage_id)
    digest = hashlib.sha256(lineage_id.encode()).hexdigest()
    return 'scene_plan:' + digest, 'scene_usage:' + digest


def initial_scene_usage(plan):
    return {'version': 1, 'plan_sha256': scene_plan_digest(plan),
            'used_micro': {str(scene['scene_index']): 0 for scene in plan['scenes']}}


def validate_scene_state(plan, usage, *, channel_id, lineage_id, kind):
    """Validate both persisted records, including their immutable digest binding."""
    _require(type(plan) is dict and set(plan) == _PLAN_FIELDS
             and type(plan['version']) is int and plan['version'] == 1)
    canonical = make_scene_plan(**{key: value for key, value in plan.items() if key != 'version'})
    _require(plan == canonical)
    _require((plan['channel_id'], plan['lineage_id'], plan['kind'])
             == (channel_id, lineage_id, kind), 'spend_scene_binding_invalid')
    _require(type(usage) is dict and set(usage) == {'version', 'plan_sha256', 'used_micro'}
             and type(usage['version']) is int and usage['version'] == 1
             and usage['plan_sha256'] == scene_plan_digest(plan)
             and type(usage['used_micro']) is dict, 'spend_scene_state_invalid')
    scenes = {str(scene['scene_index']): scene for scene in plan['scenes']}
    _require(set(usage['used_micro']) == set(scenes), 'spend_scene_state_invalid')
    for index, used in usage['used_micro'].items():
        _integer(used)
        _require(used <= scenes[index]['total_micro'], 'spend_scene_state_invalid')
    return scenes


def admit_scene(plan, usage, admission, quote: SpendQuote, *, channel_id, lineage_id,
                kind, lineage_used_micro):
    """Return updated counters plus safe receipt metadata; no mutation or IO."""
    scenes = validate_scene_state(plan, usage, channel_id=channel_id,
                                  lineage_id=lineage_id, kind=kind)
    _require(type(admission) is dict and set(admission) == {
        'connection_id', 'package_sha256', 'scene_index', 'descriptor'},
        'spend_scene_context_invalid')
    index = _bounded_int(admission['scene_index'], 0, 511)
    _require(admission['connection_id'] == plan['connection_id']
             and admission['package_sha256'] == plan['package_sha256']
             and str(index) in scenes, 'spend_scene_binding_invalid')
    descriptor = validate_scene_descriptor(admission['descriptor'])
    scene = scenes[str(index)]
    _require(descriptor in scene['allowed_requests'], 'spend_scene_request_mismatch')
    quote.validate()
    _require((quote.provider, quote.model, quote.price_revision)
             == (descriptor['provider'], descriptor['model'], descriptor['price_revision']),
             'spend_scene_quote_mismatch')
    _integer(lineage_used_micro)
    _require(sum(usage['used_micro'].values()) <= lineage_used_micro,
             'spend_scene_state_invalid')
    _require(quote.maximum_micro <= scene['max_request_micro'], 'spend_scene_request_limit')
    used = usage['used_micro'][str(index)] + quote.maximum_micro
    _require(used <= scene['total_micro'], 'spend_scene_total_limit')
    updated = {**usage, 'used_micro': {**usage['used_micro'], str(index): used}}
    receipt = {**admission, 'descriptor': descriptor,
               'plan_sha256': usage['plan_sha256']}
    return updated, receipt
