"""Verified prepaid narration for explicitly granted new channel roots.

Permanent root choices and native-ledger WATCH guards prevent two providers
from buying narration for the same root. Old native work keeps its old route.
"""
import json
import re

from app.config import settings
from app.services import kie_voice_ledger as ledger, kie_gemini_voice as gemini
from app.services.included_stock_pool import _local_transaction

ROOT_PREFIX = ledger.PREFIX + 'root:'
QUALIFICATION_PREFIX = ledger.PREFIX + 'qualification:'


def _proof(pipe, language, identity):
    """Re-evaluate retained real provider responses; never trust a pass flag."""
    from app.services import youtube_auth, audio_qc as qc, whisper_transcription as whisper
    from app.services.gemini_generation import _decode_gemini_json_response
    from app.services.word_timed_narration import edit_plan
    import httpx
    keys = {kind: QUALIFICATION_PREFIX + identity + ':' + kind for kind in ('media', 'whisper', 'prosody')}
    pipe.watch(*keys.values())
    encoded = {kind: pipe.get(key) for kind, key in keys.items()}
    ledger.require(all(value is not None and pipe.pttl(keys[kind]) == -1 for kind, value in encoded.items()),
                   'kie_voice_qualification_missing')
    records = {kind: json.loads(value) for kind, value in encoded.items()}
    media, asr, prosody = (records[k] for k in ('media', 'whisper', 'prosody'))
    ledger.require(media['request_identity'] == asr['request_identity'] == prosody['request_identity'] == identity
        and media['language'] == asr['language'] == language
        and asr['route'] == whisper.WHISPER_ROUTE and asr['model'] == 'whisper-1'
        and asr['audio_sha256'] == prosody['original_audio_sha256'] == media['audio']['sha256'])
    def response(row):
        outcome = row['outcome']
        ledger.require(outcome is not None)
        raw = youtube_auth._decrypt_json(outcome['encrypted_response'])['response'].encode()
        ledger.require(ledger.sha(raw) == outcome['response_sha256'])
        return raw
    transcription = whisper._json_payload(response(asr))
    text = media['expected_text']
    comparison = qc._require_word_timing_evidence(qc.compare_transcript(text, transcription['text'],
        words=transcription['words'], provider='openai', comparison_language=language), 'OpenAI')
    ledger.require(comparison['pass'] is True, 'kie_voice_asr_rejected')
    duration = media['audio']['decoded_samples'] / media['audio']['decoded_sample_rate']
    plan = edit_plan(re.split(r'(?<=[.!?])\s+', text.strip()), transcription, duration, language=language)
    raw = response(prosody)
    from app.services.commissioning_reasoning import MODEL, ENDPOINT
    ledger.require(prosody['model'] == MODEL and prosody['route'] == ENDPOINT
        and prosody['outcome']['http_status'] == 200 and json.loads(raw)['modelVersion'].startswith(MODEL))
    output = _decode_gemini_json_response(httpx.Response(200, content=raw), qc._PROSODY_REVIEW_SCHEMA)
    review = qc._validate_prosody_review(output, text, audio_duration_seconds=duration,
        transcript_evidence=comparison, language=language)
    ledger.require(review is not None and review['pass'] is True, 'kie_voice_prosody_rejected')
    return {kind: ledger.sha(value) for kind, value in encoded.items()}, media, plan


def activation(pipe, policy):
    pipe.watch(ledger.ACTIVE_KEY)
    encoded = pipe.get(ledger.ACTIVE_KEY)
    if encoded is None:
        return None
    record = json.loads(encoded)
    ledger.require(pipe.pttl(ledger.ACTIVE_KEY) == -1 and type(record) is dict and set(record) == {
        'version', 'purpose', 'policy_sha256', 'model', 'voices', 'authorized_at', 'owner_evidence_sha256'}
        and type(record['version']) is int and record['version'] == 1
        and record['purpose'] == 'verified_kie_voice_production' and record['model'] == gemini.MODEL
        and record['policy_sha256'] == ledger.sha(ledger.raw(policy))
        and set(record['voices']) == {'tr', 'en'}, 'kie_voice_activation_unverified')
    ledger._stamp(record['authorized_at'])
    for language, voice in record['voices'].items():
        ledger.require(set(voice) == {'voice_id', 'probe_identity', 'proof_sha256'}
            and voice['voice_id'] in gemini.VOICES and re.fullmatch('[0-9a-f]{64}', voice['probe_identity']))
        keys = {kind: QUALIFICATION_PREFIX + voice['probe_identity'] + ':' + kind
                for kind in ('media', 'whisper', 'prosody')}
        pipe.watch(*keys.values())
        ledger.require(all(pipe.get(key) is not None and pipe.pttl(key) == -1
            and ledger.sha(pipe.get(key)) == voice['proof_sha256'].get(kind) for kind, key in keys.items()),
            'kie_voice_qualification_changed')
    return record


def commission(foundation, probes, *, owner_evidence_sha256):
    """Operator-only activation of two actual qualified voices, no new money."""
    ledger.require(set(probes) == {'tr', 'en'} and re.fullmatch('[0-9a-f]{64}', owner_evidence_sha256))
    with foundation.client.pipeline() as pipe:
        policy, journal, _ = ledger._read(pipe)
        ledger.require(policy is not None and activation(pipe, policy) is None)
        ledger._foundation(pipe, foundation)
        voices = {}
        for language, identity in probes.items():
            row = journal['requests'][identity]
            ledger.require(row['scope']['kind'] == 'connection_probe' and row['scope']['language'] == language
                and row['descriptor']['model'] == gemini.MODEL and row['result'] is not None)
            proof, media, _ = _proof(pipe, language, identity)
            voice = row['descriptor']['voice_id']
            body, _ = gemini.request_body(media['expected_text'], voice, language=language)
            ledger.require(ledger.sha(ledger.raw(body)) == row['descriptor']['request_sha256']
                and media['result_receipt_sha256'] == row['result']['response_sha256'])
            voices[language] = {'voice_id': voice, 'probe_identity': identity, 'proof_sha256': proof}
        for channel, connection in policy['channels'].items():
            ledger._binding(pipe, channel, connection)
        record = {'version': 1, 'purpose': 'verified_kie_voice_production', 'model': gemini.MODEL,
            'policy_sha256': ledger.sha(ledger.raw(policy)), 'voices': voices,
            'authorized_at': ledger.now().isoformat(), 'owner_evidence_sha256': owner_evidence_sha256}
        pipe.multi(); pipe.set(ledger.ACTIVE_KEY, ledger.raw(record), nx=True)
        ledger.require(pipe.execute() == [True])
    return {'status': 'active', 'languages': sorted(voices), 'allocation_added_microcredits': 0}


def native_guard(pipe, root):
    key = ROOT_PREFIX + root
    pipe.watch(key)
    ledger.require(pipe.get(key) is None, 'kie_voice_root_provider_pinned')


def _native_intents(pipe, foundation):
    from app.services.production_credit_ledger import CreditLedger
    from app.services.production_credit_periods import recorded_intents
    native = CreditLedger(foundation.client, foundation=foundation, clock=foundation.clock)
    native._watch(pipe)
    policy, state, _, _ = native._read(pipe, foundation.clock(), historical=True)
    return recorded_intents(native, pipe, policy, state, foundation.clock())


def _choice(pipe, active, context, language):
    key = ROOT_PREFIX + context['lineage_id']
    pipe.watch(key)
    encoded = pipe.get(key)
    if encoded is None:
        return None
    record = json.loads(encoded)
    ledger.require(pipe.pttl(key) == -1 and record['context'] == context and record['language'] == language
        and record['activation_sha256'] == ledger.sha(ledger.raw(active))
        and record['voice_id'] == active['voices'][language]['voice_id'] and record['model'] == gemini.MODEL,
        'kie_voice_root_provider_pinned')
    return record


@_local_transaction
def select(language):
    from app.services import production_spend_runtime as runtime, narrator_rotation as rotation
    if language not in {'tr', 'en'} or not runtime.enforcement_enabled() or not runtime._TASK_ID.get():
        return None
    foundation = runtime.configured_ledger(read_timeout=3)
    context = runtime.resolve_context(foundation.client, runtime._TASK_ID.get())
    from app.services import framecase_kie_voice
    if context['channel_id'] not in ledger.CHANNELS | {framecase_kie_voice.CHANNEL_ID}:
        return None
    with foundation.client.pipeline() as pipe:
        pipe.watch(ledger.ACTIVE_KEY, ROOT_PREFIX + context['lineage_id'])
        if pipe.get(ledger.ACTIVE_KEY) is None:
            ledger.require(pipe.get(ROOT_PREFIX + context['lineage_id']) is None, 'kie_voice_activation_missing')
            pipe.multi(); pipe.ping(); ledger.require(pipe.execute() == [True])
            return None
        policy, journal, _ = ledger._read(pipe)
        ledger._foundation(pipe, foundation)
        connection = ledger.channel_connection(pipe, policy, context['channel_id'])
        if connection is None:
            ledger.require(pipe.get(ROOT_PREFIX + context['lineage_id']) is None, 'kie_voice_channel_not_authorized')
            pipe.multi(); pipe.ping(); ledger.require(pipe.execute() == [True])
            return None
        ledger.require(connection == context['connection_id'], 'kie_voice_connection_changed')
        if context['channel_id'] == framecase_kie_voice.CHANNEL_ID:
            if context['kind'] != 'shorts':
                ledger.require(pipe.get(ROOT_PREFIX + context['lineage_id']) is None,
                               'framecase_kie_long_not_qualified')
                pipe.multi(); pipe.ping(); ledger.require(pipe.execute() == [True])
                return None
            ledger.require(language == 'en', 'framecase_kie_language_unverified')
            framecase_kie_voice.authorize_context(pipe, context)
        ledger._binding(pipe, context['channel_id'], context['connection_id'])
        active = activation(pipe, policy)
        previous = _choice(pipe, active, context, language)
        native_key = rotation.PREFIX + 'root:' + context['lineage_id']
        pipe.watch(native_key)
        has_native = pipe.get(native_key) is not None or any(entry['reservation']['intent']['root_lineage_id']
            == context['lineage_id'] for entry in _native_intents(pipe, foundation))
        if has_native:
            ledger.require(previous is None, 'kie_voice_root_provider_conflict')
            pipe.multi(); pipe.ping(); ledger.require(pipe.execute() == [True])
            return None
        if previous is None:
            ledger.require(ledger._used(journal) + gemini.SPEC['maximum_microcredits'] <= policy['allocation_microcredits'],
                           'kie_voice_balance_exhausted')
            previous = {'version': 1, 'context': context, 'language': language, 'model': gemini.MODEL,
                'voice_id': active['voices'][language]['voice_id'], 'activation_sha256': ledger.sha(ledger.raw(active))}
            pipe.multi(); pipe.set(ROOT_PREFIX + context['lineage_id'], ledger.raw(previous), nx=True)
            ledger.require(pipe.execute() == [True])
        else:
            pipe.multi(); pipe.ping(); ledger.require(pipe.execute() == [True])
    return previous


def authorize_request(pipe, foundation, policy, context, descriptor):
    active = activation(pipe, policy)
    ledger.require(active is not None, 'kie_voice_validation_required')
    # The stored route binds the original root, channel, language and voice.
    encoded = pipe.get(ROOT_PREFIX + context['lineage_id'])
    ledger.require(encoded is not None, 'kie_voice_root_provider_pinned')
    selected = json.loads(encoded)
    _choice(pipe, active, context, selected['language'])
    ledger.require(descriptor['voice_id'] == selected['voice_id'] and descriptor['model'] == selected['model'])
    from app.services import narrator_rotation as rotation
    key = rotation.PREFIX + 'root:' + context['lineage_id']; pipe.watch(key)
    ledger.require(pipe.get(key) is None and not any(entry['reservation']['intent']['root_lineage_id']
        == context['lineage_id'] for entry in _native_intents(pipe, foundation)), 'kie_voice_root_provider_conflict')


def capacity(pipe, foundation, channel_id, *, kind=None):
    pipe.watch(ledger.ACTIVE_KEY)
    from app.services.framecase_kie_voice import CHANNEL_ID
    if channel_id == CHANNEL_ID and kind != 'shorts':
        return None
    if pipe.get(ledger.ACTIVE_KEY) is None or channel_id not in ledger.CHANNELS | {CHANNEL_ID}:
        return None
    policy, journal, _ = ledger._read(pipe)
    connection = ledger.channel_connection(pipe, policy, channel_id)
    if connection is None:
        return None
    ledger._foundation(pipe, foundation)
    ledger._binding(pipe, channel_id, connection)
    ledger.require(activation(pipe, policy) is not None)
    _native_intents(pipe, foundation)
    remaining = policy['allocation_microcredits'] - ledger._used(journal)
    ledger.require(remaining >= 3 * gemini.SPEC['maximum_microcredits'], 'kie_voice_balance_exhausted')
    return {'voice_provider': 'kie', 'voice_model': gemini.MODEL, 'available_kie_microcredits': remaining}


def _timing_evidence(path, text, *, language):
    """Recover recognition on the same audio before considering a new take.

    The normal speech gate reuses the immutable Whisper receipt and, only
    when needed, admits one independently accounted blind Scribe observation.
    Neither recognizer receives a script hint. Unknown or malformed evidence
    cannot become a synthesis defect that purchases another voice take.
    """
    from app.services import audio_qc
    review = audio_qc.verify_audio_narration(path, text, language=language)
    ledger.require(review.get('available') is True
        and review.get('independent_recognizer_unavailable') is not True,
        'kie_voice_timing_outcome_unverified')
    audio_qc._require_word_timing_evidence(review, 'Independent recognizer')
    ledger.require(review.get('provider') in {'openai', 'elevenlabs', 'gemini'},
                   'kie_voice_timing_provider_unverified')
    return {'text': review['transcript'], 'words': review['word_timestamps'],
            'language': review.get('language_code'), 'provider': review['provider']}


def synthesize(text, choice, *, attempt, work):
    from app.services import production_spend_runtime as runtime, kie_voice_adapter as api
    from app.services import kie_voice_media
    foundation = runtime.configured_ledger(read_timeout=3)
    context = runtime.resolve_context(foundation.client, runtime._TASK_ID.get())
    ledger.require(context == choice['context'])
    body, ceiling = gemini.request_body(text, choice['voice_id'], language=choice['language'])
    journal = ledger.Journal(foundation, context, body, ceiling, attempt=attempt)
    credential = ledger.credentials.read(foundation.client)
    result = api.generate(body, credential.api_key, journal)
    audio = kie_voice_media.mp3(foundation.client, journal.identity, result, longform=context['kind'] == 'long')
    path = work / 'kie_original.mp3'; path.write_bytes(audio)
    evidence = _timing_evidence(path, text, language=choice['language'])
    return audio, evidence
