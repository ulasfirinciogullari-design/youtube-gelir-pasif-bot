"""A separately authorized Gemini option within the original Kie allocation.

The existing ElevenLabs policy and eight-probe ceiling are immutable. This
extension permits two distinct Gemini checks, charging the SAME Kie journal.
2026-09-24 Kie list rates: 140/2800 credits per million input/audio tokens.
Upstream Gemini 3.1 Flash TTS limits: 8192 input, 16384 output tokens. Reserve
48 credits for the full limits; record the actual provider charge separately.
"""
from datetime import datetime, timezone
import json
import re

from app.services.production_spend import SpendBlocked

MODEL = 'google/gemini-3-1-flash-tts'
KEY = 'youtube_studio:{production_spend}:kie_voice:v1:gemini_extension'
REPAIR_KEY = KEY + ':schema_repair'
SPEC = {'model': MODEL, 'input_token_limit': 8192, 'output_token_limit': 16384,
        'input_credits_per_million': 140, 'output_credits_per_million': 2800,
        'maximum_microcredits': 48_000_000, 'max_connection_probes': 2,
        'price_review_date': '2026-09-24'}
VOICES = frozenset({'Fenrir', 'Kore', 'Puck', 'Charon'})
DUB_LANGUAGES = {'es': 'neutral Spanish', 'pt': 'Brazilian Portuguese',
                 'hi': 'standard Hindi', 'ar': 'Modern Standard Arabic'}


def require(value):
    if not value:
        raise SpendBlocked('kie_gemini_voice_unverified')


def _legacy_body(text, voice='Fenrir', *, language):
    require(language in {'tr', 'en'} and type(voice) is str and voice in VOICES and type(text) is str)
    require(text.strip() and len(text.encode('utf-16-le')) // 2 <= 5000)
    # Only the dialogue is spoken. Instructions describe delivery, never a
    # celebrity impersonation or a real person's identity.
    body = {'model': MODEL, 'input': {
        'temperature': 1,
        'scene': 'A single narrator recording a professional business documentary in a quiet studio.',
        'sample_context': 'Read the dialogue verbatim. No introductions, commentary, music or sound effects.',
        'speakers': [{'speaker_id': 'Speaker 1', 'voice_name': voice,
            'audio_profile': 'A clear, confident adult documentary narrator with natural expression.',
            'accent': 'Native standard Turkish' if language == 'tr' else 'Standard American English',
            'style': 'Engaging, clear and conversational. Precise pronunciation and restrained emotion.',
            'pace': 'Natural, brisk documentary pace with short pauses at punctuation.'}],
        'dialogue_turns': [{'speaker_id': 'Speaker 1', 'text': text}]}}
    return body, SPEC['maximum_microcredits']


def request_body(text, voice='Fenrir', *, language):
    if language in DUB_LANGUAGES:
        body, ceiling = request_body(text, voice, language='tr')
        body['input']['speakers'][0]['audio_profile'] = (
            'A clear, confident adult documentary narrator with natural expression. Native '
            + DUB_LANGUAGES[language] + ' pronunciation.')
        body['input']['sample_context'] = ('Read the dialogue verbatim in ' + DUB_LANGUAGES[language]
            + '. No introductions, commentary, music or sound effects.')
        return body, ceiling
    body, ceiling = _legacy_body(text, voice, language=language)
    speaker = body['input']['speakers'][0]
    # These are enums in Kie's published schema, not free-form directions.
    speaker.update(accent='Neutral' if language == 'tr' else 'American (Gen)',
                   style='Newscaster', pace='Natural')
    speaker['audio_profile'] += (' Native standard Turkish pronunciation.' if language == 'tr'
                                else ' Standard American English pronunciation.')
    return body, ceiling


def describe(body):
    """Only our exact single-narrator request shape has a reviewed quote."""
    require(type(body) is dict and body.get('model') == MODEL)
    try:
        text = body['input']['dialogue_turns'][0]['text']
        require(type(body['input']['temperature']) in (int, float))
        speaker = body['input']['speakers'][0]
        voice = speaker['voice_name']
        accent = speaker['accent']
        require(accent in {'Neutral', 'American (Gen)'})
        language = 'tr' if accent == 'Neutral' else 'en'
        if accent == 'Neutral':
            for candidate in DUB_LANGUAGES:
                expected, _ = request_body(text, voice, language=candidate)
                if body == expected:
                    language = candidate
                    break
        expected, ceiling = request_body(text, voice, language=language)
        require(body == expected)
    except (KeyError, IndexError, TypeError):
        raise SpendBlocked('kie_gemini_voice_unverified') from None
    return text, voice, ceiling, language


def read(pipe, policy):
    from app.services import kie_voice_ledger as ledger
    pipe.watch(KEY)
    encoded = pipe.get(KEY)
    if encoded is None:
        return None
    require(pipe.pttl(KEY) == -1 and len(encoded) < 8192)
    record = json.loads(encoded)
    require(type(record) is dict and set(record) == {
        'version', 'purpose', 'policy_sha256', 'spec', 'authorized_at', 'owner_evidence_sha256'})
    require(type(record['version']) is int and record['version'] == 1
        and record['purpose'] == 'funded_kie_gemini_voice_validation'
        and record['policy_sha256'] == ledger.sha(ledger.raw(policy)) and record['spec'] == SPEC
        and type(record['owner_evidence_sha256']) is str
        and re.fullmatch('[0-9a-f]{64}', record['owner_evidence_sha256']))
    ledger._stamp(record['authorized_at'])
    return record


def commission(foundation, *, owner_evidence_sha256):
    """Operator-only model extension. No new allocation, reset or activation."""
    from app.services import kie_voice_ledger as ledger
    require(type(owner_evidence_sha256) is str and re.fullmatch('[0-9a-f]{64}', owner_evidence_sha256))
    with foundation.client.pipeline() as pipe:
        policy, journal, _ = ledger._read(pipe)
        require(policy is not None and read(pipe, policy) is None)
        ledger._foundation(pipe, foundation)
        require(pipe.get(ledger.ACTIVE_KEY) is None and all(r['result'] is not None
            for r in journal['requests'].values()))
        require(ledger._used(journal) + 2 * SPEC['maximum_microcredits'] <= policy['allocation_microcredits'])
        for channel, connection in policy['channels'].items():
            ledger._binding(pipe, channel, connection)
        record = {'version': 1, 'purpose': 'funded_kie_gemini_voice_validation',
            'policy_sha256': ledger.sha(ledger.raw(policy)), 'spec': SPEC,
            'authorized_at': datetime.now(timezone.utc).isoformat(),
            'owner_evidence_sha256': owner_evidence_sha256}
        encoded = ledger.raw(record)
        pipe.multi(); pipe.set(KEY, encoded, nx=True)
        require(pipe.execute() == [True])
    require(foundation.client.get(KEY) in (encoded, encoded.encode()))
    return {'status': 'gemini_validation_authorized', 'allocation_added_microcredits': 0,
            'maximum_probes': 2, 'maximum_reserved_microcredits': 2 * SPEC['maximum_microcredits']}


def rejected_style(row):
    """Only the observed explicit pre-creation schema rejection qualifies."""
    from app.services import kie_voice_ledger as ledger
    if not (row['descriptor']['model'] == MODEL and row['descriptor']['attempt'] == 0
            and row['create'] is not None and row['result'] is None):
        return False
    response = ledger.restore(row['create'])
    payload = response.json()
    return response.status_code == 200 and type(payload) is dict and payload.get('code') == 422 \
        and payload.get('msg') == 'The style parameter is invalid' and payload.get('data') is None


def commission_schema_repairs(foundation, *, owner_evidence_sha256):
    """One corrected body for each rejected check; no receipt/refund/cap reset."""
    from app.services import kie_voice_ledger as ledger
    require(type(owner_evidence_sha256) is str and re.fullmatch('[0-9a-f]{64}', owner_evidence_sha256))
    with foundation.client.pipeline() as pipe:
        policy, journal, _ = ledger._read(pipe)
        require(policy is not None and read(pipe, policy) is not None)
        ledger._foundation(pipe, foundation)
        pipe.watch(REPAIR_KEY)
        require(pipe.get(REPAIR_KEY) is None and pipe.get(ledger.ACTIVE_KEY) is None)
        rows = {k: v for k, v in journal['requests'].items() if v['descriptor']['model'] == MODEL}
        require(len(rows) == 2 and all(v['scope']['kind'] == 'connection_probe'
            and rejected_style(v) for v in rows.values()))
        require(ledger._used(journal) + 2 * SPEC['maximum_microcredits'] <= policy['allocation_microcredits'])
        record = {'version': 1, 'purpose': 'correct_rejected_gemini_schema_once',
            'policy_sha256': ledger.sha(ledger.raw(policy)),
            'originals': {k: ledger.sha(ledger.raw(v)) for k, v in rows.items()},
            'owner_evidence_sha256': owner_evidence_sha256,
            'authorized_at': datetime.now(timezone.utc).isoformat()}
        pipe.multi(); pipe.set(REPAIR_KEY, ledger.raw(record), nx=True)
        require(pipe.execute() == [True])
    return {'status': 'schema_correction_authorized', 'additional_allocation': 0, 'maximum_corrections': 2}


def schema_repair_allowed(pipe, policy, same_scope, body, attempt):
    if attempt != 1 or len(same_scope) != 1 or not rejected_style(same_scope[0]):
        return False
    from app.services import kie_voice_ledger as ledger
    pipe.watch(REPAIR_KEY)
    encoded = pipe.get(REPAIR_KEY)
    require(encoded is not None and pipe.pttl(REPAIR_KEY) == -1)
    record = json.loads(encoded)
    require(type(record) is dict and record.get('version') == 1
        and record.get('purpose') == 'correct_rejected_gemini_schema_once'
        and record.get('policy_sha256') == ledger.sha(ledger.raw(policy)))
    original = same_scope[0]
    identity = ledger.sha(ledger.raw({'scope': original['scope'], 'request': original['descriptor']}))
    require(record.get('originals', {}).get(identity) == ledger.sha(ledger.raw(original)))
    text, voice, _, language = describe(body)
    legacy, _ = _legacy_body(text, voice, language=language)
    require(original['descriptor']['request_sha256'] == ledger.sha(ledger.raw(legacy)))
    return True
