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
SPEC = {'model': MODEL, 'input_token_limit': 8192, 'output_token_limit': 16384,
        'input_credits_per_million': 140, 'output_credits_per_million': 2800,
        'maximum_microcredits': 48_000_000, 'max_connection_probes': 2,
        'price_review_date': '2026-09-24'}
VOICES = frozenset({'Fenrir', 'Kore', 'Puck', 'Charon'})


def require(value):
    if not value:
        raise SpendBlocked('kie_gemini_voice_unverified')


def request_body(text, voice='Fenrir', *, language):
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


def describe(body):
    """Only our exact single-narrator request shape has a reviewed quote."""
    require(type(body) is dict and body.get('model') == MODEL)
    try:
        text = body['input']['dialogue_turns'][0]['text']
        require(type(body['input']['temperature']) in (int, float))
        speaker = body['input']['speakers'][0]
        voice = speaker['voice_name']
        accent = speaker['accent']
        require(accent in {'Native standard Turkish', 'Standard American English'})
        language = 'tr' if accent == 'Native standard Turkish' else 'en'
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
