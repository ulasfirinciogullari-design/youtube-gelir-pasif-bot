from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
import unicodedata
from typing import Any, Iterable
from uuid import uuid4

import redis

from app.config import settings


PROFILE_PREFIX = 'youtube_studio:youtube_profile:v1:'
PROFILE_INDEX = 'youtube_studio:youtube_profiles:v1'
SERIES_COUNTER_PREFIX = 'youtube_studio:youtube_series_counter:v1:'
SERIES_ASSIGNMENT_PREFIX = 'youtube_studio:youtube_series_assignment:v1:'

_ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]{6,128}$')
_SERIES_ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]{1,80}$')
_LANGUAGE_PATTERN = re.compile(r'^[a-z]{2,3}(?:-[a-z0-9]{2,8})?$')
_CATEGORY_PATTERN = re.compile(r'^[0-9]{1,3}$')
_STORAGE_KEY_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._/-]{0,511}$')

_SAVE_PROFILE = '''
local current = redis.call('GET', KEYS[1])
if not current then current = '' end
if current ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[1], ARGV[2])
redis.call('SADD', KEYS[2], ARGV[3])
return 1
'''

_RESERVE_SERIES_NUMBER = '''
local current = redis.call('GET', KEYS[2])
if current then return tonumber(current) end
local next_value = redis.call('INCR', KEYS[1])
local maximum = tonumber(ARGV[1])
if maximum > 0 and next_value > maximum then
  redis.call('DECR', KEYS[1])
  return -1
end
redis.call('SET', KEYS[2], tostring(next_value))
return next_value
'''


class YouTubeAutomationError(RuntimeError):
    pass


class ProfileConflictError(YouTubeAutomationError):
    pass


class MetadataValidationError(YouTubeAutomationError):
    pass


class SeriesExhaustedError(YouTubeAutomationError):
    pass


def _redis() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_id(value: str, name: str = 'channel_id') -> str:
    value = str(value or '').strip()
    if not _ID_PATTERN.fullmatch(value):
        raise ValueError(f'{name} is invalid')
    return value


def _profile_key(channel_id: str) -> str:
    return PROFILE_PREFIX + _safe_id(channel_id)


def _one_line(value: Any, maximum: int) -> str:
    return ' '.join(str(value or '').split())[:maximum]


def _string_list(
    values: Iterable[Any] | str | None,
    *,
    maximum_items: int,
    maximum_length: int,
    strip_hash: bool = False,
) -> list[str]:
    if isinstance(values, str):
        values = re.split(r'[,\n]+', values)
    values = values or []
    output: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = _one_line(raw, maximum_length)
        if strip_hash:
            value = value.lstrip('#').strip()
        key = value.casefold()
        if value and key not in seen:
            output.append(value)
            seen.add(key)
        if len(output) >= maximum_items:
            break
    return output


def _items(value: Iterable[Any] | str | None) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        return list(re.split(r'[,\n]+', value))
    return list(value)


def _hashtags(values: Iterable[Any] | str | None) -> list[str]:
    cleaned = []
    for value in _items(values):
        tag = re.sub(r'[^\w]+', '', str(value or '').lstrip('#'), flags=re.UNICODE)[:60]
        if tag:
            cleaned.append(tag)
    return _string_list(cleaned, maximum_items=15, maximum_length=60)


def _language(value: Any, fallback: str = 'tr') -> str:
    normalized = str(value or '').strip().replace('_', '-').casefold()
    if not _LANGUAGE_PATTERN.fullmatch(normalized):
        normalized = str(fallback or '').strip().replace('_', '-').casefold()
    if not _LANGUAGE_PATTERN.fullmatch(normalized):
        raise ValueError('default_language is invalid')
    return normalized


def _profile_payload(channel_id: str, value: dict[str, Any]) -> dict[str, Any]:
    channel_id = _safe_id(channel_id)
    default_language = _language(value.get('default_language') or 'tr')
    languages = [
        _language(item, default_language)
        for item in _string_list(
            value.get('languages') or [default_language],
            maximum_items=12,
            maximum_length=24,
        )
    ]
    if default_language not in languages:
        languages.insert(0, default_language)

    release_mode = str(value.get('release_mode') or 'private').strip().casefold()
    if release_mode not in {'private', 'public', 'scheduled'}:
        raise ValueError('release_mode is invalid')
    try:
        schedule_delay_minutes = int(value.get('schedule_delay_minutes') or 60)
    except (TypeError, ValueError) as exc:
        raise ValueError('schedule_delay_minutes is invalid') from exc
    schedule_delay_minutes = max(15, min(schedule_delay_minutes, 60 * 24 * 30))

    category_id = str(value.get('category_id') or '28').strip()
    if not _CATEGORY_PATTERN.fullmatch(category_id):
        raise ValueError('category_id is invalid')
    series_id = _one_line(value.get('series_id'), 80)
    if series_id and not _SERIES_ID_PATTERN.fullmatch(series_id):
        raise ValueError('series_id is invalid')
    try:
        series_total = int(value.get('series_total') or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError('series_total is invalid') from exc
    if not 0 <= series_total <= 10000:
        raise ValueError('series_total is invalid')

    return {
        'schema_version': 1,
        'channel_id': channel_id,
        'profile_revision': uuid4().hex,
        'channel_identity': _one_line(value.get('channel_identity'), 240),
        'route_label': _one_line(value.get('route_label'), 120),
        'topic_keywords': _string_list(
            value.get('topic_keywords'),
            maximum_items=30,
            maximum_length=80,
        ),
        'languages': languages,
        'default_language': default_language,
        'default_tags': _string_list(
            value.get('default_tags'),
            maximum_items=30,
            maximum_length=100,
        ),
        'hashtags': _hashtags(value.get('hashtags')),
        'category_id': category_id,
        'description_footer': str(value.get('description_footer') or '').strip()[:1200],
        'series_id': series_id,
        'series_name': _one_line(value.get('series_name'), 100),
        'series_total': series_total,
        'auto_publish': bool(value.get('auto_publish')),
        'release_mode': release_mode,
        'schedule_delay_minutes': schedule_delay_minutes,
        'require_thumbnail': bool(value.get('require_thumbnail')),
        'updated_at': _now().isoformat(),
    }


def get_channel_profile(channel_id: str) -> dict[str, Any] | None:
    try:
        raw = _redis().get(_profile_key(channel_id))
    except Exception as exc:
        raise YouTubeAutomationError('YouTube channel profiles are unavailable') from exc
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise YouTubeAutomationError('YouTube channel profile is inconsistent') from exc
    if not isinstance(value, dict) or value.get('channel_id') != str(channel_id):
        raise YouTubeAutomationError('YouTube channel profile is inconsistent')
    return value


def list_channel_profiles() -> list[dict[str, Any]]:
    try:
        client = _redis()
        channel_ids = sorted(str(value) for value in client.smembers(PROFILE_INDEX))
        profiles = []
        for channel_id in channel_ids:
            raw = client.get(_profile_key(channel_id))
            if not raw:
                continue
            value = json.loads(raw)
            if isinstance(value, dict) and value.get('channel_id') == channel_id:
                profiles.append(value)
        return profiles
    except Exception as exc:
        raise YouTubeAutomationError('YouTube channel profiles are unavailable') from exc


def save_channel_profile(
    channel_id: str,
    value: dict[str, Any],
    *,
    expected_revision: str | None = None,
) -> dict[str, Any]:
    channel_id = _safe_id(channel_id)
    payload = _profile_payload(channel_id, value)
    key = _profile_key(channel_id)
    try:
        client = _redis()
        current_raw = client.get(key)
        current = json.loads(current_raw) if current_raw else None
        if expected_revision is None and current_raw:
            raise ProfileConflictError('YouTube channel profile changed concurrently')
        if expected_revision is not None:
            revision = current.get('profile_revision') if isinstance(current, dict) else None
            if revision != expected_revision:
                raise ProfileConflictError('YouTube channel profile changed concurrently')
        encoded = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
        saved = client.eval(
            _SAVE_PROFILE,
            2,
            key,
            PROFILE_INDEX,
            current_raw or '',
            encoded,
            channel_id,
        )
    except ProfileConflictError:
        raise
    except Exception as exc:
        raise YouTubeAutomationError('YouTube channel profile could not be saved') from exc
    if not saved:
        raise ProfileConflictError('YouTube channel profile changed concurrently')
    return payload


def automated_quality_approved(source_job: dict[str, Any]) -> bool:
    result = source_job.get('result')
    return bool(
        source_job.get('state') == 'SUCCESS'
        and source_job.get('kind') == 'render'
        and isinstance(result, dict)
        and result.get('video_key')
        and result.get('quality_disposition') == 'automated_qc_pass'
        and result.get('manual_qa_required') is False
    )


def _search_text(value: Any) -> str:
    text = unicodedata.normalize('NFKD', str(value or '').casefold())
    return ' '.join(''.join(char for char in text if not unicodedata.combining(char)).split())


def select_channel_profile(
    source_job: dict[str, Any],
    profiles: Iterable[dict[str, Any]],
    *,
    connected_channel_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    spec = source_job.get('spec') if isinstance(source_job.get('spec'), dict) else {}
    result = source_job.get('result') if isinstance(source_job.get('result'), dict) else {}
    language = _language(spec.get('language') or 'tr')
    route_label = _search_text(spec.get('channel_id'))
    topic = _search_text(spec.get('topic') or result.get('title'))
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for profile in profiles:
        channel_id = str(profile.get('channel_id') or '')
        if connected_channel_ids is not None and channel_id not in connected_channel_ids:
            continue
        if not profile.get('auto_publish'):
            continue
        languages = {
            _language(item, language)
            for item in (profile.get('languages') or [profile.get('default_language') or language])
        }
        if language not in languages:
            continue
        expected_label = _search_text(profile.get('route_label'))
        exact_label = bool(route_label and expected_label and route_label == expected_label)
        keywords = [_search_text(item) for item in profile.get('topic_keywords') or []]
        overlaps = sum(1 for keyword in keywords if keyword and keyword in topic)
        if keywords and not overlaps and not exact_label:
            continue
        score = (1000 if exact_label else 0) + overlaps * 10
        if not expected_label and not keywords:
            score += 1
        ranked.append((score, channel_id, profile))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (-item[0], item[1]))
    if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
        return None
    return ranked[0][2]


def reserve_series_number(
    channel_id: str,
    series_id: str,
    source_task_id: str,
    *,
    total: int = 0,
) -> int:
    channel_id = _safe_id(channel_id)
    source_task_id = _safe_id(source_task_id, 'source_task_id')
    series_id = str(series_id or '').strip()
    if not _SERIES_ID_PATTERN.fullmatch(series_id):
        raise ValueError('series_id is invalid')
    total = int(total or 0)
    if not 0 <= total <= 10000:
        raise ValueError('series_total is invalid')
    scope = f'{channel_id}:{series_id}'
    try:
        number = int(_redis().eval(
            _RESERVE_SERIES_NUMBER,
            2,
            SERIES_COUNTER_PREFIX + scope,
            SERIES_ASSIGNMENT_PREFIX + scope + ':' + source_task_id,
            total,
        ))
    except Exception as exc:
        raise YouTubeAutomationError('YouTube series numbering is unavailable') from exc
    if number < 1:
        raise SeriesExhaustedError('YouTube series is complete')
    return number


def _storage_key(value: Any) -> str | None:
    key = str(value or '').strip()
    if not key:
        return None
    if '..' in key.split('/') or not _STORAGE_KEY_PATTERN.fullmatch(key):
        raise MetadataValidationError('Thumbnail storage key is invalid')
    return key


def _truncate_title(base: str, suffix: str) -> str:
    suffix = _one_line(suffix, 100)
    if not suffix:
        return _one_line(base, 100)
    room = max(1, 100 - len(suffix) - 1)
    return f'{_one_line(base, room)} {suffix}'[:100]


def build_publish_plan(
    source_task_id: str,
    source_job: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    source_task_id = _safe_id(source_task_id, 'source_task_id')
    result = source_job.get('result') if isinstance(source_job.get('result'), dict) else {}
    spec = source_job.get('spec') if isinstance(source_job.get('spec'), dict) else {}
    raw_metadata = result.get('publish_metadata')
    if not isinstance(raw_metadata, dict):
        raise MetadataValidationError('Publish metadata was not generated')
    title = _one_line(raw_metadata.get('title') or result.get('title'), 100)
    description = str(raw_metadata.get('description') or '').strip()
    if not title or not description:
        raise MetadataValidationError('Publish title and description are required')

    series_number = None
    series_id = str(profile.get('series_id') or '')
    series_total = int(profile.get('series_total') or 0)
    series_name = _one_line(profile.get('series_name'), 100)
    if series_id:
        series_number = reserve_series_number(
            str(profile.get('channel_id') or ''),
            series_id,
            source_task_id,
            total=series_total,
        )
        fraction = f'{series_number}/{series_total}' if series_total else str(series_number)
        title = _truncate_title(title, f'({fraction})')
        series_line = f'{series_name or series_id} · {fraction}'
        description = f'{series_line}\n\n{description}'

    sources = _string_list(
        raw_metadata.get('sources'),
        maximum_items=20,
        maximum_length=400,
    )
    if sources:
        description += '\n\nKaynaklar:\n' + '\n'.join(sources)
    footer = str(profile.get('description_footer') or '').strip()
    if footer:
        description += '\n\n' + footer
    hashtags = _hashtags(
        [*_items(raw_metadata.get('hashtags')), *_items(profile.get('hashtags'))]
    )
    if hashtags:
        description += '\n\n' + ' '.join(f'#{value}' for value in hashtags)
    description = description[:5000]

    tags = _string_list(
        [
            *_items(raw_metadata.get('tags') or raw_metadata.get('keywords')),
            *_items(profile.get('default_tags')),
            *_items(profile.get('topic_keywords')),
        ],
        maximum_items=30,
        maximum_length=100,
    )
    language = _language(
        profile.get('default_language') or spec.get('language') or 'tr'
    )
    release_mode = str(profile.get('release_mode') or 'private')
    publish_at = None
    if release_mode == 'scheduled':
        delay = max(15, int(profile.get('schedule_delay_minutes') or 60))
        publish_at = (_now() + timedelta(minutes=delay)).isoformat()

    plan = {
        'schema_version': 1,
        'source_task_id': source_task_id,
        'target_channel_id': _safe_id(str(profile.get('channel_id') or '')),
        'profile_revision': str(profile.get('profile_revision') or ''),
        'profile_snapshot': {
            'channel_identity': _one_line(profile.get('channel_identity'), 240),
            'route_label': _one_line(profile.get('route_label'), 120),
            'release_mode': release_mode,
            'series_id': series_id,
        },
        'title': title,
        'description': description,
        'tags': tags,
        'hashtags': hashtags,
        'category_id': str(profile.get('category_id') or '28'),
        'default_language': language,
        'thumbnail_key': _storage_key(
            raw_metadata.get('thumbnail_key') or result.get('thumbnail_key')
        ),
        'require_thumbnail': bool(profile.get('require_thumbnail')),
        'release_mode': release_mode,
        'publish_at': publish_at,
        'series': {
            'id': series_id,
            'name': series_name,
            'number': series_number,
            'total': series_total,
        } if series_id else None,
        'quality_snapshot': {
            'quality_disposition': result.get('quality_disposition'),
            'manual_qa_required': result.get('manual_qa_required'),
        },
        'created_at': _now().isoformat(),
    }
    return validate_publish_plan(plan)


def validate_publish_plan(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get('schema_version') != 1:
        raise MetadataValidationError('Publish plan is missing or invalid')
    plan = dict(value)
    plan['source_task_id'] = _safe_id(plan.get('source_task_id'), 'source_task_id')
    plan['target_channel_id'] = _safe_id(plan.get('target_channel_id'))
    plan['title'] = _one_line(plan.get('title'), 100)
    plan['description'] = str(plan.get('description') or '').strip()[:5000]
    if not plan['title'] or not plan['description']:
        raise MetadataValidationError('Publish title and description are required')
    plan['tags'] = _string_list(
        plan.get('tags'), maximum_items=30, maximum_length=100
    )
    plan['hashtags'] = _hashtags(plan.get('hashtags'))
    category_id = str(plan.get('category_id') or '28')
    if not _CATEGORY_PATTERN.fullmatch(category_id):
        raise MetadataValidationError('Publish category is invalid')
    plan['category_id'] = category_id
    plan['default_language'] = _language(plan.get('default_language') or 'tr')
    plan['thumbnail_key'] = _storage_key(plan.get('thumbnail_key'))
    release_mode = str(plan.get('release_mode') or 'private')
    if release_mode not in {'private', 'public', 'scheduled'}:
        raise MetadataValidationError('Publish release mode is invalid')
    plan['release_mode'] = release_mode
    if release_mode == 'scheduled':
        try:
            publish_at = datetime.fromisoformat(str(plan.get('publish_at') or ''))
        except (TypeError, ValueError) as exc:
            raise MetadataValidationError('Scheduled publish time is invalid') from exc
        if publish_at.tzinfo is None:
            raise MetadataValidationError('Scheduled publish time is invalid')
        plan['publish_at'] = publish_at.astimezone(timezone.utc).isoformat()
    else:
        plan['publish_at'] = None
    return plan
