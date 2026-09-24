"""Complete long-form media and real queue authority; all provider I/O is offline."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.services import commissioning_longform as longform, content_plan as plan
from app.services import commissioning_reasoning as native, production_spend_runtime as runtime
from app.services import production_included_router as included, whisper_transcription as whisper
from app.services.production_spend import SpendBlocked, LEDGER_KEY
from test_commissioning_reasoning import setup, commissioned, client, CHANNEL, CONTEXT, RESULT
from test_production_cash_disabled import dump
from test_whisper_transcription import _mp3, _payload


@pytest.fixture
def long_case(setup, monkeypatch):
    ledger, prepared, sender = setup
    c = ledger.client
    context = {**CONTEXT, 'kind': 'long'}
    previous = plan.item('Önceki bölüm', 'Kaynaklı eski seri')
    entry = plan.item('Belgesel', 'Kaynaklı üç dakikalık belgesel', 'long', depends_on=[previous['id']])
    spec = {'mode': 'production', 'format': 'landscape', 'duration_minutes': 3,
        'publish_after_render': True, 'production_scheduled': True,
        'production_channel_id': CHANNEL, 'production_connection_id': context['connection_id'],
        'content_plan_item_id': entry['id']}
    root = {'task_id': context['lineage_id'], 'kind': 'render', 'parent_id': None,
            'spec': spec, 'state': 'PROGRESS'}
    document = {'version': 1, 'channel_id': CHANNEL, 'revision': str(uuid4()), 'enabled': True,
        'after_queue': 'auto_shorts', 'items': [previous, entry], 'updated_at': entry['created_at']}
    dispatch = {'task_id': context['lineage_id'], 'channel_id': CHANNEL,
        'connection_id': context['connection_id'], 'spec_sha256': plan._sha(spec), 'item': entry}
    c.set(runtime._JOB_PREFIX + context['lineage_id'], plan._raw(root))
    c.set(plan.PLAN_PREFIX + CHANNEL, plan._raw(document))
    c.set(plan.DISPATCH_PREFIX + entry['id'], plan._raw(dispatch))
    c.set(plan.COMPLETION_PREFIX + previous['id'], plan._raw({'public_delivery_verified': True}))
    c.hset(LEDGER_KEY, 'binding:' + context['lineage_id'], plan._raw(context))
    monkeypatch.setattr(runtime, 'configured_ledger', lambda **_: ledger.foundation)
    token = runtime._TASK_ID.set(context['lineage_id'])
    yield ledger, prepared, sender, context, entry, previous, root
    runtime._TASK_ID.reset(token)


def test_exact_queued_long_dispatch_admits_once_and_preserves_existing_receipts(long_case):
    ledger, prepared, sender, context, *_ = long_case
    before = dump(ledger.client)
    assert longform.active() is True
    assert native.generate(prepared, 'editorial', ledger, ledger.foundation, context) == RESULT
    assert native.generate(prepared, 'editorial', ledger, ledger.foundation, context) == RESULT
    sender.assert_called_once()
    after = dump(ledger.client)
    assert all(after[key] == value for key, value in before.items())
    assert included._LAST_OBSERVED.get()['context']['kind'] == 'long'


def test_long_review_capacity_counts_the_complete_existing_lineage(long_case):
    from test_production_included_router import request
    ledger, prepared, sender, context, *_ = long_case
    key = native.PREFIX + 'lineage:' + context['lineage_id']
    ledger.client.sadd(key, *['prior-' + str(i) for i in range(native.MAX_LONG_LINEAGE - 1)])
    assert native.generate(prepared, 'editorial', ledger, ledger.foundation, context) == RESULT
    assert ledger.client.scard(key) == native.MAX_LONG_LINEAGE
    with pytest.raises(SpendBlocked, match='capacity'):
        native.generate(request('Another distinct request'), 'editorial', ledger, ledger.foundation, context)
    sender.assert_called_once()


def test_redis_integral_duration_roundtrip_preserves_original_long_dispatch(long_case):
    ledger, prepared, sender, context, entry, _, root = long_case
    c = ledger.client; key = plan.DISPATCH_PREFIX + entry['id']
    dispatch = json.loads(c.get(key))
    dispatch['spec_sha256'] = plan._sha({**root['spec'], 'duration_minutes': 3.0})
    c.set(key, plan._raw(dispatch)); before = c.get(key)
    assert type(root['spec']['duration_minutes']) is int
    assert longform.active() is True
    assert native.generate(prepared, 'editorial', ledger, ledger.foundation, context) == RESULT
    sender.assert_called_once()
    assert c.get(key) == before


@pytest.mark.parametrize('damage', ['missing_dispatch', 'changed_script', 'previous_unpublished',
                                   'removed_item', 'short_relabelled', 'child_not_root'])
def test_long_authority_cannot_be_inferred_or_forged(long_case, damage):
    ledger, prepared, sender, context, entry, previous, root = long_case
    c = ledger.client
    if damage == 'missing_dispatch': c.delete(plan.DISPATCH_PREFIX + entry['id'])
    if damage == 'previous_unpublished': c.delete(plan.COMPLETION_PREFIX + previous['id'])
    if damage == 'removed_item':
        doc = json.loads(c.get(plan.PLAN_PREFIX + CHANNEL)); doc['items'] = [previous]
        c.set(plan.PLAN_PREFIX + CHANNEL, plan._raw(doc))
    if damage in {'changed_script', 'short_relabelled', 'child_not_root'}:
        if damage == 'changed_script': root['spec']['topic'] = 'different paid scope'
        if damage == 'short_relabelled': root['spec']['format'] = 'shorts'
        if damage == 'child_not_root': root['parent_id'] = str(uuid4())
        c.set(runtime._JOB_PREFIX + context['lineage_id'], plan._raw(root))
    before = dump(c)
    with pytest.raises((SpendBlocked, plan.ContentPlanError)):
        native.generate(prepared, 'editorial', ledger, ledger.foundation, context)
    sender.assert_not_called()
    assert dump(c) == before


def test_full_three_minute_mp3_and_final_words_survive_validation():
    raw = _mp3(180)
    with pytest.raises(SpendBlocked): whisper._snapshot_audio(raw, '.mp3', allow_natural_short=True)
    snapshot = whisper._snapshot_audio(raw, '.mp3', allow_commissioned_long=True)
    assert snapshot.raw == raw
    assert 179000 <= snapshot.samples * 1000 / snapshot.descriptor({})['audio']['decoded_sample_rate'] <= 181000
    result = _payload(duration=180, text='Last sentence.', words=[
        {'word': 'Last', 'start': 177, 'end': 178}, {'word': 'sentence.', 'start': 178, 'end': 179.5}],
        usage={'type': 'duration', 'seconds': 180})
    assert whisper._json_payload(json.dumps(result), maximum_seconds=240) == result
    with pytest.raises(ValueError): whisper._json_payload(json.dumps(result))
    with pytest.raises(SpendBlocked): whisper._snapshot_audio(_mp3(241), '.mp3', allow_commissioned_long=True)


def test_long_prosody_carries_complete_original_audio_and_cannot_use_short_context(long_case):
    from app.services import abacus_router_audio_adapter as adapter, audio_qc
    ledger, _, sender, context, *_ = long_case
    raw = _mp3(180)
    request = adapter.prepare_longform_prosody_request(raw, api_key='offline-key', language='en',
        expected_narration='A complete documentary.', system_instruction=audio_qc._PROSODY_SYSTEM_INSTRUCTION,
        json_schema=audio_qc._PROSODY_REVIEW_SCHEMA)
    body, schema, _ = native._request(request, 'prosody')
    import base64
    assert base64.b64decode(body['contents'][0]['parts'][0]['inlineData']['data']) == raw
    assert schema == audio_qc._PROSODY_REVIEW_SCHEMA
    with pytest.raises(SpendBlocked): native.generate(request, 'prosody', ledger, ledger.foundation, CONTEXT)
    sender.assert_not_called()


def test_documentary_uses_one_continuous_take_with_thirty_aligned_scenes(long_case, monkeypatch, tmp_path):
    from app.services import voice
    # This audio-alignment fixture deliberately has no real credit account or
    # narrator grant. Narrator assignment has separate ledger integration tests.
    from app.services import narrator_rotation
    monkeypatch.setattr(narrator_rotation, 'assigned', lambda language: None)
    scenes = [{'narration': 'The note passes through one careful check before returning to circulation.'}
              for _ in range(30)]
    narration = ' '.join(v['narration'] for v in scenes)
    scale = 179.2 / len(narration)
    alignment = {'characters': list(narration),
        'character_start_times_seconds': [i * scale for i in range(len(narration))],
        'character_end_times_seconds': [(i+1) * scale for i in range(len(narration))]}
    timestamps = Mock(return_value=(_mp3(180), alignment))
    segmented = Mock(side_effect=AssertionError('Unexpected segmented paid synthesis'))
    monkeypatch.setattr(voice, '_selected_voice_or_raise', lambda: {'voice_id':'test-voice','name':'Selected voice'})
    monkeypatch.setattr(voice, 'synthesize_voice_with_timestamps', timestamps)
    monkeypatch.setattr(voice, 'synthesize_voice_with_id', segmented)
    monkeypatch.setattr(voice, 'Path', lambda p: tmp_path if str(p) == '/tmp' else Path(p))
    result = voice.synthesize_scene_sequence(scenes, str(uuid4()), 180, language='en')
    timestamps.assert_called_once(); segmented.assert_not_called()
    assert timestamps.call_args.args[0] == narration
    assert len(result['scene_durations']) == 30
    assert sum(result['scene_durations']) == pytest.approx(result['duration_after_fit'], abs=.1)
    assert result['duration_after_fit'] > 175 and all(4 < v < 8 for v in result['scene_durations'])
    assert Path(result['path']).is_file()


@pytest.mark.parametrize('reject_last', [False, True])
def test_every_long_story_sentence_is_reviewed_and_last_batch_can_block(long_case, monkeypatch, reject_last):
    from app.services import included_research_sources as sources, included_source_passages as passages
    from app.services.director import ProductionContentError
    text = 'Banknotes are printed, distributed, checked, and eventually replaced when they become worn.'
    page = {'url': 'https://www.bep.gov/currency/how-money-is-made', 'text': text,
            'text_sha256': hashlib.sha256(text.encode()).hexdigest()}
    monkeypatch.setattr(sources, 'fetch_page', lambda url: deepcopy(page))
    package = {'scenes': [{'narration': text, 'scene_number': n} for n in range(30)],
               'sources': [{'url': page['url']}], 'narration': ' '.join([text]*30)}
    seen = []
    def review(prompt, schema, *, purpose):
        assert purpose == 'story_review'
        batch = json.loads(prompt.split('EXACT FINAL NARRATION TO AUDIT:\n', 1)[1]); seen.extend(batch)
        checks = schema['properties']['editorial_review']['required']
        return {'editorial_review': {check: True for check in checks}, 'factual_audit': {'sentences': [
            {**row, 'assessment': 'unsupported' if reject_last and len(seen) == 30 and i == 9 else 'supported',
             'reason': 'The retrieved passage contains the complete narrated process.',
             'quotations': [{'passage_id': passages.catalogue([page])[0]['passage_id']}]}
            for i,row in enumerate(batch)]}}
    monkeypatch.setattr(included, 'generate_text_json', review)
    original = deepcopy(package)
    if reject_last:
        with pytest.raises(ProductionContentError): longform.review_story(package, 'Banknote', 'en')
    else:
        result = longform.review_story(package, 'Banknote', 'en')
        assert result['longform_story_qc']['accepted'] is True
        assert result['longform_story_qc']['reviewed_scene_count'] == 30
        assert [v['offset'] for v in result['longform_story_qc']['reviews']] == [0,10,20]
    assert len(seen) == 30 and package == original
