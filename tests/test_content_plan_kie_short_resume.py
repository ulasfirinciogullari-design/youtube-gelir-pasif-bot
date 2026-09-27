from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import fakeredis
import pytest

from app.services import content_plan_kie_short_resume as short, content_plan as plan
from app.services import kie_voice_ledger as kie, kie_gemini_voice as gemini
from app.services import commissioning_scribe as scribe, commissioning_audio as primary, youtube_auth
from app.services.production_spend import SpendBlocked
from app.services.voice import VoiceQualityError
from app.services.audio_qc import AudioQCError
from test_content_plan_attention import dump


@pytest.fixture
def recorded(monkeypatch):
    c = fakeredis.FakeRedis(decode_responses=True)
    fixture = json.loads((Path(__file__).parent / 'fixtures/qr_retained_scribe.json').read_text())
    text = ' '.join(fixture['scenes']); source = {'spec': {'language': 'tr',
        'topic': 'Spoken narration must be exactly: ' + json.dumps(text)}}
    context = {'lineage_id': 'qr-root', 'connection_id': 'current', 'channel_id': 'capital', 'kind': 'shorts'}
    choice = {'voice_id': 'Fenrir'}; rows = []; keys = []
    request, _ = gemini.request_body(text, choice['voice_id'], language='tr')
    monkeypatch.setattr(primary, '_read', lambda pipe: ({}, {'requests': {}}))
    monkeypatch.setattr(youtube_auth, '_decrypt_json', json.loads)
    def receipt(value):
        raw = json.dumps(value)
        return {'http_status': 200, 'observed_at': datetime.now(timezone.utc).isoformat(),
            'encrypted_response': json.dumps({'response': raw}), 'response_sha256': kie.sha(raw)}
    # Fixture stores genuine successful take 0, successful take 2, failed take 1.
    for attempt, observed in enumerate([fixture['recognitions'][i] for i in (0, 2, 1)]):
        identity = str(attempt) * 64; audio = observed['audio']
        result = receipt({'data': {'state': 'success'}})
        row = {'scope': context, 'descriptor': {'attempt': attempt, 'model': gemini.MODEL,
            'voice_id': choice['voice_id'], 'request_sha256': kie.sha(kie.raw(request))},
            'create': receipt({'data': {'taskId': str(attempt)}}), 'result': result}
        rows.append((identity, row))
        c.set(kie.PREFIX + 'media:' + identity, plan._raw({'request_identity': identity,
            'result_receipt_sha256': result['response_sha256'], 'mp3_sha256': audio['sha256'], 'audio': audio}))
        record = {'context': context, 'request': {'audio': audio}, 'outcome': receipt(observed['response'])}
        key = scribe.PREFIX + kie.sha(scribe._raw({'context': context, 'request': record['request']}))
        c.set(key, plan._raw(record)); keys.append(key)
    return c, source, context, choice, rows, keys


def test_actual_retained_first_take_resumes_without_writes_or_new_allocation(recorded):
    c, source, context, choice, rows, _ = recorded; before = dump(c)
    with c.pipeline() as pipe:
        pipe.watch(kie.POLICY_KEY)  # Caller enters WATCH mode while reading the journal.
        proof = short.captured(pipe, source, context, choice, rows)
        pipe.multi(); pipe.ping(); assert pipe.execute() == [True]
    assert len(proof['takes']) == 3 and proof['new_allocation'] == 0
    assert proof['original_take_limit'] == 3 and dump(c) == before


@pytest.mark.parametrize('damage', ['unknown_tts', 'unknown_scribe', 'unknown_primary', 'changed_script',
    'changed_voice', 'wrong_first_take', 'modified_audio', 'missing_asr', 'modified_receipt', 'extra_take'])
def test_unknown_changed_or_inaccurate_evidence_never_resumes(recorded, monkeypatch, damage):
    c, source, context, choice, rows, keys = recorded
    if damage == 'unknown_tts': rows[1][1]['result'] = None
    if damage == 'unknown_scribe':
        row = json.loads(c.get(keys[1])); row['outcome'] = None; c.set(keys[1], plan._raw(row))
    if damage == 'unknown_primary': monkeypatch.setattr(primary, '_read', lambda p: ({}, {'requests': {'unknown': {'context': context}}}))
    if damage == 'changed_script': source['spec']['topic'] = source['spec']['topic'].replace('Denso', 'Başka')
    if damage == 'changed_voice': choice['voice_id'] = 'Kore'
    if damage == 'wrong_first_take':
        rows[0][1]['descriptor']['attempt'], rows[1][1]['descriptor']['attempt'] = 1, 0
    if damage == 'modified_audio':
        key = kie.PREFIX + 'media:' + rows[0][0]; row = json.loads(c.get(key))
        row['audio']['decoded_samples'] -= 100; c.set(key, plan._raw(row))
    if damage == 'missing_asr': c.delete(keys[2])
    if damage == 'modified_receipt':
        row = json.loads(c.get(keys[0])); row['outcome']['response_sha256'] = 'f' * 64; c.set(keys[0], plan._raw(row))
    if damage == 'extra_take': rows.append(deepcopy(rows[0]))
    before = dump(c)
    with pytest.raises((ValueError, TypeError, SpendBlocked, VoiceQualityError, AudioQCError)):
        with c.pipeline() as pipe:
            pipe.watch(kie.POLICY_KEY)
            short.captured(pipe, source, context, choice, rows)
    assert dump(c) == before
