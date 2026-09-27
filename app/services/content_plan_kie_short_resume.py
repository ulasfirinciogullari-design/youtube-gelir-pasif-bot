"""Reassess captured Shorts speech without purchasing another take.

Only an exact locked narration with all three settled Kie/Scribe receipts and
a now-valid first take can continue. Original failures, audio, timestamps and
all financial reservations remain immutable; ordinary full QA still runs.
"""
import json
import re

from app.services import content_plan as plan

ERROR = 'Voice synthesis quality rejected before paid media: ' + json.dumps({
    'generation_attempts': 3, 'quality_errors': [
        {'generation_attempt': i, 'reason': 'Blind speech timing does not match the scene narration'}
        for i in range(3)]}, separators=(',', ':'))


def spoken(source):
    from app.services.voice import normalize_turkish_tts
    spec = source['spec']; prefix = 'Spoken narration must be exactly: '
    plan._require(spec['language'] == 'tr' and spec['topic'].startswith(prefix))
    text, _ = json.JSONDecoder().raw_decode(spec['topic'][len(prefix):])
    plan._require(type(text) is str and 1 <= len(text) <= 1500)
    scenes = re.split(r'(?<=[.!?])\s+', text.strip())
    plan._require(len(scenes) == 6)
    return [normalize_turkish_tts(line, ensure_terminal=i == 5) for i, line in enumerate(scenes)]


def captured(pipe, source, context, choice, rows):
    from app.services import kie_voice_ledger as kie, kie_gemini_voice as gemini
    from app.services import commissioning_audio as primary, commissioning_scribe as scribe
    from app.services import audio_qc as qc
    from app.services.word_timed_narration import edit_plan
    scenes = spoken(source)
    body, _ = gemini.request_body(' '.join(scenes), choice['voice_id'], language='tr')
    plan._require(len(rows) == 3 and sorted(r['descriptor']['attempt'] for _, r in rows) == [0, 1, 2])
    _, journal = primary._read(pipe)
    # Unknown primary recognition must never be concealed by secondary speech.
    plan._require(not any(r['context'] == context for r in journal['requests'].values()))
    recognitions = []
    for count, key in enumerate(pipe.scan_iter(match=scribe.PREFIX + '*', count=100)):
        plan._require(count < 2000)
        pipe.watch(key); encoded = pipe.get(key); row = plan._object(encoded)
        if row.get('context') != context:
            continue
        plan._require(pipe.pttl(key) == -1 and row.get('outcome') is not None
            and key == scribe.PREFIX + kie.sha(scribe._raw({'context': context, 'request': row['request']})))
        payload = qc._response_payload(scribe._response(row['outcome']), 'ElevenLabs')
        recognitions.append((row, payload))
    plan._require(len(recognitions) == 3)
    proofs = {}; matched = set(); first_plan = None
    for identity, row in sorted(rows, key=lambda entry: entry[1]['descriptor']['attempt']):
        descriptor = row['descriptor']
        plan._require(row['create'] is not None and row['result'] is not None
            and descriptor['model'] == gemini.MODEL and descriptor['voice_id'] == choice['voice_id']
            and descriptor['request_sha256'] == kie.sha(kie.raw(body))
            and kie.restore(row['result']).json()['data']['state'] == 'success')
        key = kie.PREFIX + 'media:' + identity; pipe.watch(key)
        media = plan._object(pipe.get(key))
        plan._require(pipe.pttl(key) == -1 and media['request_identity'] == identity
            and media['result_receipt_sha256'] == row['result']['response_sha256']
            and media['audio']['sha256'] == media['mp3_sha256'])
        matches = [(r, p) for r, p in recognitions if r['request']['audio'] == media['audio']]
        plan._require(len(matches) == 1 and media['mp3_sha256'] not in matched)
        matched.add(media['mp3_sha256']); recognition, payload = matches[0]
        if descriptor['attempt'] == 0:
            evidence = {**payload, 'provider': 'elevenlabs', 'language': payload.get('language_code')}
            seconds = media['audio']['decoded_samples'] / media['audio']['decoded_sample_rate']
            first_plan = edit_plan(scenes, evidence, seconds, language='tr')
        proofs[identity] = {'voice_receipt_sha256': plan._sha(row), 'media_sha256': plan._sha(media),
            'blind_asr_sha256': plan._sha(recognition)}
    plan._require(first_plan is not None)
    return {'takes': proofs, 'first_take_timing_sha256': plan._sha(first_plan),
        'original_take_limit': 3, 'new_allocation': 0}
