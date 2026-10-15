import json

import pytest

from app.services import kie_voice_production as production, kie_voice_ledger as ledger
from app.services import kie_gemini_voice as gemini, production_spend_runtime as runtime
from app.services import narrator_rotation
from app.services.production_spend import SpendBlocked
from test_kie_gemini_voice import setup, extension
from test_kie_voice import kie, box, KEY


@pytest.fixture
def active(kie, monkeypatch):
    setup(kie); extension(kie)
    channel, connection = next(iter(json.loads(kie.client.get(ledger.POLICY_KEY))['channels'].items()))
    kie.context = {'kind': 'shorts', 'lineage_id': 'b0cc6d94-8d40-40d5-a970-a58c29b32550',
        'channel_id': channel, 'connection_id': connection}
    monkeypatch.setattr(runtime, 'configured_ledger', lambda **kw: kie.foundation)
    monkeypatch.setattr(runtime, 'enforcement_enabled', lambda: True)
    monkeypatch.setattr(runtime, 'resolve_context', lambda client, task: dict(kie.context))
    monkeypatch.setattr(production, '_native_intents', lambda pipe, foundation: kie.native)
    kie.native = []
    voices = {}
    for language, voice in [('tr', 'Fenrir'), ('en', 'Kore')]:
        identity = ('a' if language == 'tr' else 'b') * 64
        proofs = {}
        for kind in ('media', 'whisper', 'prosody'):
            raw = ledger.raw({'test_fixture': language, 'kind': kind})
            kie.client.set(production.QUALIFICATION_PREFIX + identity + ':' + kind, raw)
            proofs[kind] = ledger.sha(raw)
        voices[language] = {'voice_id': voice, 'probe_identity': identity, 'proof_sha256': proofs}
    policy = json.loads(kie.client.get(ledger.POLICY_KEY))
    kie.activation = {'version': 1, 'purpose': 'verified_kie_voice_production', 'model': gemini.MODEL,
        'policy_sha256': ledger.sha(ledger.raw(policy)), 'voices': voices,
        'authorized_at': ledger.now().isoformat(), 'owner_evidence_sha256': 'a' * 64}
    kie.client.set(ledger.ACTIVE_KEY, ledger.raw(kie.activation))
    token = runtime._TASK_ID.set(kie.context['lineage_id'])
    try:
        yield kie
    finally:
        runtime._TASK_ID.reset(token)


def test_new_root_is_permanently_bound_before_any_voice_request(active):
    first = production.select('tr')
    assert first == production.select('tr')
    assert first['voice_id'] == 'Fenrir' and first['model'] == gemini.MODEL
    assert json.loads(active.client.get(production.ROOT_PREFIX + active.context['lineage_id'])) == first
    assert [r.method for r in active.requests] == ['GET']
    with active.client.pipeline() as pipe, pytest.raises(SpendBlocked, match='provider_pinned'):
        production.native_guard(pipe, active.context['lineage_id'])


@pytest.mark.parametrize('prior', ['native_intent', 'native_narrator'])
def test_old_native_unknown_or_assignment_keeps_its_provider_without_new_spend(active, prior):
    if prior == 'native_intent':
        active.native.append({'reservation': {'intent': {'root_lineage_id': active.context['lineage_id']}},
                              'settlement': None})
    else:
        active.client.set(narrator_rotation.PREFIX + 'root:' + active.context['lineage_id'], 'retained')
    assert production.select('tr') is None
    assert not active.client.exists(production.ROOT_PREFIX + active.context['lineage_id'])
    assert [r.method for r in active.requests] == ['GET']


def test_kie_assignment_cannot_fall_back_if_activation_removed(active):
    production.select('tr'); active.client.delete(ledger.ACTIVE_KEY)
    with pytest.raises(SpendBlocked, match='activation_missing'):
        production.select('tr')


def test_different_language_cannot_change_existing_root_voice(active):
    production.select('tr')
    with pytest.raises(SpendBlocked, match='provider_pinned'):
        production.select('en')


def test_peer_channel_never_uses_this_route(active):
    active.context['channel_id'] = 'UCs93z6wf134H5_BL9pkQX4Q'
    assert production.select('en') is None
    assert not active.client.exists(production.ROOT_PREFIX + active.context['lineage_id'])


def test_changed_qualification_blocks_before_any_paid_request(active):
    active.client.set(production.QUALIFICATION_PREFIX + 'a' * 64 + ':whisper', 'changed')
    with pytest.raises(SpendBlocked, match='qualification_changed'):
        production.select('tr')
    assert [r.method for r in active.requests] == ['GET']


def test_unrelated_native_unknown_is_preserved_and_does_not_move_to_kie(active):
    active.native.append({'reservation': {'intent': {'root_lineage_id': 'unrelated-peer-root'}},
                          'settlement': None})
    assert production.select('tr')['voice_id'] == 'Fenrir'
    assert active.native[0]['settlement'] is None


def test_journal_checks_permanent_route_before_send(active):
    from app.services import kie_voice_adapter as api
    body, ceiling = gemini.request_body('Bir fikir büyür.', language='tr')
    with pytest.raises(SpendBlocked, match='provider_pinned'):
        api.generate(body, KEY, ledger.Journal(active.foundation, active.context, body, ceiling))
    assert [r.method for r in active.requests] == ['GET']
    production.select('tr')
    result = api.generate(body, KEY, ledger.Journal(active.foundation, active.context, body, ceiling), sleep=lambda _: None)
    again = api.generate(body, KEY, ledger.Journal(active.foundation, active.context, body, ceiling), sleep=lambda _: None)
    assert result == again
    assert [r.method for r in active.requests].count('POST') == 1


def test_native_guard_watch_detects_concurrent_kie_assignment(active):
    from redis.exceptions import WatchError
    with active.client.pipeline() as pipe:
        production.native_guard(pipe, active.context['lineage_id'])
        production.select('tr')
        pipe.multi(); pipe.set('native-send-test', 'must-not-commit')
        with pytest.raises(WatchError): pipe.execute()
    assert not active.client.exists('native-send-test')


def test_forged_probe_summary_cannot_activate_production(active):
    active.client.delete(ledger.ACTIVE_KEY)
    before = active.client.get(ledger.JOURNAL_KEY)
    with pytest.raises((KeyError, SpendBlocked)):
        production.commission(active.foundation, {'tr': 'a' * 64, 'en': 'b' * 64},
            owner_evidence_sha256='f' * 64)
    assert not active.client.exists(ledger.ACTIVE_KEY)
    assert active.client.get(ledger.JOURNAL_KEY) == before


def test_full_voice_orchestration_uses_kie_words_and_keeps_final_quality_unapproved(tmp_path, monkeypatch):
    from pathlib import Path
    from unittest.mock import Mock
    from app.services import voice
    actual_path = Path
    monkeypatch.setattr(voice, 'Path', lambda value: tmp_path if str(value) == '/tmp' else actual_path(value))
    choice = {'voice_id': 'Fenrir', 'model': gemini.MODEL}
    monkeypatch.setattr(production, 'select', lambda language: choice)
    native = Mock(side_effect=AssertionError('Native provider must never run for a Kie root'))
    monkeypatch.setattr(voice, 'synthesize_voice_with_timestamps', native)
    monkeypatch.setattr(narrator_rotation, 'assigned', native)
    transcript = {'text': 'Bir fikir büyür. Sonra dünyaya yayılır.', 'words': [
        {'word': word, 'start': index * .5, 'end': index * .5 + .4}
        for index, word in enumerate('Bir fikir büyür. Sonra dünyaya yayılır.'.split())]}
    generate = Mock(return_value=(b'accepted-kie-audio', transcript))
    monkeypatch.setattr(production, 'synthesize', generate)
    monkeypatch.setattr(voice, '_media_duration', lambda path: 24.)
    def normalize(command, **kwargs): actual_path(command[-1]).write_bytes(b'normalized-complete-audio')
    monkeypatch.setattr(voice.subprocess, 'run', normalize)
    result = voice.synthesize_scene_sequence([{'narration': 'Bir fikir büyür.'},
        {'narration': 'Sonra dünyaya yayılır.'}], 'offline-kie-render', 30,
        language='tr', flexible_short=True, natural_timeline=True)
    assert result['voice_model'] == gemini.MODEL and result['voice_name'] == 'Fenrir'
    assert result['voice_language_code'] == 'tr'
    assert sum(result['scene_durations']) == pytest.approx(24)
    assert not result.get('audio_qc') and not result.get('publish_eligible')
    assert generate.call_count == 1 and native.call_count == 0
