"""Explicit, bounded post-publication dub preparation on the existing Kie ledger.

An immutable owner-authorized plan permits one synthesis per language. These
are additional audio tracks for the original public video, never new uploads
or replacement render identities. Prepared audio is not proof of YouTube upload.
"""
import json
import re

from app.services import kie_voice_ledger as ledger, kie_gemini_voice as gemini
from app.services import video_localization as localization

PREFIX = 'youtube_studio:video_dubbing:v1:'
INDEX = PREFIX + 'videos'
LANGUAGES = ('en', 'es', 'pt', 'hi', 'ar')


def _key(video):
    ledger.require(type(video) is str and localization._VIDEO.fullmatch(video), 'dub_video_invalid')
    return PREFIX + video


def _read(client, video):
    key = _key(video)
    if hasattr(client, 'watch'):
        client.watch(key, key + ':sha256')
    encoded = client.get(key)
    ledger.require(encoded is not None and client.pttl(key) == -1, 'dub_plan_missing')
    ledger.require(client.get(key + ':sha256') == ledger.sha(encoded), 'dub_plan_changed')
    plan = json.loads(encoded)
    ledger.require(plan.get('version') == 1 and plan.get('video_id') == video, 'dub_plan_invalid')
    return plan


def scope(plan, language):
    ledger.require(language in plan['languages'], 'dub_language_not_planned')
    return {'kind': 'video_dub', 'channel_id': plan['channel_id'],
        'connection_id': plan['connection_id'], 'source_task_id': plan['source_task_id'],
        'video_id': plan['video_id'], 'language': language, 'plan_sha256': ledger.sha(ledger.raw(plan))}


def _source(client, plan):
    from app.services import youtube_automation as profiles
    profile_key = profiles.PROFILE_PREFIX + plan['channel_id']
    if hasattr(client, 'watch'):
        client.watch(profile_key)
    profile = json.loads(client.get(profile_key) or '{}')
    ledger.require(profile.get('production_enabled') is True and profile.get('auto_publish') is True,
        'dub_channel_paused')
    source, receipt = localization._source(client, plan['source_task_id'])
    ledger.require(receipt['youtube_video_id'] == plan['video_id']
        and receipt['target_channel_id'] == plan['channel_id']
        and localization._current_connection(client, plan['channel_id']) == plan['connection_id']
        and ledger.sha(ledger.raw({'spec': source['spec'],
            'caption_key': source['result']['caption_key'],
            'duration': source['result']['duration']})) == plan['source_sha256'], 'dub_source_changed')
    return source


def prepare(foundation, source_id, languages, *, owner_evidence_sha256):
    """Operator admission; no paid request and no cash/prepaid allocation added."""
    ledger.require(type(owner_evidence_sha256) is str
        and re.fullmatch('[0-9a-f]{64}', owner_evidence_sha256), 'dub_owner_authority_missing')
    ledger.require(type(languages) is dict and set(languages) == set(LANGUAGES), 'dub_languages_invalid')
    for language, row in languages.items():
        ledger.require(type(row) is dict and set(row) == {'sentences', 'voice_id'}
            and row['voice_id'] in gemini.VOICES and type(row['sentences']) is list
            and 1 <= len(row['sentences']) <= 10
            and all(type(text) is str and 1 <= len(text) <= 300 and '\n' not in text
                and not any(c in text for c in '<>\x00') for text in row['sentences']), 'dub_script_invalid')
        body, _ = gemini.request_body(' '.join(row['sentences']), row['voice_id'], language=language)
        ledger.require(len(body['input']['dialogue_turns'][0]['text']) <= 1600, 'dub_script_too_long')
    with foundation.client.pipeline() as pipe:
        policy, journal, _ = ledger._read(pipe)
        ledger.require(policy is not None and gemini.read(pipe, policy) is not None, 'dub_funding_missing')
        ledger._foundation(pipe, foundation)
        source, receipt = localization._source(pipe, source_id)
        channel, video = receipt['target_channel_id'], receipt['youtube_video_id']
        connection = localization._current_connection(pipe, channel)
        ledger.require(channel in ledger.CHANNELS and ledger.channel_connection(pipe, policy, channel) == connection)
        ledger._binding(pipe, channel, connection)
        duration = source['result']['duration']
        ledger.require(type(duration) in (int, float) and 5 <= duration <= 40, 'dub_short_duration_invalid')
        ledger.require(ledger._used(journal) + len(languages) * gemini.SPEC['maximum_microcredits']
            <= policy['allocation_microcredits'], 'kie_voice_balance_exhausted')
        key = _key(video); pipe.watch(key, key + ':sha256')
        ledger.require(not pipe.exists(key, key + ':sha256'), 'dub_plan_already_exists')
        plan = {'version': 1, 'video_id': video, 'channel_id': channel, 'connection_id': connection,
            'source_task_id': source_id, 'duration': duration, 'source_language': source['spec']['language'],
            'source_sha256': ledger.sha(ledger.raw({'spec': source['spec'],
                'caption_key': source['result']['caption_key'], 'duration': duration})),
            'title': source['result'].get('title') or source['result'].get('publish_metadata', {}).get('title') or '',
            'languages': languages, 'owner_evidence_sha256': owner_evidence_sha256,
            'created_at': ledger.now().isoformat(), 'maximum_syntheses': len(languages),
            'youtube_upload_authorized_by_this_plan': False}
        encoded = ledger.raw(plan)
        _source(pipe, plan)
        pipe.multi(); pipe.set(key, encoded, nx=True); pipe.set(key + ':sha256', ledger.sha(encoded), nx=True)
        pipe.sadd(INDEX, video)
        ledger.require(pipe.execute()[:2] == [True, True], 'dub_plan_write_uncertain')
    return plan


def authorize_scope(pipe, policy, context):
    plan = _read(pipe, context.get('video_id'))
    ledger.require(context == scope(plan, context.get('language')), 'dub_scope_changed')
    _source(pipe, plan)
    ledger.require(plan['channel_id'] in ledger.CHANNELS
        and ledger.channel_connection(pipe, policy, plan['channel_id']) == plan['connection_id'])
    ledger._binding(pipe, plan['channel_id'], plan['connection_id'])
    return plan


def authorize_request(pipe, policy, context, descriptor):
    plan = authorize_scope(pipe, policy, context)
    row = plan['languages'][context['language']]
    body, ceiling = gemini.request_body(' '.join(row['sentences']), row['voice_id'], language=context['language'])
    ledger.require(descriptor['attempt'] == 0 and descriptor['voice_id'] == row['voice_id']
        and descriptor['model'] == gemini.MODEL and descriptor['request_sha256'] == ledger.sha(ledger.raw(body))
        and descriptor['ceiling_microcredits'] == ceiling, 'dub_request_changed')


def generate(foundation, video, language):
    """Resume the same provider task/bytes. Never create a second paid attempt."""
    from app.services import kie_voice_adapter as api, kie_credentials, kie_voice_media
    plan = _read(foundation.client, video)
    row = plan['languages'].get(language)
    ledger.require(row is not None, 'dub_language_not_planned')
    _source(foundation.client, plan)
    body, ceiling = gemini.request_body(' '.join(row['sentences']), row['voice_id'], language=language)
    journal = ledger.Journal(foundation, scope(plan, language), body, ceiling)
    credential = kie_credentials.read(foundation.client)
    ledger.require(credential is not None, 'kie_voice_key_missing')
    result = api.generate(body, credential.api_key, journal)
    kie_voice_media.mp3(foundation.client, journal.identity, result, longform=False)
    media = json.loads(foundation.client.get(ledger.PREFIX + 'media:' + journal.identity))
    record = {'version': 1, 'video_id': video, 'language': language,
        'plan_sha256': ledger.sha(ledger.raw(plan)), 'request_identity': journal.identity,
        'status': 'generated', 'generated_at': ledger.now().isoformat(),
        'original_key': media['original_key'], 'original_sha256': media['original_sha256'],
        'source_seconds': media['source_seconds'], 'youtube_status': 'not_uploaded'}
    key = _key(video) + ':track:' + language
    if not foundation.client.set(key, ledger.raw(record), nx=True):
        previous = json.loads(foundation.client.get(key))
        ledger.require(all(previous.get(k) == record[k] for k in (
            'plan_sha256', 'request_identity', 'original_key', 'original_sha256')), 'dub_track_conflict')
        return previous
    return record


def dashboard(client):
    result = []
    for video in sorted(client.smembers(INDEX))[:100]:
        plan = _read(client, video)
        result.append({**plan, 'tracks': {language: json.loads(client.get(_key(video) + ':track:' + language) or '{}')
            for language in plan['languages']}})
    return result
