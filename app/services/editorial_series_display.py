"""Owner-audited display numbering; never rewrites historical series allocations."""
from datetime import datetime, timezone
import hashlib
import json
import re
from uuid import UUID


RECEIPT_PREFIX = 'youtube_studio:editorial_series_display:v1:'
CLAIM_PREFIX = 'youtube_studio:editorial_series_display_claim:v1:'
INTENT_FIELDS = {'number', 'total', 'reason', 'predecessor_video_id',
                 'expected_channel_id', 'expected_connection_id', 'expected_profile_revision'}


class SeriesDisplayError(ValueError):
    """Fixed safe error at the owner boundary; underlying credentials stay private."""


def _require(condition):
    if not condition:
        raise SeriesDisplayError('editorial_series_display_invalid_or_unavailable')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _digest(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _object(raw):
    value = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    _require(type(value) is dict)
    return value


def validate_series_display_intent(value):
    _require(type(value) is dict and set(value) == INTENT_FIELDS)
    _require(type(value['number']) is int and type(value['total']) is int
             and 2 <= value['number'] <= value['total'] <= 10000
             and value['reason'] == 'owner_renumbered_predecessor')
    for key in ('expected_channel_id', 'expected_connection_id', 'expected_profile_revision'):
        _require(type(value[key]) is str and re.fullmatch(r'[A-Za-z0-9_-]{8,128}', value[key]))
    _require(type(value['predecessor_video_id']) is str
             and re.fullmatch(r'[A-Za-z0-9_-]{11}', value['predecessor_video_id']))
    return dict(value)


def _binding(source, intent):
    spec, result = source.get('spec', {}), source.get('result', {})
    _require(spec.get('workflow') == 'external_import'
             and result.get('quality_disposition') == 'editorial_review_pass'
             and result.get('manual_qa_required') is False
             and spec.get('publish_after_render') is True
             and spec.get('production_channel_id') == intent['expected_channel_id']
             and spec.get('production_connection_id') == intent['expected_connection_id']
             and spec.get('production_profile_revision') == intent['expected_profile_revision']
             and re.fullmatch(r'[a-f0-9]{64}', str(result.get('editorial_review_sha256') or '')))


def _claim_key(channel_id, series_id, number):
    return f'{CLAIM_PREFIX}{channel_id}:{series_id}:{number}'


def _verified_predecessor(intent):
    from googleapiclient.discovery import build
    from app.services.youtube_auth import load_credentials

    credentials = load_credentials(intent['expected_channel_id'],
        expected_connection_id=intent['expected_connection_id'], refresh=True)
    _require(credentials is not None)
    youtube = build('youtube', 'v3', credentials=credentials, cache_discovery=False)
    channels = youtube.channels().list(part='id', mine=True, maxResults=1).execute(num_retries=0)
    _require([item.get('id') for item in channels.get('items', [])]
             == [intent['expected_channel_id']])
    response = youtube.videos().list(part='id,snippet,status',
        id=intent['predecessor_video_id'], maxResults=1).execute(num_retries=0)
    items = response.get('items', [])
    _require(len(items) == 1)
    item, suffix = items[0], f"({intent['number'] - 1}/{intent['total']})"
    _require(item.get('id') == intent['predecessor_video_id']
             and item.get('snippet', {}).get('channelId') == intent['expected_channel_id']
             and item.get('status', {}).get('privacyStatus') == 'public'
             and str(item.get('snippet', {}).get('title') or '').endswith(suffix))
    return {'video_id': item['id'], 'channel_id': item['snippet']['channelId'],
            'title': item['snippet']['title'], 'privacy_status': 'public'}


def _validate_receipt(reader, source, receipt):
    intent = validate_series_display_intent(receipt.get('intent'))
    _binding(source, intent)
    _require(receipt.get('version') == 1 and receipt.get('source_task_id') == source['task_id']
             and receipt.get('editorial_review_sha256') == source['result']['editorial_review_sha256']
             and receipt.get('receipt_sha256') == _digest({k: v for k, v in receipt.items() if k != 'receipt_sha256'})
             and type(receipt.get('series_id')) is str
             and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', receipt['series_id']))
    proof = receipt.get('predecessor_proof', {})
    _require(proof.get('video_id') == intent['predecessor_video_id']
             and proof.get('channel_id') == intent['expected_channel_id']
             and proof.get('privacy_status') == 'public'
             and str(proof.get('title') or '').endswith(f"({intent['number'] - 1}/{intent['total']})")
             and reader.get(_claim_key(intent['expected_channel_id'], receipt['series_id'], intent['number']))
             == source['task_id'])
    return receipt


def create_series_display_receipt(source_task_id, intent):
    """Called only after owner authentication and actual editorial review.

    One immutable receipt and one scope claim. An existing upload cannot acquire
    a display correction, and a second source cannot claim this displayed part.
    """
    from app.services import external_editorial_review as review
    from app.services.external_master_ingest import _redis
    from app.services.youtube_automation import PROFILE_PREFIX, SERIES_ASSIGNMENT_PREFIX
    from app.services.youtube_publish_state import EXECUTION_LOCK_PREFIX

    try:
        intent = validate_series_display_intent(intent)
        _require(str(UUID(source_task_id)) == source_task_id)
        client = _redis()
        source = _object(client.get(review.JOB_PREFIX + source_task_id))
        _binding(source, intent)
        review.validate_editorial_publication(source)
        profile_key = PROFILE_PREFIX + intent['expected_channel_id']
        profile = _object(client.get(profile_key))
        series_id = profile.get('series_id')
        _require(type(series_id) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', series_id)
                 and profile.get('profile_revision') == intent['expected_profile_revision']
                 and profile.get('channel_id') == intent['expected_channel_id']
                 and type(profile.get('series_total')) is int and profile['series_total'] == intent['total'])
        receipt_key = RECEIPT_PREFIX + source_task_id
        claim_key = _claim_key(intent['expected_channel_id'], series_id, intent['number'])
        execution_key = EXECUTION_LOCK_PREFIX + source_task_id
        assignment_key = SERIES_ASSIGNMENT_PREFIX + intent['expected_channel_id'] + ':' + series_id + ':' + source_task_id
        existing = client.get(receipt_key)
        if existing is not None:
            receipt = _validate_receipt(client, source, _object(existing))
            _require(receipt['intent'] == intent and receipt['series_id'] == series_id)
            return receipt
        # Public API read only. It is not a caller-provided claim about a title.
        proof = _verified_predecessor(intent)
        receipt = {'version': 1, 'source_task_id': source_task_id, 'intent': intent,
                   'series_id': series_id, 'editorial_review_sha256': source['result']['editorial_review_sha256'],
                   'predecessor_proof': proof, 'created_at': datetime.now(timezone.utc).isoformat()}
        receipt['receipt_sha256'] = _digest(receipt)
        with client.pipeline() as pipe:
            pipe.watch(*review._keys(source), receipt_key, claim_key, execution_key, assignment_key)
            # Existing QA, authority, OAuth generation, and source proof remain
            # authoritative and are watched through this separate receipt write.
            review._validate(pipe, source)
            _require(_object(pipe.get(profile_key)) == profile
                     and pipe.get(review.UPLOAD_PREFIX + source_task_id) is None)
            existing = pipe.get(receipt_key)
            if existing is not None:
                stored = _validate_receipt(pipe, source, _object(existing))
                _require(stored['intent'] == intent and stored['series_id'] == series_id)
                return stored
            current = _object(pipe.get(review.JOB_PREFIX + source_task_id))
            _require(pipe.get(claim_key) is None and pipe.get(execution_key) is None
                     and pipe.get(assignment_key) is None
                     and not current.get('publish_task_id')
                     and not current.get('result', {}).get('youtube')
                     and not current.get('result', {}).get('youtube_automation', {}).get('publish_task_id'))
            pipe.multi()
            pipe.set(receipt_key, _json(receipt), nx=True)
            pipe.set(claim_key, source_task_id, nx=True)
            _require(pipe.execute() == [True, True])
        return receipt
    except Exception:
        raise SeriesDisplayError('editorial_series_display_invalid_or_unavailable') from None


def get_series_display_receipt(source, profile):
    from app.services.external_master_ingest import _redis
    try:
        client = _redis()
        raw = client.get(RECEIPT_PREFIX + source['task_id'])
        if raw is None:
            return None
        receipt = _validate_receipt(client, source, _object(raw))
        intent = receipt['intent']
        _require(receipt['series_id'] == profile.get('series_id')
                 and profile.get('series_total') == intent['total']
                 and profile.get('profile_revision') == intent['expected_profile_revision'])
        return receipt
    except Exception:
        raise SeriesDisplayError('editorial_series_display_invalid_or_unavailable') from None


def series_display_watch_keys(source, frozen_plan):
    if frozen_plan is None:
        return []
    if 'series_display' not in frozen_plan:
        return [RECEIPT_PREFIX + source['task_id']]
    display = frozen_plan['series_display']
    _require(type(display) is dict)
    return [RECEIPT_PREFIX + source['task_id'], _claim_key(
        source['spec']['production_channel_id'], (frozen_plan.get('series') or {}).get('id'), display.get('number'))]


def validate_series_display_plan(source, frozen_plan, *, reader=None):
    """Read-only publisher validation, including after private insert.

    Pass the publisher's WATCH reader to bind the immutable receipt to the same
    optimistic transaction that verifies editorial authorization.
    """
    from app.services.external_master_ingest import _redis
    try:
        if frozen_plan is None:
            return
        reader = reader or _redis()
        raw = reader.get(RECEIPT_PREFIX + source['task_id'])
        if 'series_display' not in frozen_plan:
            _require(raw is None)
            return
        display = frozen_plan['series_display']
        _require(type(display) is dict and set(display) == {'number', 'total', 'reason', 'predecessor_video_id', 'receipt_sha256'})
        receipt = _validate_receipt(reader, source, _object(raw))
        intent, series = receipt['intent'], frozen_plan.get('series') or {}
        _require(display == {**{k: intent[k] for k in ('number', 'total', 'reason', 'predecessor_video_id')},
                             'receipt_sha256': receipt['receipt_sha256']}
                 and frozen_plan.get('source_task_id') == source['task_id']
                 and frozen_plan.get('target_channel_id') == intent['expected_channel_id']
                 and frozen_plan.get('profile_revision') == intent['expected_profile_revision']
                 and series.get('id') == receipt['series_id'] and series.get('total') == intent['total']
                 and type(series.get('number')) is int and intent['number'] <= series['number'] <= intent['total']
                 and str(frozen_plan.get('title') or '').endswith(f"({intent['number']}/{intent['total']})")
                 and str(frozen_plan.get('description') or '').startswith(
                     f"{series.get('name') or series['id']} · {intent['number']}/{intent['total']}\n\n"))
    except Exception:
        raise SeriesDisplayError('editorial_series_display_invalid_or_unavailable') from None
