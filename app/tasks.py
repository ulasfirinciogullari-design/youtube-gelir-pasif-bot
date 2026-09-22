from __future__ import annotations

from pathlib import Path
from copy import deepcopy
import hashlib
import json
import math
import re
import shutil
import time
from concurrent.futures import as_completed
from app.services.abacus_generation import AbacusGenerationError
from app.services.production_spend import SpendBlocked
from app.services.production_failures import ProductionContentError
from app.services.production_spend_runtime import (
    SpendingThreadPoolExecutor as ThreadPoolExecutor, spending_task,
    prepare_video_scene_budget, spending_scene,
)

import httpx
from celery.exceptions import Ignore

from app.celery_app import celery
from app.services.audio_design import generate_music_bed, mix_voice_and_music
from app.services.audio_qc import (
    AudioQCError,
    audio_qc_provider_diagnostics,
    verify_audio_narration,
    verify_audio_prosody,
)
from app.services.director import (
    ImmutableNarrationSceneBudgetError,
    direct_and_qc,
    short_story_package_is_approved,
)
from app.services.pexels import find_broll, download_broll
from app.services.render import (
    aspect_ratio_for_mode,
    media_duration,
    render_video,
    resolution_for_mode,
    video_frame_count,
)
from app.services.research import research_and_script
from app.services.runway import (
    GeminiImageAttemptedError,
    GeminiOmniContinuityReferenceError,
    GeminiOmniTerminalError,
    create_gemini_omni_continuity_reference,
    download_generated_scene,
    generate_scene,
)
from app.services.storage import download_file, upload_file, presigned_download_url
from app.services.studio_state import (
    acquire_retry_child_execution,
    render_cancellation_requested,
    retained_delivery_blocked,
    mark_failure,
    mark_success,
    paid_create_budget_state,
    save_repair_checkpoint,
    set_stage,
    update_job,
)
from app.services.visual_qc import review_scene_visuals
from app.services.voice import (
    VoiceQualityError,
    VoiceScriptFitError,
    is_transient_voice_http_error,
    synthesize_scene_sequence,
    voice_http_retry_delay_seconds,
)
from app.services.visual_identity import manufactured_replica_guardrail
from app.services.visual_routing import (
    preview_paid_ai_limit,
    preview_runway_repair_indices,
    preview_total_paid_create_cap,
    should_rank_runway_candidate,
)


class FinalVisualQualityError(RuntimeError):
    """A bounded semantic-quality rejection that should not rerun the whole pipeline."""


class FinalAudioQualityError(RuntimeError):
    """A bounded exact-transcript rejection before any paid video request."""


class UnsupportedLanguageError(ValueError):
    """A terminal language contract rejection before any provider call."""


SUPPORTED_PIPELINE_LANGUAGES = frozenset({'tr', 'en', 'de', 'es', 'ar'})
_OMNI_CONTINUITY_MARKER_PATTERN = re.compile(
    r'\b(?:same|recurring|unchanged|identical|continuity|returning|'
    r'aynı|tekrar|süreklilik|unverändert|wiederkehrend|derselbe|dieselbe|'
    r'dasselbe|mismo|misma|recurrente)\b',
    re.IGNORECASE,
)
_OMNI_CONTINUITY_STOP_WORDS = frozenset({
    'action', 'angle', 'background', 'black', 'blue', 'bright', 'camera',
    'cinematic', 'clean', 'color', 'consistent', 'continuity', 'dark',
    'documentary', 'environment', 'exact', 'frame', 'geometry', 'green',
    'hand', 'identity', 'lighting', 'location', 'material', 'natural',
    'object', 'person', 'photorealistic', 'physical', 'realistic', 'recurring',
    'red', 'room', 'same', 'scene', 'setting', 'shot', 'subject', 'surface',
    'unchanged', 'unbranded', 'vertical', 'visible', 'white',
    'with', 'without', 'from', 'into', 'this', 'that', 'the', 'and',
    'aynı', 'tekrar', 'süreklilik', 'sahne', 'nesne', 'kişi', 'mekan',
    'mismo', 'misma', 'escena', 'objeto', 'persona',
    'derselbe', 'dieselbe', 'dasselbe', 'szene', 'objekt', 'person',
})


def normalize_pipeline_language(language: str) -> str:
    normalized = str(language or '').strip().casefold()
    if normalized not in SUPPORTED_PIPELINE_LANGUAGES:
        raise UnsupportedLanguageError(
            'Supported languages are tr, en, de, es and ar'
        )
    return normalized


def _omni_identity_tokens(scene: dict) -> set[str]:
    """Return bounded, non-generic identity terms from a scene contract."""
    text = str((scene or {}).get('ai_prompt') or '').casefold()[:4000]
    return {
        token
        for token in re.findall(r'[^\W_]{3,}', text, flags=re.UNICODE)
        if token not in _OMNI_CONTINUITY_STOP_WORDS
    }


def _omni_continuity_reference_applies(
    anchor_scene: dict,
    current_scene: dict,
) -> bool:
    """Require an explicit recurrence marker and a shared identity token."""
    current_prompt = str(
        (current_scene or {}).get('ai_prompt') or ''
    )[:4000]
    if not _OMNI_CONTINUITY_MARKER_PATTERN.search(current_prompt):
        return False
    anchor_tokens = _omni_identity_tokens(anchor_scene)
    current_tokens = _omni_identity_tokens(current_scene)
    return any(
        anchor == current
        or (
            min(len(anchor), len(current)) >= 4
            and (anchor in current or current in anchor)
        )
        for anchor in anchor_tokens
        for current in current_tokens
    )


def _omni_continuity_reference_needed(
    anchor_scene_idx: int,
    scenes: list[dict],
    eligible_scene_indices: set[int] | list[int] | tuple[int, ...],
) -> bool:
    """Create a billed anchor gate only when a later paid scene will use it."""
    if (
        type(anchor_scene_idx) is not int
        or anchor_scene_idx < 0
        or anchor_scene_idx >= len(scenes)
    ):
        return False
    return any(
        type(scene_idx) is int
        and anchor_scene_idx < scene_idx < len(scenes)
        and _omni_continuity_reference_applies(
            scenes[anchor_scene_idx],
            scenes[scene_idx],
        )
        for scene_idx in eligible_scene_indices
    )


class PreRunwayRetryableError(RuntimeError):
    """A pre-paid preflight rejection that may safely regenerate the automatic plan."""


class PexelsRetryError(RuntimeError):
    """A bounded Pexels retry could not produce provider evidence."""


_RECOVERED_MEDIA_SOURCE_PATTERN = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
)
_RECOVERED_MEDIA_KEY_PATTERN = re.compile(
    r'^recovery/(?P<source>[0-9a-f-]{36})/raw/'
    r'(?:clip-[0-9]{2}|scene-[0-9]{2}-(?:initial|repair-[0-9]{2}))\.mp4$'
)
_RECOVERED_MEDIA_PROVIDERS = {
    'runway',
    'fal_seedance_2_fast',
    'gemini_omni',
    'gemini_veo',
    'gemini_veo_fast',
    'gemini_veo_standard',
}
_REPAIR_RECOVERED_MEDIA_PROVIDERS = {
    *_RECOVERED_MEDIA_PROVIDERS,
    'gemini_image_motion',
}
_MAX_RECOVERED_VIDEO_BYTES = 100 * 1024 * 1024
_MAX_RECOVERED_AUDIO_BYTES = 20 * 1024 * 1024
_RECOVERED_VOICE_KEY_PATTERN = re.compile(
    r'^recovery/(?P<source>[0-9a-f-]{36})/raw/voice\.mp3$'
)
_SHA256_PATTERN = re.compile(r'^[0-9a-f]{64}$')


def _file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _recovery_package_sha256(package: dict) -> str:
    """Bind every checkpoint to the exact server-approved storyboard."""
    if not isinstance(package, dict):
        raise FinalVisualQualityError('Recovery storyboard is invalid')
    material = {
        key: value
        for key, value in package.items()
        if key not in {
            '_recovered_generated_media',
            '_recovered_voice',
        }
    }
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
        default=str,
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _validated_recovered_generated_media(
    raw: object,
    scene_count: int,
    expected_package_sha256: str | None = None,
) -> dict | None:
    """Validate a server-authored, Storage-only paid-media recovery contract."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise FinalVisualQualityError(
            'Recovered generated-media contract is malformed'
        )
    if raw.get('version') == 7:
        from app.services.content_plan_stock_repair import validate_media
        return validate_media(raw, scene_count, expected_package_sha256)
    if raw.get('version') == 6:
        try:
            from app.services.selected_visual_recovery import validate_selected_visual_recovery
            return validate_selected_visual_recovery(raw, scene_count, expected_package_sha256)
        except Exception:
            raise FinalVisualQualityError('Selected recovery contract could not be verified') from None
    if raw.get('version') == 5:
        try:
            from app.services.mixed_visual_recovery import validate_mixed_visual_recovery
            return validate_mixed_visual_recovery(raw, scene_count, expected_package_sha256)
        except Exception:
            raise FinalVisualQualityError('Mixed recovery contract could not be verified') from None

    if raw.get('version') in (2, 3, 4):
        partitioned_repair = raw.get('version') == 4
        recovery_only = raw.get('version') == 3
        expected_fields = {
            'version',
            'source_task_id',
            'package_sha256',
            'scenes',
        } | (
            {'recovery_only'} if recovery_only
            else {'repair_only', 'repair_scene_indices'}
        )
        if set(raw) != expected_fields:
            raise FinalVisualQualityError(
                'Recovered generated-media repair contract is malformed'
            )
        source_task_id = str(
            raw.get('source_task_id') or ''
        ).strip().lower()
        package_sha256 = str(
            raw.get('package_sha256') or ''
        ).strip().lower()
        raw_repair_indices = [] if recovery_only else raw.get('repair_scene_indices')
        raw_scenes = raw.get('scenes')
        if (
            raw.get('recovery_only' if recovery_only else 'repair_only') is not True
            or (recovery_only and type(raw.get('version')) is not int)
            or (partitioned_repair and (
                type(raw.get('version')) is not int
                or type(scene_count) is not int or scene_count < 1
                or expected_package_sha256 is None
            ))
            or not _RECOVERED_MEDIA_SOURCE_PATTERN.fullmatch(
                source_task_id
            )
            or not _SHA256_PATTERN.fullmatch(package_sha256)
            or (
                expected_package_sha256 is not None
                and package_sha256 != expected_package_sha256
            )
            or not isinstance(raw_repair_indices, list)
            or not isinstance(raw_scenes, dict)
            or (recovery_only and not raw_scenes)
        ):
            raise FinalVisualQualityError(
                'Recovered generated-media repair contract is invalid'
            )
        if (
            any(type(index) is not int for index in raw_repair_indices)
            or raw_repair_indices != sorted(set(raw_repair_indices))
            or (not recovery_only and not 1 <= len(raw_repair_indices) <= (4 if partitioned_repair else 2))
            or any(
                not 0 <= index < int(scene_count)
                for index in raw_repair_indices
            )
        ):
            raise FinalVisualQualityError(
                'Recovered generated-media repair indices are invalid'
            )

        repaired = set(raw_repair_indices)
        scenes: dict[int, list[dict]] = {}
        seen_object_keys: set[str] = set()
        for raw_scene_idx, raw_entries in raw_scenes.items():
            try:
                scene_idx = int(raw_scene_idx)
            except (TypeError, ValueError) as exc:
                raise FinalVisualQualityError(
                    'Recovered generated-media scene index is invalid'
                ) from exc
            if (
                str(scene_idx) != str(raw_scene_idx)
                or not 0 <= scene_idx < int(scene_count)
                or scene_idx in scenes
                or scene_idx in repaired
                or not isinstance(raw_entries, list)
                or not 1 <= len(raw_entries) <= 3
            ):
                raise FinalVisualQualityError(
                    'Recovered generated-media scene mapping is invalid'
                )
            entries: list[dict] = []
            for raw_entry in raw_entries:
                if not isinstance(raw_entry, dict) or set(raw_entry) != {
                    'key',
                    'sha256',
                    'size',
                    'provider',
                    'provider_attempts',
                    'synthetic_motion_only',
                    'motion_recipe_version',
                    'source_media_type',
                }:
                    raise FinalVisualQualityError(
                        'Recovered generated-media entry is malformed'
                    )
                key = str(raw_entry.get('key') or '').strip()
                checksum = str(
                    raw_entry.get('sha256') or ''
                ).strip().lower()
                provider = str(
                    raw_entry.get('provider') or ''
                ).strip()
                size = raw_entry.get('size')
                attempts = raw_entry.get('provider_attempts')
                synthetic_motion_only = raw_entry.get(
                    'synthetic_motion_only'
                )
                motion_recipe_version = raw_entry.get(
                    'motion_recipe_version'
                )
                source_media_type = raw_entry.get('source_media_type')
                match = _RECOVERED_MEDIA_KEY_PATTERN.fullmatch(key)
                if (
                    not match
                    or match.group('source') != source_task_id
                    or key in seen_object_keys
                    or not _SHA256_PATTERN.fullmatch(checksum)
                    or type(size) is not int
                    or not 1024 <= size <= _MAX_RECOVERED_VIDEO_BYTES
                    or provider not in _REPAIR_RECOVERED_MEDIA_PROVIDERS
                    or type(attempts) is not int
                    or not 1 <= attempts <= 10
                    or type(synthetic_motion_only) is not bool
                    or (
                        motion_recipe_version is not None
                        and (
                            not isinstance(motion_recipe_version, str)
                            or not 1 <= len(motion_recipe_version) <= 80
                        )
                    )
                    or source_media_type not in {None, 'image', 'video'}
                ):
                    raise FinalVisualQualityError(
                        'Recovered generated-media entry is invalid'
                    )
                normalized_entry = dict(raw_entry)
                normalized_entry.update({
                    'key': key,
                    'sha256': checksum,
                    'provider': provider,
                })
                entries.append(normalized_entry)
                seen_object_keys.add(key)
            scenes[scene_idx] = entries
        if partitioned_repair and set(scenes) | repaired != set(range(scene_count)):
            raise FinalVisualQualityError(
                'V4 repair requires a complete retained/repair partition'
            )
        if recovery_only:
            return {
                'version': 3,
                'recovery_only': True,
                'source_task_id': source_task_id,
                'package_sha256': package_sha256,
                'scenes': scenes,
            }
        return {
            'version': 4 if partitioned_repair else 2,
            'repair_only': True,
            'source_task_id': source_task_id,
            'package_sha256': package_sha256,
            'repair_scene_indices': list(raw_repair_indices),
            'scenes': scenes,
        }

    if set(raw) != {
        'version',
        'recovery_only',
        'source_task_id',
        'provider',
        'scenes',
    }:
        raise FinalVisualQualityError(
            'Recovered generated-media contract is malformed'
        )
    source_task_id = str(raw.get('source_task_id') or '').strip().lower()
    provider = str(raw.get('provider') or '').strip()
    raw_scenes = raw.get('scenes')
    if (
        raw.get('version') != 1
        or raw.get('recovery_only') is not True
        or not _RECOVERED_MEDIA_SOURCE_PATTERN.fullmatch(source_task_id)
        or provider not in _RECOVERED_MEDIA_PROVIDERS
        or not isinstance(raw_scenes, dict)
        or not raw_scenes
    ):
        raise FinalVisualQualityError(
            'Recovered generated-media contract is invalid'
        )

    scenes: dict[int, list[str]] = {}
    seen_object_keys: set[str] = set()
    for raw_scene_idx, raw_keys in raw_scenes.items():
        try:
            scene_idx = int(raw_scene_idx)
        except (TypeError, ValueError) as exc:
            raise FinalVisualQualityError(
                'Recovered generated-media scene index is invalid'
            ) from exc
        if (
            str(scene_idx) != str(raw_scene_idx)
            or not 0 <= scene_idx < int(scene_count)
            or scene_idx in scenes
            or not isinstance(raw_keys, list)
            or not 1 <= len(raw_keys) <= 3
        ):
            raise FinalVisualQualityError(
                'Recovered generated-media scene mapping is invalid'
            )
        keys: list[str] = []
        for raw_key in raw_keys:
            key = str(raw_key or '').strip()
            match = _RECOVERED_MEDIA_KEY_PATTERN.fullmatch(key)
            if (
                not match
                or match.group('source') != source_task_id
                or key in keys
                or key in seen_object_keys
            ):
                raise FinalVisualQualityError(
                    'Recovered generated-media object key is invalid'
                )
            keys.append(key)
            seen_object_keys.add(key)
        scenes[scene_idx] = keys

    return {
        'version': 1,
        'recovery_only': True,
        'source_task_id': source_task_id,
        'provider': provider,
        'scenes': scenes,
    }


def _validate_recovered_generated_clip(
    path: str | Path,
    minimum_duration: float,
    *,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
) -> None:
    """Fail closed on an empty, oversized, non-MP4 or too-short checkpoint."""
    clip = Path(path)
    try:
        size = clip.stat().st_size
    except OSError as exc:
        raise FinalVisualQualityError(
            'Recovered generated-media clip is unavailable'
        ) from exc
    if not 1024 <= size <= _MAX_RECOVERED_VIDEO_BYTES:
        raise FinalVisualQualityError(
            'Recovered generated-media clip size is invalid'
        )
    if expected_size is not None and size != expected_size:
        raise FinalVisualQualityError(
            'Recovered generated-media clip size does not match checkpoint'
        )
    if (
        expected_sha256 is not None
        and _file_sha256(clip) != expected_sha256
    ):
        raise FinalVisualQualityError(
            'Recovered generated-media clip checksum does not match checkpoint'
        )
    with clip.open('rb') as file_handle:
        header = file_handle.read(12)
    if len(header) < 12 or header[4:8] != b'ftyp':
        raise FinalVisualQualityError(
            'Recovered generated-media clip is not a valid MP4 file'
        )
    try:
        minimum = float(minimum_duration)
        duration = float(media_duration(clip))
        frames = int(video_frame_count(clip))
    except Exception as exc:
        raise FinalVisualQualityError(
            'Recovered generated-media clip could not be probed'
        ) from exc
    if (
        not math.isfinite(minimum)
        or minimum <= 0
        or not math.isfinite(duration)
        or duration + 0.04 < minimum
        or frames < max(1, int(math.floor(minimum * 12)))
    ):
        raise FinalVisualQualityError(
            'Recovered generated-media clip is too short'
        )


def _validated_recovered_voice(
    raw: object,
    scene_count: int,
    expected_package_sha256: str,
) -> dict | None:
    """Validate the exact previously approved narration checkpoint."""
    if raw is None:
        return None
    if isinstance(raw, dict) and raw.get('version') == 6:
        try:
            from app.services.selected_visual_recovery import validate_selected_voice_recovery
            return validate_selected_voice_recovery(raw, scene_count, expected_package_sha256)
        except Exception:
            raise FinalAudioQualityError('Selected narration contract could not be verified') from None
    required_fields = {
        'version',
        'source_task_id',
        'package_sha256',
        'key',
        'sha256',
        'size',
        'scene_durations',
        'spoken_texts',
        'duration_before_fit',
        'duration_after_fit',
        'tempo_rate',
        'content_target_seconds',
        'reserved_tail_seconds',
    }
    if not isinstance(raw, dict) or set(raw) != required_fields:
        raise FinalAudioQualityError(
            'Recovered narration contract is malformed'
        )
    source_task_id = str(raw.get('source_task_id') or '').strip().lower()
    package_sha256 = str(raw.get('package_sha256') or '').strip().lower()
    key = str(raw.get('key') or '').strip()
    checksum = str(raw.get('sha256') or '').strip().lower()
    size = raw.get('size')
    durations = raw.get('scene_durations')
    spoken_texts = raw.get('spoken_texts')
    key_match = _RECOVERED_VOICE_KEY_PATTERN.fullmatch(key)
    if (
        raw.get('version') != 1
        or not _RECOVERED_MEDIA_SOURCE_PATTERN.fullmatch(source_task_id)
        or not _SHA256_PATTERN.fullmatch(package_sha256)
        or package_sha256 != expected_package_sha256
        or not key_match
        or key_match.group('source') != source_task_id
        or not _SHA256_PATTERN.fullmatch(checksum)
        or type(size) is not int
        or not 1024 <= size <= _MAX_RECOVERED_AUDIO_BYTES
        or not isinstance(durations, list)
        or len(durations) != int(scene_count)
        or not isinstance(spoken_texts, list)
        or len(spoken_texts) != int(scene_count)
    ):
        raise FinalAudioQualityError(
            'Recovered narration contract is invalid'
        )
    normalized_durations: list[float] = []
    for raw_duration in durations:
        if (
            isinstance(raw_duration, bool)
            or not isinstance(raw_duration, (int, float))
        ):
            raise FinalAudioQualityError(
                'Recovered narration scene timing is invalid'
            )
        duration = float(raw_duration)
        if not math.isfinite(duration) or not 0.10 <= duration <= 20.0:
            raise FinalAudioQualityError(
                'Recovered narration scene timing is invalid'
            )
        normalized_durations.append(duration)
    normalized_spoken = [str(value or '').strip() for value in spoken_texts]
    if any(not value or len(value) > 600 for value in normalized_spoken):
        raise FinalAudioQualityError(
            'Recovered narration spoken text is invalid'
        )
    numeric_fields: dict[str, float] = {}
    for field in (
        'duration_before_fit',
        'duration_after_fit',
        'tempo_rate',
        'content_target_seconds',
        'reserved_tail_seconds',
    ):
        raw_value = raw.get(field)
        if isinstance(raw_value, bool) or not isinstance(
            raw_value,
            (int, float),
        ):
            raise FinalAudioQualityError(
                'Recovered narration timing metadata is invalid'
            )
        value = float(raw_value)
        if not math.isfinite(value):
            raise FinalAudioQualityError(
                'Recovered narration timing metadata is invalid'
            )
        numeric_fields[field] = value
    if (
        not 0.5 <= numeric_fields['duration_before_fit'] <= 60 * 30
        or not 0.5 <= numeric_fields['duration_after_fit'] <= 60 * 30
        or not 0.80 <= numeric_fields['tempo_rate'] <= 1.25
        or not 0.5 <= numeric_fields['content_target_seconds'] <= 60 * 30
        or not 0.0 <= numeric_fields['reserved_tail_seconds'] <= 2.0
        or abs(
            sum(normalized_durations)
            - numeric_fields['duration_after_fit']
        ) > 1.0
    ):
        raise FinalAudioQualityError(
            'Recovered narration timing metadata is inconsistent'
        )
    return {
        **raw,
        'source_task_id': source_task_id,
        'package_sha256': package_sha256,
        'key': key,
        'sha256': checksum,
        'scene_durations': normalized_durations,
        'spoken_texts': normalized_spoken,
        **numeric_fields,
    }


def _download_recovered_voice_candidate(
    recovered_voice: dict,
    work: Path,
) -> dict:
    output = work / 'recovered_voice.mp3'
    try:
        download_file(recovered_voice['key'], output)
        size = output.stat().st_size
        if size != recovered_voice['size']:
            raise FinalAudioQualityError(
                'Recovered narration size does not match checkpoint'
            )
        if _file_sha256(output) != recovered_voice['sha256']:
            raise FinalAudioQualityError(
                'Recovered narration checksum does not match checkpoint'
            )
        actual_duration = float(media_duration(output))
    except FinalAudioQualityError:
        raise
    except Exception as exc:
        raise FinalAudioQualityError(
            'Recovered narration could not be downloaded or probed'
        ) from exc
    if (
        not math.isfinite(actual_duration)
        or abs(
            actual_duration - recovered_voice['duration_after_fit']
        ) > 0.35
    ):
        raise FinalAudioQualityError(
            'Recovered narration duration does not match checkpoint'
        )
    return {
        'path': str(output),
        'scene_durations': list(recovered_voice['scene_durations']),
        'spoken_texts': list(recovered_voice['spoken_texts']),
        'voice_name': 'Recovered approved voice',
        'duration_before_fit': recovered_voice['duration_before_fit'],
        'duration_after_fit': recovered_voice['duration_after_fit'],
        'tempo_rate': recovered_voice['tempo_rate'],
        'content_target_seconds': recovered_voice[
            'content_target_seconds'
        ],
        'reserved_tail_seconds': recovered_voice[
            'reserved_tail_seconds'
        ],
        'removed_silence_seconds': 0.0,
        'compacted_boundary_pause_count': 0,
        'compacted_trailing_silence': False,
        '_generation_attempt': 0,
        '_generation_attempts_used': 1,
        '_synthesis_quality_errors': [],
        '_recovered_voice': True,
    }


def _stage_scene_repair_artifacts(
    *,
    task_id: str,
    package: dict,
    voice_result: dict,
    scene_durations: list[float],
    generated_checkpoint_specs: dict[int, list[dict]],
    excluded_scene_indices: set[int] | None = None,
) -> tuple[dict[str, list[dict]], dict]:
    """Upload immutable raw artifacts before the final critic can fail."""
    source_task_id = str(task_id or '').strip().lower()
    if not _RECOVERED_MEDIA_SOURCE_PATTERN.fullmatch(source_task_id):
        raise FinalVisualQualityError('Repair artifact task id is invalid')
    excluded = set(excluded_scene_indices or set())
    package_sha256 = _recovery_package_sha256(package)
    recovered_scenes: dict[str, list[dict]] = {}
    for scene_idx in sorted(generated_checkpoint_specs):
        if scene_idx in excluded:
            continue
        raw_specs = generated_checkpoint_specs.get(scene_idx) or []
        entries: list[dict] = []
        seen_paths: set[str] = set()
        for raw_spec in raw_specs:
            if not isinstance(raw_spec, dict):
                continue
            path = Path(str(raw_spec.get('path') or ''))
            resolved_path = str(path)
            if not path.is_file() or resolved_path in seen_paths:
                continue
            provider = str(
                raw_spec.get('generation_provider') or ''
            ).strip()
            if provider not in _REPAIR_RECOVERED_MEDIA_PROVIDERS:
                raise FinalVisualQualityError(
                    'Generated checkpoint provider is unsupported'
                )
            _validate_recovered_generated_clip(
                path,
                minimum_duration=max(
                    5.0,
                    float(scene_durations[scene_idx]) + 0.35,
                ),
            )
            ordinal = len(entries)
            suffix = (
                'initial'
                if ordinal == 0
                else f'repair-{ordinal:02d}'
            )
            key = (
                f'recovery/{source_task_id}/raw/'
                f'scene-{scene_idx:02d}-{suffix}.mp4'
            )
            size = path.stat().st_size
            checksum = _file_sha256(path)
            upload_file(path, key, 'video/mp4')
            entries.append({
                'key': key,
                'sha256': checksum,
                'size': size,
                'provider': provider,
                'provider_attempts': max(
                    1,
                    min(
                        10,
                        int(
                            raw_spec.get(
                                'generation_provider_attempts'
                            ) or 1
                        ),
                    ),
                ),
                'synthetic_motion_only': bool(
                    raw_spec.get('synthetic_motion_only')
                ),
                'motion_recipe_version': (
                    str(raw_spec.get('motion_recipe_version'))[:80]
                    if raw_spec.get('motion_recipe_version')
                    else None
                ),
                'source_media_type': (
                    raw_spec.get('source_media_type')
                    if raw_spec.get('source_media_type') in {
                        'image',
                        'video',
                    }
                    else None
                ),
            })
            seen_paths.add(resolved_path)
            if len(entries) >= 3:
                break
        if entries:
            recovered_scenes[str(scene_idx)] = entries

    voice_path = Path(str(voice_result.get('path') or ''))
    if not voice_path.is_file():
        raise FinalAudioQualityError(
            'Approved narration is unavailable for repair checkpoint'
        )
    voice_size = voice_path.stat().st_size
    if not 1024 <= voice_size <= _MAX_RECOVERED_AUDIO_BYTES:
        raise FinalAudioQualityError(
            'Approved narration size is invalid for repair checkpoint'
        )
    voice_key = f'recovery/{source_task_id}/raw/voice.mp3'
    upload_file(voice_path, voice_key, 'audio/mpeg')
    voice_contract = {
        'version': 1,
        'source_task_id': source_task_id,
        'package_sha256': package_sha256,
        'key': voice_key,
        'sha256': _file_sha256(voice_path),
        'size': voice_size,
        'scene_durations': [float(value) for value in scene_durations],
        'spoken_texts': [
            str(value or '').strip()
            for value in (voice_result.get('spoken_texts') or [])
        ],
        'duration_before_fit': float(
            voice_result.get('duration_before_fit') or 0
        ),
        'duration_after_fit': float(
            voice_result.get('duration_after_fit') or 0
        ),
        'tempo_rate': float(voice_result.get('tempo_rate') or 0),
        'content_target_seconds': float(
            voice_result.get('content_target_seconds') or 0
        ),
        'reserved_tail_seconds': float(
            voice_result.get('reserved_tail_seconds') or 0
        ),
    }
    _validated_recovered_voice(
        voice_contract,
        len(scene_durations),
        package_sha256,
    )
    return recovered_scenes, voice_contract


def _persist_scene_repair_checkpoint(
    *,
    task_id: str,
    package: dict,
    voice_result: dict,
    scene_durations: list[float],
    generated_checkpoint_specs: dict[int, list[dict]],
    rejected_scene_indices: list[int],
    staged_recovered_scenes: dict[str, list[dict]] | None = None,
    staged_voice_contract: dict | None = None,
) -> bool:
    """Publish a single-use repair pointer before temporary cleanup."""
    source_task_id = str(task_id or '').strip().lower()
    rejected = sorted(set(int(index) for index in rejected_scene_indices))
    if (
        not _RECOVERED_MEDIA_SOURCE_PATTERN.fullmatch(source_task_id)
        or not 1 <= len(rejected) <= 2
        or any(not 0 <= index < len(scene_durations) for index in rejected)
    ):
        return False
    package_sha256 = _recovery_package_sha256(package)
    if staged_recovered_scenes is None or staged_voice_contract is None:
        recovered_scenes, voice_contract = (
            _stage_scene_repair_artifacts(
                task_id=source_task_id,
                package=package,
                voice_result=voice_result,
                scene_durations=scene_durations,
                generated_checkpoint_specs=generated_checkpoint_specs,
                excluded_scene_indices=set(rejected),
            )
        )
    else:
        recovered_scenes = {
            str(scene_idx): list(entries)
            for scene_idx, entries in staged_recovered_scenes.items()
            if int(scene_idx) not in rejected
        }
        voice_contract = dict(staged_voice_contract)
    media_contract = {
        'version': 2,
        'repair_only': True,
        'source_task_id': source_task_id,
        'package_sha256': package_sha256,
        'repair_scene_indices': rejected,
        'scenes': recovered_scenes,
    }
    approved_package = dict(package)
    approved_package['_recovered_generated_media'] = media_contract
    approved_package['_recovered_voice'] = voice_contract
    checkpoint = {
        'version': 1,
        'source_task_id': source_task_id,
        'package_sha256': package_sha256,
        'approved_package': approved_package,
    }
    # Validate the complete package before publishing its single-use pointer.
    _validated_recovered_generated_media(
        media_contract,
        len(scene_durations),
        package_sha256,
    )
    _validated_recovered_voice(
        voice_contract,
        len(scene_durations),
        package_sha256,
    )
    # Publish the non-sensitive UI description before the checkpoint.  The
    # final save atomically makes the private package available and clears any
    # earlier claim marker, so a concurrent consumer can never be overwritten
    # by a late ``repair_available=True`` job update.
    update_job(
        source_task_id,
        repair_scene_indices=rejected,
        repair_message=(
            'Kabul edilen ses ve sahneler kilitlendi; yalnızca reddedilen '
            'sahne yeniden üretilecek.'
        ),
    )
    save_repair_checkpoint(source_task_id, checkpoint)
    return True


def _require_recovered_media_coverage(
    recovered_generated_media: dict | None,
    selected_scene_indices: list[int],
) -> None:
    """A recovery-only run may never fall through to a new paid create."""
    if not recovered_generated_media:
        return
    selected = set(int(index) for index in selected_scene_indices)
    if recovered_generated_media.get('version') == 6:
        if selected != set(recovered_generated_media['repair_scene_indices']):
            raise FinalVisualQualityError('Selected recovery may create only its frozen rejected scenes')
        return
    recovered = set(recovered_generated_media['scenes'])
    if recovered_generated_media.get('version') in (2, 4, 5):
        recovered |= set(
            recovered_generated_media['repair_scene_indices']
        )
    missing = sorted(selected - recovered)
    unexpected = sorted(recovered - selected)
    if missing or unexpected:
        raise FinalVisualQualityError(
            'Recovered generated-media contract does not exactly match '
            'selected paid scenes: '
            f'missing={missing},unexpected={unexpected}'
        )


def _curated_recovery_source(
    task_id: str, source_task_id: str | None, runtime_spec: dict,
    approved_package: dict | None, manifest: dict,
    recovered_media: dict | None, recovered_voice: dict | None,
) -> dict:
    """A curated input is server-dispatched recovery, never a model option."""
    if isinstance(manifest, dict) and manifest.get('kind') == 'owner_plan_retained':
        from app.services.content_plan_recovery import verify_child
        return verify_child(task_id, source_task_id, runtime_spec, approved_package, manifest)
    from app.services.studio_state import get_job

    if (
        not isinstance(manifest, dict) or not isinstance(approved_package, dict)
        or runtime_spec.get('mode') != 'production' or runtime_spec.get('format') != 'shorts'
        or runtime_spec.get('duration_minutes') != 0.5 or runtime_spec.get('music') != 'off'
        or len(approved_package.get('scenes') or []) != 6
        or not source_task_id or manifest.get('source_task_id') != source_task_id
        or not recovered_media or recovered_media.get('version') != 3
        or recovered_media.get('recovery_only') is not True
        or set(recovered_media.get('scenes') or {}) != {3}
        or len(recovered_media['scenes'][3]) != 1
        or not recovered_voice
        or recovered_media['source_task_id'] != recovered_voice['source_task_id']
    ):
        raise FinalVisualQualityError('Curated recovery scope is invalid; no replacement media was generated')
    source, child = get_job(source_task_id), get_job(task_id)
    if (
        not isinstance(source, dict) or not isinstance(child, dict)
        or source.get('task_id') != source_task_id or source.get('kind') != 'render'
        or source.get('state') != 'FAILURE' or source.get('retry_child_task_id') != task_id
        or child.get('task_id') != task_id or child.get('parent_id') != source_task_id
        or child.get('kind') != 'render'
        or source.get('spec') != runtime_spec or child.get('spec') != runtime_spec
    ):
        raise FinalVisualQualityError('Curated recovery dispatch binding is invalid')
    return source


def _collect_curated_recovery_visuals(
    manifest: dict, source_job: dict, approved_package: dict,
    recovered_media: dict, recovered_voice: dict, task_id: str, work: Path,
) -> dict:
    """Load the pinned stocks and existing paid shots before any review."""
    if isinstance(manifest, dict) and manifest.get('kind') == 'owner_plan_retained':
        from app.services.content_plan_recovery import load_visuals
        return load_visuals(manifest, source_job, approved_package, recovered_media,
                            recovered_voice, task_id, work)
    if recovered_media.get('version') == 5:
        return _collect_mixed_recovery_visuals(recovered_media, recovered_voice, work)
    try:
        from app.services.curated_stock import load_curated_stock_manifest
        from app.services.voice_candidate_recovery import _download_bounded
        from app.services import storage

        stock = load_curated_stock_manifest(
            manifest, source_job=source_job, approved_package=approved_package,
            child_task_id=task_id, work_dir=work,
        )
        pools = stock['scene_visuals']
        if not isinstance(pools, dict) or set(pools) != {0, 1, 2, 4, 5}:
            raise ValueError('Incomplete curated stock coverage')
        scene_visuals = [[] for _ in range(6)]
        for index, pool in pools.items():
            if (not isinstance(pool, list) or len(pool) != 1 or not isinstance(pool[0], dict)
                    or pool[0].get('source_type') != 'stock' or pool[0].get('stock_provider') != 'pexels'
                    or pool[0].get('generated') is True):
                raise ValueError('Invalid curated stock source')
            spec = dict(pool[0])
            spec.update(curated_pinned=True, preserve_start_fraction=True)
            scene_visuals[index] = [spec]
        entry = recovered_media['scenes'][3][0]
        path = work / 'recovered_s03_00.mp4'
        if path.exists() or path.is_symlink():
            raise ValueError('Curated paid destination already exists')
        checksum, _size = _download_bounded(
            storage._client(), entry['key'], path, _MAX_RECOVERED_VIDEO_BYTES,
            expected_size=entry['size'],
        )
        if checksum != entry['sha256']:
            raise ValueError('Curated paid source changed')
        _validate_recovered_generated_clip(
            path, minimum_duration=max(5.0, recovered_voice['scene_durations'][3] + 0.35),
            expected_size=entry['size'], expected_sha256=entry['sha256'],
        )
        paid = _generated_visual_spec(path, provider=entry['provider'], provider_attempts=entry['provider_attempts'])
        paid.update(curated_pinned=True, generation_recovered=True,
                    recovered_from_task_id=recovered_media['source_task_id'])
        scene_visuals[3] = [paid]
        _require_unique_selected_stock(scene_visuals)
        return {'scene_visuals': scene_visuals, 'credits': stock['credits'],
                'seen_ids': {pool[0]['pexels_id'] for pool in pools.values()}}
    except Exception:
        raise FinalVisualQualityError('Curated recovery media could not be verified; no replacement media was generated') from None


def _mixed_recovery_source(
    task_id: str, source_task_id: str | None, runtime_spec: dict,
    approved_package: dict | None, recovered_media: dict, recovered_voice: dict | None,
    cap: int | None, *, paid_slots_used: int,
) -> dict:
    """V5 is a claimed server recovery, not a caller-selected stock option."""
    from app.services.studio_state import get_job

    if (
        not isinstance(approved_package, dict) or not source_task_id
        or not isinstance(task_id, str) or _RECOVERED_MEDIA_SOURCE_PATTERN.fullmatch(task_id) is None
        or task_id == source_task_id or source_task_id != recovered_media.get('source_task_id')
        or runtime_spec.get('mode') != 'production' or runtime_spec.get('format') != 'shorts'
        or runtime_spec.get('duration_minutes') != 0.5 or runtime_spec.get('language') != 'tr'
        or runtime_spec.get('music') != 'off' or len(approved_package.get('scenes') or []) != 6
        or recovered_media.get('version') != 5 or not recovered_voice
        or recovered_media['source_task_id'] != recovered_voice['source_task_id']
        or type(cap) is not int or cap < 1
    ):
        raise FinalVisualQualityError('Mixed recovery requires a bounded claimed production Short')
    _validate_paid_create_allocation(
        [{'scene_index': index} for index in sorted(
            set(recovered_media['scenes']) | set(recovered_media['repair_scene_indices'])
        )], recovered_media, cap, paid_slots_used=paid_slots_used,
    )
    source, child = get_job(source_task_id), get_job(task_id)
    if (
        not isinstance(source, dict) or not isinstance(child, dict)
        or source.get('task_id') != source_task_id or source.get('kind') != 'render'
        or source.get('state') != 'FAILURE' or source.get('retry_child_task_id') != task_id
        or child.get('task_id') != task_id or child.get('parent_id') != source_task_id
        or child.get('kind') != 'render'
        or source.get('spec') != runtime_spec or child.get('spec') != runtime_spec
    ):
        raise FinalVisualQualityError('Mixed recovery dispatch binding is invalid')
    return source


def _collect_mixed_recovery_visuals(recovered_media: dict, recovered_voice: dict, work: Path) -> dict:
    """Load only the immutable real stock and retained generated shot; no search."""
    try:
        from app.services.mixed_visual_recovery import load_mixed_stock
        from app.services.voice_candidate_recovery import _download_bounded
        from app.services import storage

        stock = load_mixed_stock(recovered_media, work)
        pools = stock['scene_visuals']
        if not isinstance(pools, dict) or set(pools) != {5}:
            raise ValueError('Invalid mixed stock coverage')
        pool = pools[5]
        if (not isinstance(pool, list) or len(pool) != 1 or not isinstance(pool[0], dict)
                or pool[0].get('source_type') != 'stock' or pool[0].get('stock_provider') != 'pexels'
                or pool[0].get('generated') is not False):
            raise ValueError('Invalid mixed stock identity')
        spec = dict(pool[0])
        entry = recovered_media['stock_scenes'][5]
        _validate_recovered_generated_clip(
            Path(spec['path']), minimum_duration=max(5.0, recovered_voice['scene_durations'][5] + 0.35),
            expected_size=entry['size'], expected_sha256=entry['sha256'],
        )
        spec.update(curated_pinned=True, preserve_start_fraction=True, forbid_loop=True)
        scene_visuals = [[] for _ in range(6)]
        scene_visuals[5] = [spec]
        entry = recovered_media['scenes'][1][0]
        path = work / 'recovered_s01_00.mp4'
        if path.exists() or path.is_symlink():
            raise ValueError('Mixed paid destination already exists')
        checksum, _size = _download_bounded(
            storage._client(), entry['key'], path, _MAX_RECOVERED_VIDEO_BYTES,
            expected_size=entry['size'],
        )
        if checksum != entry['sha256']:
            raise ValueError('Mixed paid source changed')
        _validate_recovered_generated_clip(
            path, minimum_duration=max(5.0, recovered_voice['scene_durations'][1] + 0.35),
            expected_size=entry['size'], expected_sha256=entry['sha256'],
        )
        paid = _generated_visual_spec(path, provider=entry['provider'], provider_attempts=entry['provider_attempts'])
        paid.update(curated_pinned=True, generation_recovered=True,
                    recovered_from_task_id=recovered_media['source_task_id'])
        scene_visuals[1] = [paid]
        return {'scene_visuals': scene_visuals, 'credits': stock['credits'], 'seen_ids': {spec['pexels_id']}}
    except Exception:
        raise FinalVisualQualityError('Mixed recovery media could not be verified; no replacement media was generated') from None


def _selected_recovery_authorization(task_id, source_id, runtime_spec, approved_package):
    """Recheck the real one-use child and owner state; never initialize money."""
    from app.services.selected_visual_recovery_state import verify_selected_recovery_child

    try:
        if getattr(settings, 'studio_spend_enforcement', False) is not True:
            raise SpendBlocked('spend_selected_recovery_requires_enforcement')
        return verify_selected_recovery_child(task_id, source_id, runtime_spec, approved_package)
    except SpendBlocked:
        raise
    except Exception:
        raise FinalVisualQualityError('Selected recovery dispatch binding could not be verified') from None


def _prepare_selected_worker_recovery(
    task_id, source_id, runtime_spec, approved_package, package, media, voice, work,
):
    """Keep the original storyboard and voice while reviewing a detached copy."""
    from app.services.selected_visual_recovery import load_selected_recovery
    from app.services.director import revalidate_immutable_short_story

    try:
        authorization = _selected_recovery_authorization(task_id, source_id, runtime_spec, approved_package)
        loaded = load_selected_recovery(media, voice, task_id, work,
                                        expected_binding=authorization['binding'])
        if (loaded['package'] != package
                or loaded['repair_scene_indices'] != media['repair_scene_indices']):
            raise ValueError('Selected recovery package changed')
        from app.services.production_shot_prompt import build_production_shot_prompt
        repair_prompts = {}
        for index in loaded['repair_scene_indices']:
            scene = package['scenes'][index]
            if scene.get('ai_prompt') is not None:
                repair_prompts[index] = build_production_shot_prompt(scene)
            else:
                observed = loaded['manifest']['scenes'][index]['qa_observation']
                review = {**{key: value for key, value in observed.items() if key != 'gates'},
                          **observed['gates']}
                composed = _runway_prompt_for_scene(scene, review, '9:16')
                repair_prompts[index] = build_production_shot_prompt({'ai_prompt': composed})
        review_options = {key: value for key, value in runtime_spec.items()
                          if key not in {'topic', 'duration_minutes', 'language', 'channel_id'}}
        review_kwargs = {}
        if 'spoken_word_budget' in package:
            from app.services.director import validate_spoken_word_budget
            if runtime_spec['language'] != 'en' or runtime_spec.get('production_scheduled') is not True:
                raise ValueError('Selected recovery spoken budget changed')
            review_kwargs['verified_spoken_word_budget'] = validate_spoken_word_budget(package['spoken_word_budget'])
        original = deepcopy(package)
        reviewed = revalidate_immutable_short_story(
            deepcopy(original), runtime_spec['topic'], 0.5, runtime_spec['language'], review_options,
            immutable_candidate_narrations=[scene['narration'] for scene in original['scenes']],
            immutable_scene_fields=True,
            **review_kwargs,
        )
        if (not isinstance(reviewed, dict) or reviewed.get('scenes') != original['scenes']
                or any(reviewed.get(key) != original.get(key) for key in ('narration', 'title', 'sources'))
                or package != original or not short_story_package_is_approved(reviewed, runtime_spec['topic'])):
            raise ValueError('Selected recovery story is not unchanged and approved')
        # New review metadata never replaces the package or changes its money
        # family. It attests only to the unchanged scene/source material.
        loaded['story_revalidated'] = True
        loaded['repair_prompts'] = repair_prompts
        return loaded
    except SpendBlocked:
        raise
    except Exception:
        raise FinalVisualQualityError('Selected recovery failed immutable story or asset verification') from None


def _selected_review_verdict(result, indices, threshold):
    """Keep observed negative gates; no score-only or duplicate-index approval."""
    if (type(result) is not dict or result.get('missing_review_indices')
            or type(result.get('reviews')) is not list or len(result['reviews']) != len(indices)):
        raise FinalVisualQualityError('Selected exact-cut quality review is incomplete')
    mapped, rejected = {}, []
    positive = ('evidence_gate_passed', 'editorial_gate_passed', 'identity_gate_passed',
                'subject_visible', 'spoken_action_visible')
    negative = ('unexplained_reset', *_MANUAL_QA_CLEAR_VISUAL_FIELDS)
    for raw in result['reviews']:
        if (type(raw) is not dict or type(raw.get('scene_index')) is not int
                or not 0 <= raw['scene_index'] < len(indices) or raw['scene_index'] in mapped
                or type(raw.get('score')) not in (int, float) or not math.isfinite(raw['score'])
                or not 0 <= raw['score'] <= 100 or type(raw.get('best_candidate_index')) is not int
                or raw['best_candidate_index'] != 0
                or any(type(raw.get(field)) is not bool for field in (*positive, *negative))):
            raise FinalVisualQualityError('Selected exact-cut quality review is malformed')
        index = indices[raw['scene_index']]
        mapped[raw['scene_index']] = {**raw, 'scene_index': index}
        if (raw['score'] < threshold or any(raw[field] is not True for field in positive)
                or any(raw[field] is not False for field in negative)):
            rejected.append(index)
    return {**result, 'reviews': [mapped[position] for position in sorted(mapped)],
            'selected_recovery_rejected_indices': sorted(rejected)}


def _review_selected_exact(recovery, scenes, work, topic, options, evidence_sources, *, scene_visuals=None):
    from app.services.selected_visual_recovery import prepare_selected_exact_visuals

    try:
        prepared = prepare_selected_exact_visuals(recovery, work, scene_visuals=scene_visuals)
        indices = sorted(prepared['scene_visuals'])
        if not indices:
            if scene_visuals is not None or len(recovery['repair_scene_indices']) != len(scenes):
                raise ValueError('Missing selected exact cuts')
            return {'reviews': [], 'selected_recovery_rejected_indices': []}
        result = review_scene_visuals(
            [scenes[index] for index in indices], [prepared['scene_visuals'][index] for index in indices],
            work / ('selected_retained_critic' if scene_visuals is None else 'selected_final_critic'),
            len(indices), _missing_review_attempts=0, _score_reason_consistency_attempts=0,
            topic=topic, story_scenes=scenes, content_style=options.get('content_style', ''),
            evidence_sources=evidence_sources,
        )
        threshold = max(MANUAL_QA_PUBLISH_QUALITY_THRESHOLD, int(options.get('quality_threshold') or 80),
                        recovery['manifest']['quality_threshold'])
        checked = _selected_review_verdict(result, indices, threshold)
        if scene_visuals is None and checked['selected_recovery_rejected_indices']:
            raise ValueError('Retained exact cuts rejected')
        return {**checked, 'selected_recovery_qa_inputs': prepared['qa_inputs']}
    except SpendBlocked:
        raise
    except Exception:
        raise FinalVisualQualityError('Selected exact-cut quality gate failed; no extra replacement is authorized') from None


def _review_mixed_retained_before_paid(
    scenes: list[dict], scene_visuals: list[list[dict]], voice_result: dict,
    work: Path, topic: str, options: dict, evidence_sources: list,
) -> dict:
    """Freshly approve only exact retained cuts before buying any repair."""
    try:
        from app.services.mixed_visual_recovery import exact_mixed_retained_visuals
        prepared = exact_mixed_retained_visuals(scenes, scene_visuals, voice_result, work)
        result = review_scene_visuals(
            [scenes[index] for index in (1, 5)],
            [prepared['scene_visuals'][index] for index in (1, 5)],
            work / 'mixed_prepaid_visual_qc', 2,
            _missing_review_attempts=0, _score_reason_consistency_attempts=0,
            topic=topic, story_scenes=scenes,
            content_style=options.get('content_style', ''), evidence_sources=evidence_sources,
        )
        if (not isinstance(result, dict) or result.get('missing_review_indices')
                or not isinstance(result.get('reviews'), list) or len(result['reviews']) != 2):
            raise ValueError('Mixed retained review missing')
        reviews = {}
        threshold = max(MANUAL_QA_PUBLISH_QUALITY_THRESHOLD, int(options['quality_threshold']))
        for review in result['reviews']:
            if (not isinstance(review, dict) or type(review.get('scene_index')) is not int
                    or review['scene_index'] not in (0, 1) or review['scene_index'] in reviews
                    or type(review.get('score')) not in (int, float)
                    or not threshold <= review['score'] <= 100
                    or type(review.get('best_candidate_index')) is not int or review['best_candidate_index'] != 0
                    or any(review.get(key) is not True for key in (
                        'evidence_gate_passed', 'editorial_gate_passed', 'identity_gate_passed',
                        'subject_visible', 'spoken_action_visible'))
                    or review.get('unexplained_reset') is not False
                    or any(review.get(key) is not False for key in _MANUAL_QA_CLEAR_VISUAL_FIELDS)):
                raise ValueError('Mixed retained exact cut rejected')
            reviews[review['scene_index']] = review
        return {**result, 'reviews': [
            {**reviews[local], 'scene_index': index} for local, index in enumerate((1, 5))
        ]}
    except Exception:
        raise FinalVisualQualityError('Mixed retained exact-cut quality gate failed before any new media submission') from None


SHORT_PREVIEW_PROVIDER_OUTAGE_RUNWAY_CAP = 2
# A real-first preview normally buys only one primary generated scene. If the
# exact stock tournament still leaves two ordinary (non-forced) scenes without
# publishable media, allow that second required scene locally instead of
# regenerating narration, research and the storyboard. A third remains a hard
# pre-paid failure, so this completion path cannot grow with scene count.
SHORT_PREVIEW_REQUIRED_RUNWAY_CAP = 2
# An exact-action preview can legitimately expose four different semantic
# stock misses after the full tournament.  Product close-ups and hand actions
# are especially unlikely to exist as one honest stock clip even when the
# story itself is sound.  Keep four replacements bounded by the scene count
# and fail closed on a fifth instead of discarding an otherwise approved
# storyboard before any paid submission.
SHORT_PREVIEW_STOCK_QUALITY_RUNWAY_CAP = 4
MANUAL_QA_PREVIEW_STOCK_FLOOR = 60
MANUAL_QA_PREVIEW_GENERATED_FLOOR = 60
MANUAL_QA_PUBLISH_QUALITY_THRESHOLD = 86
MAX_AUDIO_GENERATION_ATTEMPTS = 3
AUDIO_QC_PROVIDER_ATTEMPTS = 2
AUDIO_QC_PROVIDER_RETRY_DELAY_SECONDS = 1.0
STOCK_TOURNAMENT_GEMINI_MODEL = 'gemini-3.7-flash'
STOCK_TOURNAMENT_REVIEW_MAX_WORKERS = 4
_MANUAL_QA_CLEAR_VISUAL_FIELDS = (
    'prominent_readable_text_or_logo_visible',
    'major_visual_artifact_visible',
    'effectively_static_or_frozen',
    'substantially_repeats_adjacent_scene',
    'authored_identity_or_material_conflict_visible',
)
_TRANSIENT_PEXELS_HTTP_STATUS_CODES = {408, 425, 429}


def _persisted_paid_create_budget(
    task_id: str,
    cap: int,
    *,
    reserve: bool = False,
) -> dict[str, int]:
    try:
        return paid_create_budget_state(task_id, cap, reserve=reserve)
    except Exception as exc:
        raise FinalVisualQualityError(
            'Short-preview paid-create budget could not be reserved or '
            'verified; no new generation was submitted'
        ) from exc


def _persisted_paid_create_slots(
    task_id: str,
    cap: int,
    *,
    reserve: bool = False,
) -> int:
    return _persisted_paid_create_budget(task_id, cap, reserve=reserve)['used']


def _reserve_paid_create_slot(
    used: int,
    cap: int | None,
    *,
    task_id: str,
) -> int:
    """Conservatively reserve one paid create before contacting a provider."""
    from app.services.production_included_router import enabled
    from app.services.commissioning_video import enabled_for_task
    if enabled() and not enabled_for_task():
        raise FinalVisualQualityError(
            'Included production requires approved stock; new video generation is unavailable'
        )
    if cap is not None:
        return _persisted_paid_create_slots(task_id, cap, reserve=True)
    reserved = max(0, int(used))
    return reserved + 1


def _validate_paid_create_allocation(
    selected_candidates: list[dict],
    recovered_generated_media: dict | None,
    cap: int | None,
    *,
    paid_slots_used: int = 0,
) -> None:
    """Limit new creates while permitting reuse of already-paid clips."""
    from app.services.production_included_router import enabled
    from app.services.commissioning_video import enabled_for_task
    if enabled() and not enabled_for_task():
        retained = (recovered_generated_media or {}).get('scenes') or {}
        new_creates = (
            len(recovered_generated_media['repair_scene_indices'])
            if recovered_generated_media and recovered_generated_media.get('version') == 6
            else sum(not bool(retained.get(int(row['scene_index']))) for row in selected_candidates)
        )
        if new_creates:
            from app.services.production_failures import content_rejection

            raise content_rejection(FinalVisualQualityError(
                'Included production stock quality remains unresolved after bounded stock rescue; '
                'new video generation is unavailable'
            ), 'visual_quality_exhausted')
    if cap is None:
        return
    if recovered_generated_media and recovered_generated_media.get('version') == 6:
        # Every retained stock/generated selection is already present. It never
        # enters the create loop even when the frozen storyboard has ai_prompt.
        new_creates = len(recovered_generated_media['repair_scene_indices'])
        if new_creates > max(0, cap - paid_slots_used):
            raise FinalVisualQualityError('Selected recovery exceeds remaining paid-create capacity')
        return
    recovered_scenes = (
        recovered_generated_media['scenes']
        if recovered_generated_media
        else {}
    )
    new_creates = sum(
        not bool(recovered_scenes.get(int(candidate['scene_index'])))
        for candidate in selected_candidates
    )
    if new_creates > max(0, cap - paid_slots_used):
        raise FinalVisualQualityError(
            'Short-preview paid media allocation exceeds the job-wide '
            'paid-create cap before any submission'
        )


def _record_prepaid_visual_diagnostics(
    task_id: str,
    scene_visuals: list,
    reviews: dict,
    ranked_candidates: list[dict],
    cap: int | None,
    *,
    paid_slots_used: int,
    quality_threshold: int,
) -> None:
    """Retain bounded allocation facts, never paths or provider prose."""
    if type(cap) is not int or cap < 1:
        return
    pending = {
        row['scene_index'] for row in ranked_candidates
        if isinstance(row, dict) and type(row.get('scene_index')) is int
    }
    used = paid_slots_used if type(paid_slots_used) is int and paid_slots_used >= 0 else cap
    threshold = quality_threshold if type(quality_threshold) is int and 0 <= quality_threshold <= 100 else 100
    records = []
    for index, visuals in enumerate(scene_visuals[:64]):
        review = reviews.get(index)
        raw_score = review.get('score') if isinstance(review, dict) else None
        score = (
            float(raw_score)
            if type(raw_score) in (int, float) and math.isfinite(raw_score) and 0 <= raw_score <= 100
            else -1
        )
        has_visual = bool(isinstance(visuals, list) and any(_visual_path(spec) for spec in visuals))
        records.append({
            'scene_index': index,
            'score': score,
            'has_visual': has_visual,
            'requires_paid_replacement': index in pending,
            'reason': (
                'missing_visual' if not has_visual
                else 'missing_review' if score < 0
                else 'below_quality_threshold' if score < threshold
                else 'quality_threshold_met'
            ),
        })
    try:
        update_job(task_id, prepaid_visual_diagnostics={
            'stage': 'before_paid_allocation',
            'paid_create_cap': cap,
            'paid_slots_used': used,
            'paid_slots_remaining': max(0, cap - used),
            'quality_threshold': threshold,
            'scenes': records,
        })
    except Exception:
        # Diagnostic availability must not change the strict allocation gate.
        return


def _checkpoint_overbudget_visuals(
    task_id: str,
    scenes: list[dict],
    scene_visuals: list,
    reviews: dict,
    ranked_candidates: list[dict],
    work: Path,
    cap: int | None,
    *,
    paid_slots_used: int,
    quality_threshold: int,
) -> None:
    """Keep actual failure evidence without creating links or changing QA."""
    if (type(cap) is not int or cap < 1
            or type(paid_slots_used) is not int or paid_slots_used < 0):
        return
    remaining = max(0, cap - paid_slots_used)
    if len(ranked_candidates) <= remaining:
        return
    try:
        from app.services.visual_allocation_checkpoint import persist_visual_allocation_checkpoint

        pending = [row['scene_index'] for row in ranked_candidates]
        fields = persist_visual_allocation_checkpoint(
            task_id, scenes, scene_visuals, reviews, work,
            quality_threshold=quality_threshold,
            allocation={
                'paid_create_cap': cap,
                'paid_create_used': paid_slots_used,
                'paid_slots_remaining': remaining,
                'required_paid_scenes': len(pending),
                'quality_threshold': quality_threshold,
                'selected_paid_scene_indices': pending[:remaining],
                'overflow_scene_indices': pending[remaining:],
                'failed_stock_scene_indices': pending,
            },
        )
        update_job(task_id, **fields)
    except Exception:
        # The original allocation failure and all paid ledgers remain intact.
        return


def _checkpoint_selected_visuals(
    task_id, work, *, duration_minutes, effective_edit_target_seconds, package, scene_visuals, final_reviews,
    rejected_scene_indices, quality_threshold, voice_result, scene_durations,
    options, audio_qc, audio_duration_qc, audio_prosody_qc,
) -> None:
    """Preserve the rejected edit's inputs without creating a repair permit."""
    try:
        if (
            not isinstance(options, dict) or options.get('mode') != 'production'
            or options.get('format') != 'shorts' or options.get('music') != 'off'
            or type(duration_minutes) not in (int, float) or duration_minutes != 0.5
            or not isinstance(voice_result, dict)
            or not isinstance(scene_durations, list)
            or voice_result.get('scene_durations') != scene_durations
            or not all(
                isinstance(gate, dict) and gate.get('available') is True and gate.get('pass') is True
                for gate in (audio_qc, audio_duration_qc, audio_prosody_qc)
            )
        ):
            return
        from copy import deepcopy
        from app.services.selected_visual_context import resolve_selected_visual_binding
        from app.services.selected_visual_checkpoint import persist_selected_visual_checkpoint

        binding = resolve_selected_visual_binding(task_id)
        package_sha = _recovery_package_sha256(package)
        pointer = persist_selected_visual_checkpoint(
            task_id, work, binding=deepcopy(binding), package=deepcopy(package),
            voice_result=deepcopy(voice_result), scene_visuals=deepcopy(scene_visuals),
            final_reviews=deepcopy(final_reviews), rejected_scene_indices=deepcopy(rejected_scene_indices),
            quality_threshold=quality_threshold, options=deepcopy(options),
            duration_minutes=duration_minutes,
            effective_edit_target_seconds=effective_edit_target_seconds,
        )
        if (
            not isinstance(pointer, dict) or type(pointer.get('version')) is not int or pointer['version'] != 1
            or pointer.get('source_task_id') != task_id
            or pointer.get('status') != 'preserved_selected_candidates'
            or pointer.get('binding') != binding or pointer.get('package_sha256') != package_sha
            or any(pointer.get(key) is not False for key in (
                'accepted', 'qa_approved', 'publish_eligible', 'reusable', 'qa_input_verified',
            ))
            or any(pointer.get(key) is not True for key in ('requires_full_qa', 'exact_cut_qa_required'))
        ):
            return
        update_job(task_id, selected_visual_checkpoint=deepcopy(pointer))
    except Exception:
        # Preserve the original rejection. No fallback create, claim reset,
        # full rebuild, publication or changes to the existing spend history.
        return


def _checkpoint_qa_workprint(
    task_id: str,
    work: Path,
    *,
    duration_minutes: float,
    scenes: list[dict],
    scene_visuals: list,
    final_reviews: dict,
    voice_result: dict,
    scene_durations: list,
    narration: str,
    options: dict,
    audio_qc: dict,
    audio_duration_qc: dict,
    audio_prosody_qc: dict,
    effective_edit_target_seconds: float | None = None,
) -> None:
    """Keep a rejected private draft without changing its terminal outcome."""
    try:
        if (
            not isinstance(options, dict) or options.get('mode') != 'production'
            or options.get('format') != 'shorts' or options.get('music') != 'off'
            or type(duration_minutes) not in (int, float) or duration_minutes != 0.5
            or not all(
                isinstance(gate, dict) and gate.get('available') is True and gate.get('pass') is True
                for gate in (audio_qc, audio_duration_qc, audio_prosody_qc)
            )
        ):
            return
        from copy import deepcopy
        from app.services.qa_workprint import persist_qa_workprint

        target = duration_minutes * 60
        if (options.get('production_scheduled') is True
                and type(effective_edit_target_seconds) in (int, float)
                and 30 < effective_edit_target_seconds <= 40):
            target = effective_edit_target_seconds

        # Keep full candidate pools and their exact final best indices. The
        # helper understands already-collapsed selected singletons; rejected
        # multi-candidate scenes must not silently fall back to their first clip.
        fields = persist_qa_workprint(
            task_id, work,
            scenes=deepcopy(scenes), scene_visuals=deepcopy(scene_visuals),
            final_reviews=deepcopy(final_reviews), voice_result=deepcopy(voice_result),
            scene_durations=deepcopy(scene_durations), narration=narration,
            options=deepcopy(options), voice_quality_passed=True,
            target_seconds=target,
        )
        pointer = fields.get('qa_workprint') if isinstance(fields, dict) else None
        if (
            not isinstance(pointer, dict) or type(pointer.get('version')) is not int
            or pointer['version'] != (1 if target == 30 else 2)
            or pointer.get('task_id') != task_id or pointer.get('status') != 'qa_workprint'
            or any(pointer.get(key) is not False for key in ('qa_approved', 'publish_eligible', 'reusable'))
        ):
            return
        # This private diagnostic field is never result.video_key, SUCCESS or
        # an automatic-publication input. No spending/claim/QA fields are merged.
        update_job(task_id, qa_workprint=deepcopy(pointer))
    except Exception:
        # Rendering/storage diagnostics are best effort. Always preserve the
        # real final-visual rejection; never regenerate, review or retry here.
        return


def _preflight_production_shorts_paid_plan(
    options: dict,
    scenes: list[dict],
    recovered_generated_media: dict | None,
    cap: int | None,
    *,
    paid_slots_used: int = 0,
) -> None:
    """Reject over-budget production AI plans before voice/media spend."""
    if options.get('mode') != 'production' or options.get('format') != 'shorts' or cap is None:
        return
    authored_candidates = [
        {'scene_index': index}
        for index, scene in enumerate(scenes)
        if str(scene.get('ai_prompt') or '').strip()
    ]
    _validate_paid_create_allocation(
        authored_candidates,
        recovered_generated_media,
        cap,
        paid_slots_used=paid_slots_used,
    )


def _checkpoint_audio_candidate(task_id: str, package: dict, voice_result: dict) -> None:
    """Preserve an unapproved candidate without changing any quality decision."""
    from app.services.audio_checkpoint import persist_audio_candidate_checkpoint

    try:
        fields = persist_audio_candidate_checkpoint(task_id, package, voice_result)
        fields['audio_candidate_checkpoint_error'] = None
    except Exception:
        # Keep an earlier candidate address if a later attempt cannot be saved.
        fields = {'audio_candidate_checkpoint_error': 'unavailable'}
    try:
        update_job(task_id, **fields)
    except Exception:
        # Losing the registry must not discard a voice that still needs QA.
        pass


def _checkpoint_generated_asset(
    task_id, work, package, voice_result, visual_spec, scene_index, phase,
    options, duration_minutes, journal,
):
    """Record only private unapproved bytes; never change a render decision."""
    longform = bool(options.get('content_plan_item_id') and options.get('mode') == 'production'
                    and options.get('format') == 'landscape' and duration_minutes == 3)
    if (
        not longform and (options.get('mode') != 'production' or options.get('format') != 'shorts'
        or duration_minutes != 0.5)
    ):
        return
    try:
        from app.services.generated_asset_checkpoint import persist_generated_asset_candidate
        pointer = persist_generated_asset_candidate(
            task_id, work, package=package, voice_result=voice_result,
            visual_spec=visual_spec, scene_index=scene_index, phase=phase,
            options=options, duration_minutes=duration_minutes,
        )
        if (
            not isinstance(pointer, dict) or pointer.get('source_task_id') != task_id
            or pointer.get('scene_index') != scene_index or pointer.get('phase') != phase
            or pointer.get('status') != 'preserved_candidate'
            or pointer.get('qa_approved') is not False or pointer.get('reusable') is not False
            or pointer.get('requires_full_qa') is not True
        ):
            raise ValueError('Invalid preservation receipt')
    except Exception:
        pointer = {
            'source_task_id': task_id, 'scene_index': scene_index, 'phase': phase,
            'status': 'unavailable', 'reason': 'generated_asset_preservation_unavailable',
            'qa_approved': False, 'reusable': False, 'requires_full_qa': True,
        }
    journal.append(pointer)
    try:
        update_job(task_id, generated_asset_candidates={
            'version': 1, 'source_task_id': task_id, 'status': 'candidate_journal',
            'diagnostic_only': True, 'qa_approved': False, 'reusable': False,
            'requires_full_qa': True, 'attempted_count': len(journal),
            'preserved_count': sum(item['status'] == 'preserved_candidate' for item in journal),
            'failed_count': sum(item['status'] != 'preserved_candidate' for item in journal),
            'entries': list(journal[-(32 if longform else 24):]),
        })
    except Exception:
        # The content-addressed private manifest survives a registry outage.
        # Never resubmit a provider create or assert QA because a journal failed.
        pass


def _persist_final_thumbnail(task_id, work, rendered, options, quality_disposition, manual_qa_required):
    """A missing cover must not buy another render or silently approve release."""
    empty = {'thumbnail_key': None, 'thumbnail_sha256': None, 'thumbnail_size': None}
    if options.get('mode') != 'production' or options.get('format') not in {'shorts', 'landscape'}:
        return {**empty, 'thumbnail_generation': {'status': 'not_requested'}}
    if quality_disposition != 'automated_qc_pass' or manual_qa_required is not False:
        return {**empty, 'thumbnail_generation': {'status': 'not_approved'}}
    try:
        from app.services.publication_thumbnail import prepare_approved_final_thumbnail
        output = work / 'thumbnail.jpg'
        proof = prepare_approved_final_thumbnail(
            task_id, rendered['path'], output,
            video_format=options['format'], qa_approved=True,
        )
        key = f'videos/{task_id}/thumbnail.jpg'
        uploaded = upload_file(output, key, 'image/jpeg')
        if not isinstance(uploaded, dict) or uploaded.get('key') != key or uploaded.get('size') != proof['size']:
            raise ValueError('Thumbnail storage receipt did not match')
        return {'thumbnail_key': key, 'thumbnail_sha256': proof['sha256'],
                'thumbnail_size': proof['size'], 'thumbnail_generation': {**proof, 'status': 'stored'}}
    except Exception:
        # Keep the approved final. The unchanged publisher blocks public release
        # when its frozen require_thumbnail policy has no successfully uploaded
        # cover. No TTS, generated-video retry, or quality override is triggered.
        return {**empty, 'thumbnail_generation': {
            'status': 'unavailable', 'reason': 'thumbnail_preparation_unavailable',
        }}


def _effective_short_edit_target(
    options: dict,
    requested_seconds: float,
    voice_duration_seconds: float,
) -> float:
    """End an automatic short edit after its real voice.

    This selects a frame-aligned video endpoint only. It neither edits the
    audio/scene timeline nor approves its transcript, prosody or visual quality.
    Fixed previews and out-of-scope/thin/dense takes keep their original gates.
    """
    if (
        not isinstance(options, dict)
        or options.get('mode') != 'production'
        or options.get('format') != 'shorts'
        or type(requested_seconds) not in (int, float)
        or requested_seconds != 30
        or type(voice_duration_seconds) not in (int, float)
        or not math.isfinite(voice_duration_seconds)
        or not 25.5 <= voice_duration_seconds <= (
            39.45 if options.get('production_scheduled') is True else 29.75
        )
    ):
        return requested_seconds
    if _short_preview_voice_duration_qc(
        {'duration_after_fit': voice_duration_seconds}, requested_seconds,
    ).get('pass') is True:
        return requested_seconds
    endpoint = math.ceil((voice_duration_seconds + 0.55) * 30) / 30
    return endpoint if voice_duration_seconds > 29.75 else min(requested_seconds, endpoint)


def _short_preview_voice_duration_qc(
    voice_result: dict,
    target_seconds: float,
) -> dict:
    if not 0 < float(target_seconds) <= 40:
        return {
            'available': True,
            'pass': True,
            'retryable': False,
            'duration_seconds': voice_result.get('duration_after_fit'),
            'minimum_seconds': None,
            'maximum_seconds': None,
            'reason': None,
        }
    # A natural short may finish speaking before the final frame. Preserve up
    # to 1.30 seconds for a closing breath and visual hold instead of forcing
    # speech to be time-stretched into the entire 30-second edit.
    minimum = float(target_seconds) - 1.30
    maximum = float(target_seconds) - 0.25
    duration = voice_result.get('duration_after_fit')
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        return {
            'available': False,
            'pass': False,
            'retryable': False,
            'duration_seconds': None,
            'minimum_seconds': round(minimum, 3),
            'maximum_seconds': round(maximum, 3),
            'reason': 'voice_duration_missing',
        }
    duration_seconds = float(duration)
    passed = bool(
        math.isfinite(duration_seconds)
        and minimum <= duration_seconds <= maximum
    )
    deficit = max(0.0, minimum - duration_seconds)
    excess = max(0.0, duration_seconds - maximum)
    retryable = bool(not passed and max(deficit, excess) <= 0.65)
    if passed:
        reason = None
    elif deficit > 0.65:
        reason = 'short_form_script_too_thin'
    elif excess > 0.65:
        reason = 'short_form_script_too_dense'
    else:
        reason = 'short_form_duration_near_boundary'
    return {
        'available': True,
        'pass': passed,
        'retryable': retryable,
        'duration_seconds': round(duration_seconds, 3),
        'minimum_seconds': round(minimum, 3),
        'maximum_seconds': round(maximum, 3),
        'reason': reason,
    }


def _audio_qc_failure_evidence(history: list[dict]) -> dict:
    """Bounded factual checks, never provider text, transcripts or media keys."""
    reasons = {
        'voice_duration_missing', 'short_form_script_too_thin',
        'short_form_script_too_dense', 'short_form_duration_near_boundary',
        'speech_to_text_keys_missing', 'unnatural_internal_pause',
        'choppy_phrase_grouping', 'flat_emphasis', 'unnatural_pacing',
        'mispronunciation', 'gemini_api_key_unavailable',
        'gemini_prosody_protocol_invalid', 'gemini_prosody_review_failed',
        'prosody_review_unavailable', 'not_run',
        'not_run_transcript_rejected', 'not_run_duration_rejected',
        'not_applicable_language_or_long_form',
    }

    def number(value, maximum):
        if type(value) not in (int, float) or not 0 <= value <= maximum:
            return None
        return round(value, 3) if math.isfinite(value) else None

    def verdict(value):
        value = value if isinstance(value, dict) else {}
        reason = value.get('reason')
        return {
            'available': value.get('available') if type(value.get('available')) is bool else None,
            'pass': value.get('pass') if type(value.get('pass')) is bool else None,
            # Closed codes are stronger than redacting arbitrary provider text.
            'reason': (None if reason is None else reason
                       if isinstance(reason, str) and reason in reasons else 'reason_omitted'),
        }

    checks = []
    # Three synthesis attempts can include one same-audio pause recheck.
    for item in (history if isinstance(history, list) else [])[-(MAX_AUDIO_GENERATION_ATTEMPTS + 1):]:
        if not isinstance(item, dict):
            continue
        transcript, duration, prosody = (
            item.get(key) if isinstance(item.get(key), dict) else {}
            for key in ('transcript', 'duration', 'prosody')
        )
        attempt = item.get('generation_attempt')
        scores = prosody.get('scores')
        checks.append({
            'generation_attempt': (attempt if type(attempt) is int
                                   and 0 <= attempt < MAX_AUDIO_GENERATION_ATTEMPTS else None),
            'transcript': {**verdict(transcript), 'score': number(transcript.get('score'), 100)},
            'duration': {
                **verdict(duration),
                'retryable': duration.get('retryable') if type(duration.get('retryable')) is bool else None,
                **{key: number(duration.get(key), 86400) for key in (
                    'duration_seconds', 'minimum_seconds', 'maximum_seconds',
                )},
            },
            'prosody': {
                **verdict(prosody),
                'scores': ({key: number(scores.get(key), 100) for key in (
                    'pronunciation', 'naturalness', 'pacing',
                    'sentence_flow', 'emphasis', 'roboticness',
                )} if isinstance(scores, dict) else None),
            },
        })
    return {'version': 1, 'status': 'rejected', 'requires_full_qa': True, 'checks': checks}


def _strict_short_preview_render_qc(
    rendered: dict,
    requested_seconds: float,
    voice_duration_seconds: float,
) -> dict:
    """Keep the fixed master and its audible ending aligned fail-closed."""
    voice_gate = _short_preview_voice_duration_qc(
        {'duration_after_fit': voice_duration_seconds},
        requested_seconds,
    )
    try:
        requested = float(requested_seconds)
        voice_duration = float(voice_duration_seconds)
        actual_frames = int(rendered.get('frame_count') or 0)
        ending_silence = float(
            rendered.get('ending_silence_seconds') or 0
        )
    except (TypeError, ValueError, OverflowError):
        return {
            'pass': False,
            'reason': 'final_render_metrics_invalid',
            'expected_frames': None,
            'actual_frames': None,
            'ending_silence_seconds': None,
            'minimum_ending_silence_seconds': None,
            'maximum_ending_silence_seconds': None,
        }
    if (
        not math.isfinite(requested)
        or not math.isfinite(voice_duration)
        or not math.isfinite(ending_silence)
        or voice_gate.get('pass') is not True
    ):
        return {
            'pass': False,
            'reason': 'final_voice_duration_invalid',
            'expected_frames': None,
            'actual_frames': actual_frames,
            'ending_silence_seconds': ending_silence,
            'minimum_ending_silence_seconds': None,
            'maximum_ending_silence_seconds': None,
        }

    expected_frames = int(round(requested * 30))
    expected_hold = max(0.0, requested - voice_duration)
    # The master is quantized to 30 fps while silence is measured in audio
    # samples. Allow at most one picture frame beyond the existing natural
    # tail/codec allowance. Keep the absolute cap and exact frame-count check.
    # A real 29.520s take yielded 0.737s of silence on its 900-frame master;
    # the former 0.730s bound falsely rejected that sub-frame difference.
    minimum_ending_silence = max(0.15, expected_hold - 0.12)
    maximum_ending_silence = min(1.55, expected_hold + 0.25 + 1 / 30)
    if actual_frames != expected_frames:
        reason = 'final_frame_count_mismatch'
    elif not (
        minimum_ending_silence
        <= ending_silence
        <= maximum_ending_silence
    ):
        reason = 'final_ending_silence_out_of_bounds'
    else:
        reason = None
    return {
        'pass': reason is None,
        'reason': reason,
        'expected_frames': expected_frames,
        'actual_frames': actual_frames,
        'ending_silence_seconds': round(ending_silence, 3),
        'minimum_ending_silence_seconds': round(
            minimum_ending_silence,
            3,
        ),
        'maximum_ending_silence_seconds': round(
            maximum_ending_silence,
            3,
        ),
    }


def _synthesize_voice_candidate(
    scenes: list[dict],
    task_id: str,
    target_seconds: float,
    *,
    start_attempt: int = 0,
    language: str | None = None,
    voice_replacement_request: dict | None = None,
    flexible_short: bool = False,
) -> dict:
    """Use bounded new seeds for synthesis defects without rerunning the job."""
    from app.services.abacus_generation import AbacusGenerationError
    from app.services.production_spend import SpendBlocked

    replacement_options = {}
    maximum_attempts = MAX_AUDIO_GENERATION_ATTEMPTS
    if voice_replacement_request is not None:
        if start_attempt != 0 or target_seconds != 30 or language != 'tr':
            raise FinalAudioQualityError('Voice replacement requires one initial Turkish short take')
        from app.services.voice_replacement import acquire_voice_replacement_attempt
        from app.services.voice_replacement_diagnostic import persist_raw_voice_replacement

        def reserve_replacement(voice_id):
            reservation = acquire_voice_replacement_attempt(
                task_id, voice_replacement_request['source_task_id'],
                voice_replacement_request['audio_sha256'], voice_replacement_request['spec'],
                voice_id=voice_id,
            )
            if (not isinstance(reservation, dict) or reservation.get('model_id') != 'eleven_multilingual_v2'
                    or type(reservation.get('max_attempts')) is not int or reservation['max_attempts'] != 1):
                raise FinalAudioQualityError('Voice replacement reservation could not be verified')

        def preserve_raw_replacement(audio):
            pointer = persist_raw_voice_replacement(task_id, audio)
            update_job(task_id, voice_replacement_raw_audio=pointer)

        replacement_options = {
            'profile_override': 'turkish_multilingual_v2',
            'before_paid_request': reserve_replacement,
            'raw_audio_sink': preserve_raw_replacement,
        }
        maximum_attempts = 1
    quality_errors: list[dict] = []
    voice_transport_failed = False
    for generation_attempt in range(
        max(0, int(start_attempt)),
        maximum_attempts,
    ):
        attempt_task_id = (
            task_id
            if generation_attempt == 0
            else f'{task_id}_audio_retry_{generation_attempt}'
        )
        try:
            result = synthesize_scene_sequence(
                scenes,
                attempt_task_id,
                target_seconds,
                generation_attempt=generation_attempt,
                language=language,
                **({'flexible_short': True} if flexible_short is True and voice_replacement_request is None else {}),
                **replacement_options,
            )
        except VoiceScriptFitError as exc:
            from app.services.production_failures import content_rejection

            raise content_rejection(FinalAudioQualityError(
                'Voice script duration rejected before paid media: '
                + json.dumps(
                    {
                        'generation_attempt': generation_attempt,
                        'reason': str(exc)[:240],
                    },
                    ensure_ascii=False,
                    separators=(',', ':'),
                )
            ), 'audio_quality_exhausted') from None
        except VoiceQualityError as exc:
            quality_errors.append({
                'generation_attempt': generation_attempt,
                'reason': str(exc)[:240],
            })
            continue
        except httpx.HTTPError as exc:
            status_code = (
                exc.response.status_code
                if isinstance(exc, httpx.HTTPStatusError)
                else None
            )
            retryable = is_transient_voice_http_error(exc)
            if retryable:
                voice_transport_failed = True
                quality_errors.append({
                    'generation_attempt': generation_attempt,
                    'reason': (
                        f'voice_provider_http_{status_code}'
                        if status_code is not None
                        else 'voice_provider_transport'
                    ),
                })
                if target_seconds > 40:
                    raise FinalAudioQualityError(
                        'Long-form voice scene retry budget was exhausted '
                        'before paid media'
                    ) from None
                if generation_attempt < maximum_attempts - 1:
                    time.sleep(
                        voice_http_retry_delay_seconds(
                            exc,
                            generation_attempt,
                        )
                    )
                continue
            raise FinalAudioQualityError(
                'Voice provider rejected synthesis before paid media: '
                + json.dumps(
                    {
                        'generation_attempt': generation_attempt,
                        'status_code': status_code,
                    },
                    separators=(',', ':'),
                )
            ) from None
        except (SpendBlocked, AbacusGenerationError):
            # An account or credit refusal is not a synthesis-quality defect.
            # Keep its stable code and original no-replay semantics.
            raise
        except Exception as exc:
            raise FinalAudioQualityError(
                'Voice synthesis failed safely before paid media: '
                + json.dumps(
                    {
                        'generation_attempt': generation_attempt,
                        'error_type': type(exc).__name__[:80],
                    },
                    separators=(',', ':'),
                )
            ) from None
        return {
            **result,
            '_generation_attempt': generation_attempt,
            '_generation_attempts_used': generation_attempt + 1,
            '_synthesis_quality_errors': quality_errors,
        }
    from app.services.production_failures import content_rejection

    raise content_rejection(FinalAudioQualityError(
        'Voice synthesis quality rejected before paid media: '
        + json.dumps(
            {
                'generation_attempts': maximum_attempts,
                'quality_errors': quality_errors,
            },
            ensure_ascii=False,
            separators=(',', ':'),
        )
    ), 'audio_review_unverified' if voice_transport_failed else 'audio_quality_exhausted')


def _audio_provider_evidence_sink(task_id: str, audio_path: str | Path):
    """Record bounded unapproved provider evidence without changing any QA decision."""
    from app.services.audio_evidence import persist_audio_provider_evidence

    pointers = []

    def save(**evidence):
        try:
            pointer = persist_audio_provider_evidence(task_id, audio_path, **evidence)
            if pointer not in pointers:
                pointers.append(pointer)
            update_job(task_id, audio_provider_evidence=pointers[-6:])
        except Exception:
            # Evidence storage is diagnostic only; never expose provider responses
            # or turn a failed audio gate into an approved candidate.
            pass

    return save


def _verify_audio_narration_with_retry(
    audio_path: str | Path,
    expected_narration: str,
    *,
    language: str,
    task_id: str | None = None,
) -> dict:
    """Retry a transient STT outage without regenerating immutable audio."""
    from app.services.whisper_transcription import WhisperTranscriptionError
    from app.services.production_failures import content_rejection

    provider_attempts = []
    evidence_options = (
        {'provider_evidence_sink': _audio_provider_evidence_sink(task_id, audio_path)}
        if task_id else {}
    )
    for attempt in range(AUDIO_QC_PROVIDER_ATTEMPTS):
        try:
            return verify_audio_narration(
                audio_path,
                expected_narration,
                language=language,
                **evidence_options,
            )
        except AudioQCError as exc:
            provider_attempts.append({
                'attempt': attempt + 1,
                'providers': audio_qc_provider_diagnostics(exc),
            })
            if attempt + 1 < AUDIO_QC_PROVIDER_ATTEMPTS:
                time.sleep(AUDIO_QC_PROVIDER_RETRY_DELAY_SECONDS)
                continue
            error = FinalAudioQualityError(
                'Audio narration QA could not be verified after one '
                'bounded same-audio retry before paid media: '
                + json.dumps({'provider_attempts': provider_attempts}, separators=(',', ':'))
            )
            error.audio_qc_diagnostics = provider_attempts
            raise content_rejection(error, 'audio_review_unverified') from None
        except WhisperTranscriptionError:
            # A reserved native request is terminal even before video creates.
            # The same audio must not trigger a provider or Celery retry.
            raise content_rejection(FinalAudioQualityError(
                'Reserved audio verification stopped without another paid request'
            ), 'audio_review_unverified') from None
    raise content_rejection(FinalAudioQualityError(
        'Audio narration QA providers were unavailable before paid media'
    ), 'audio_review_unverified')


def _is_transient_pexels_provider_error(exc: Exception) -> bool:
    """Return true only for temporary provider/transport failures.

    Authentication, authorization, request-shape, decoding, configuration,
    programming, and local filesystem failures must remain visible and must
    never authorize a paid fallback.
    """
    if isinstance(
        exc,
        (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ),
    ):
        return True
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    response = getattr(exc, 'response', None)
    try:
        status_code = int(response.status_code)
    except Exception:
        return False
    return (
        status_code in _TRANSIENT_PEXELS_HTTP_STATUS_CODES
        or 500 <= status_code <= 599
    )


def _normalized_options(options: dict | None, duration_minutes: float) -> dict:
    value = dict(options or {})
    value.setdefault('mode', 'preview' if duration_minutes <= 1 else 'production')
    requested_format = value.get('format')
    if requested_format is None:
        requested_format = 'shorts' if value['mode'] == 'preview' else 'landscape'
    if not isinstance(requested_format, str) or requested_format.strip().lower() not in {'shorts', 'landscape'}:
        raise ValueError('Video format must be shorts or landscape')
    value['format'] = requested_format.strip().lower()
    requested_publish_after_render = value.get('publish_after_render')
    value.setdefault('workflow', 'auto')
    value.setdefault('content_style', 'documentary')
    value.setdefault('pace', 'balanced')
    value.setdefault('visual_mix', 'balanced')
    value.setdefault('music', 'off' if value['mode'] == 'preview' else 'auto')
    value.setdefault('subtitles', 'sidecar')
    value.setdefault('reference_url', None)
    quality_floor = 84 if value['mode'] == 'production' else 86
    try:
        requested_quality = int(value.get('quality_threshold', quality_floor))
    except Exception:
        requested_quality = quality_floor
    value['quality_threshold'] = max(quality_floor, requested_quality)
    if value['mode'] == 'preview':
        value['music'] = 'off'
    value['publish_after_render'] = bool(
        requested_publish_after_render is True
        and value['mode'] == 'production'
    )
    if value.get('production_delivery') is not None:
        from app.services.production_delivery import delivery_requested
        from app.services.production_delivery_runtime import enabled

        delivery_requested(value, duration_minutes)
        if not enabled():
            raise ValueError('Long-form delivery and its spending guard are not enabled')
    return value


def _record_publish_queue_outcome(task_id: str, outcome: dict) -> None:
    """Fill a missing routing outcome without replacing a real publish state."""
    allowed_statuses = {
        'queued', 'reserved', 'uploading', 'uploaded', 'completed', 'uncertain',
        'already_reserved', 'failed_preflight', 'quality_blocked',
        'preview_blocked', 'not_enabled', 'no_unique_route',
        'connection_missing', 'connection_changed', 'profile_changed',
        'metadata_blocked', 'reservation_blocked', 'queue_blocked', 'queue_error',
    }
    try:
        from app.services.studio_state import merge_youtube_result_field

        status = outcome.get('status')
        automation = {
            'status': status if isinstance(status, str) and status in allowed_statuses else 'queue_error',
        }
        publish_task_id = outcome.get('publish_task_id')
        if isinstance(publish_task_id, str) and re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', publish_task_id):
            automation['publish_task_id'] = publish_task_id
        merge_youtube_result_field(
            task_id, 'youtube_automation', automation, only_if_missing=True,
        )
    except Exception:
        # Registry failure must not discard an already rendered master.
        return


def _queue_automatic_publish_if_enabled(task_id: str, options: dict) -> bool:
    """Best-effort autonomous routing after an explicit production opt-in."""
    if (
        options.get('publish_after_render') is not True
        or options.get('mode') != 'production'
    ):
        return False
    try:
        from app.publish_tasks import queue_automatic_publish

        outcome = queue_automatic_publish(task_id)
    except Exception:
        _record_publish_queue_outcome(task_id, {'status': 'queue_error'})
        return False
    if not isinstance(outcome, dict):
        _record_publish_queue_outcome(task_id, {'status': 'queue_error'})
        return False
    _record_publish_queue_outcome(task_id, outcome)
    return outcome.get('status') == 'queued'


def _task_spec(topic: str, duration_minutes: float, language: str, channel_id: str | None, options: dict) -> dict:
    return {
        'topic': topic,
        'duration_minutes': duration_minutes,
        'language': language,
        'channel_id': channel_id,
        **options,
    }


def _select_ranked_broll_candidates(
    query_results: list[tuple[str, list[dict]]],
    seen_ids: set,
    limit: int,
    minimum_duration: float = 5.0,
    allow_seen_fallback: bool = True,
    allow_short_fallback: bool = True,
    orientation: str = 'landscape',
    query_diverse_first: bool = False,
) -> list[tuple[str, dict]]:
    """Select a stable relevance-first, query-diverse Pexels candidate pool."""
    orientation = str(orientation or '').strip().lower()
    if orientation not in {'landscape', 'portrait'}:
        raise ValueError('Pexels task orientation must be landscape or portrait')
    limit = max(1, int(limit))
    selected: list[tuple[str, dict]] = []
    selected_ids: set = set()

    def candidate_key(item: dict):
        return item.get('pexels_id') or item.get('download_url')

    def usable(item: dict, require_unseen: bool, require_duration: bool) -> bool:
        key = candidate_key(item)
        if not key or key in selected_ids:
            return False
        if require_unseen and key in seen_ids:
            return False
        width = int(item.get('width') or 0)
        height = int(item.get('height') or 0)
        if width and height:
            if orientation == 'portrait' and width >= height:
                return False
            if orientation == 'landscape' and width < height:
                return False
        try:
            duration = float(item.get('duration') or 0)
        except Exception:
            duration = 0
        if require_duration and duration < minimum_duration:
            return False
        return bool(item.get('download_url'))

    max_rank = max(
        (len(candidates) for _query, candidates in query_results),
        default=0,
    )
    # Prefer globally new, >=5-second clips. Duration may relax when needed;
    # global uniqueness relaxes only for callers that explicitly permit it.
    selection_phases = [(True, True)]
    if allow_short_fallback:
        selection_phases.append((True, False))
    if allow_seen_fallback:
        selection_phases.append((False, True))
        if allow_short_fallback:
            selection_phases.append((False, False))
    if query_diverse_first:
        # The bounded overflow rescue must actually sample both queries,
        # even when one query's first usable clip is farther down its list.
        for query, candidates in query_results:
            for require_unseen, require_duration in selection_phases:
                first_usable = next((
                    item for item in candidates
                    if usable(item, require_unseen, require_duration)
                ), None)
                if first_usable is None:
                    continue
                selected.append((query, first_usable))
                selected_ids.add(candidate_key(first_usable))
                break
            if len(selected) >= limit:
                return selected
    for require_unseen, require_duration in selection_phases:
        for rank in range(max_rank):
            for query, candidates in query_results:
                if rank >= len(candidates):
                    continue
                item = candidates[rank]
                if not usable(item, require_unseen, require_duration):
                    continue
                selected.append((query, item))
                selected_ids.add(candidate_key(item))
                if len(selected) >= limit:
                    return selected
    return selected


def _collect_broll(
    scenes: list[dict],
    work: Path,
    strict_duration: bool = False,
    orientation: str = 'landscape',
) -> dict:
    orientation = str(orientation or '').strip().lower()
    if orientation not in {'landscape', 'portrait'}:
        raise ValueError('Pexels task orientation must be landscape or portrait')
    scene_visuals: list[list[dict]] = [[] for _ in scenes]
    credits: list[dict] = []
    seen_ids: set[int | str] = set()
    requests: list[tuple[int, int, str]] = []
    for scene_idx, scene in enumerate(scenes):
        queries = [q for q in scene.get('visual_queries', []) if isinstance(q, str) and q.strip()][:3]
        for query_idx, query in enumerate(queries):
            requests.append((scene_idx, query_idx, query))

    search_results: dict[tuple[int, int, str], list[dict]] = {}
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(requests)))) as executor:
        future_map = {
            executor.submit(
                find_broll,
                query,
                16,
                orientation=orientation,
            ): (scene_idx, query_idx, query)
            for scene_idx, query_idx, query in requests
        }
        for future in as_completed(future_map):
            key = future_map[future]
            try:
                search_results[key] = future.result() or []
            except Exception:
                search_results[key] = []

    download_specs: list[tuple[int, int, str, dict, Path]] = []
    selection_seen_ids = set(seen_ids)
    for scene_idx in range(len(scenes)):
        scene_query_results = [
            (
                query,
                search_results.get((request_scene_idx, query_idx, query)) or [],
            )
            for request_scene_idx, query_idx, query in requests
            if request_scene_idx == scene_idx
        ]
        selected = _select_ranked_broll_candidates(
            scene_query_results,
            selection_seen_ids,
            3,
            allow_seen_fallback=False,
            allow_short_fallback=not strict_duration,
            orientation=orientation,
        )
        for candidate_idx, (query, item) in enumerate(selected):
            candidate_id = item.get('pexels_id') or item.get('download_url')
            if candidate_id:
                selection_seen_ids.add(candidate_id)
            path = work / f'pexels_s{scene_idx:02d}_{candidate_idx:02d}.mp4'
            download_specs.append((
                scene_idx,
                candidate_idx,
                query,
                item,
                path,
            ))

    def download_one(spec):
        scene_idx, query_idx, query, item, path = spec
        download_broll(item, path)
        return scene_idx, query_idx, query, item, path

    downloaded: list[tuple[int, int, str, dict, Path]] = []
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(download_specs)))) as executor:
        futures = [executor.submit(download_one, spec) for spec in download_specs]
        for future in as_completed(futures):
            try:
                downloaded.append(future.result())
            except Exception:
                continue

    # Network completion order must never change the candidate indices later
    # consumed by visual QC.
    for scene_idx, candidate_idx, query, item, path in sorted(
        downloaded,
        key=lambda result: (result[0], result[1]),
    ):
        candidate_id = item.get('pexels_id') or item.get('download_url')
        if candidate_id:
            seen_ids.add(candidate_id)
        try:
            source_duration = float(item.get('duration') or 0)
        except Exception:
            source_duration = 0.0
        scene_visuals[scene_idx].append({
            'path': str(path),
            'pexels_id': (
                candidate_id if type(candidate_id) is int and candidate_id > 0 else None
            ),
            'start_fraction': 0.25,
            'source_duration': source_duration,
            'source_type': 'stock',
            'stock_provider': 'pexels',
        })
        credits.append({
            'source': 'Pexels',
            'scene_index': scene_idx,
            'query': query,
            'creator_name': item.get('creator_name'),
            'creator_url': item.get('creator_url'),
            'page_url': item.get('page_url'),
            'pexels_id': item.get('pexels_id'),
            'selected_by': 'candidate_pool',
        })
    return {'scene_visuals': scene_visuals, 'credits': credits, 'seen_ids': seen_ids}


def _broll_retry_blocked_ids(
    scene_idx: int,
    seen_ids: set,
    credits: list[dict],
    active_scene_visuals: list[list[str | dict]],
) -> set:
    """Release only proven cross-scene losers, never active or own candidates."""
    blocked = set(seen_ids)
    if (
        type(scene_idx) is not int
        or not isinstance(active_scene_visuals, list)
        or not 0 <= scene_idx < len(active_scene_visuals)
        or not isinstance(credits, list)
    ):
        return blocked
    held: set[int] = set()
    unknown_stock_identity = False
    for specs in active_scene_visuals:
        if not isinstance(specs, list):
            unknown_stock_identity = True
            continue
        for spec in specs:
            if isinstance(spec, dict) and (
                spec.get('generated') is True or spec.get('source_type') == 'generated'
            ):
                continue
            candidate_id = spec.get('pexels_id') if isinstance(spec, dict) else None
            if (
                isinstance(spec, dict)
                and spec.get('source_type') == 'stock'
                and spec.get('stock_provider') == 'pexels'
                and type(candidate_id) is int and candidate_id > 0
            ):
                held.add(candidate_id)
            else:
                # A legacy path may still refer to any previously seen asset.
                unknown_stock_identity = True
    downloaded: set[int] = set()
    own_attempted: set[int] = set()
    for credit in credits:
        if not isinstance(credit, dict) or credit.get('source') != 'Pexels':
            continue
        candidate_id = credit.get('pexels_id')
        owner = credit.get('scene_index')
        if (
            type(candidate_id) is not int or candidate_id <= 0
            or type(owner) is not int or not 0 <= owner < len(active_scene_visuals)
        ):
            continue
        downloaded.add(candidate_id)
        if owner == scene_idx:
            own_attempted.add(candidate_id)
    blocked.update(held | own_attempted)
    if not unknown_stock_identity:
        blocked.difference_update(downloaded - held - own_attempted)
    return blocked


def _require_unique_selected_stock(scene_visuals: list[list[str | dict]]) -> None:
    """Check known stock identities only; legacy/general reuse is caller-scoped."""
    owners: dict[int, int] = {}
    for scene_idx, specs in enumerate(scene_visuals):
        for spec in specs:
            if not isinstance(spec, dict) or (
                spec.get('source_type') != 'stock' or spec.get('stock_provider') != 'pexels'
            ):
                continue
            candidate_id = spec.get('pexels_id')
            if type(candidate_id) is not int or candidate_id <= 0:
                continue
            if candidate_id in owners and owners[candidate_id] != scene_idx:
                raise FinalVisualQualityError(
                    'Final stock uniqueness gate rejected repeated Pexels media'
                )
            owners[candidate_id] = scene_idx


def _download_ranked_broll_candidates(
    scene_idx: int,
    queries: list[str],
    seen_ids: set,
    work: Path,
    credits: list[dict],
    *,
    file_prefix: str,
    selected_by: str,
    max_candidates: int,
    search_limit: int,
    minimum_duration: float = 5.0,
    allow_short_fallback: bool = False,
    orientation: str = 'landscape',
    active_scene_visuals: list[list[str | dict]] | None = None,
) -> list[dict]:
    orientation = str(orientation or '').strip().lower()
    if orientation not in {'landscape', 'portrait'}:
        raise ValueError('Pexels task orientation must be landscape or portrait')
    normalized_queries: list[str] = []
    query_keys: set[str] = set()
    for raw_query in queries:
        query = str(raw_query or '').strip()
        key = query.casefold()
        if not query or key in query_keys:
            continue
        query_keys.add(key)
        normalized_queries.append(query)
        if len(normalized_queries) >= 3:
            break
    if not normalized_queries or max_candidates <= 0:
        return []

    search_results: dict[int, list[dict]] = {}
    search_errors: list[Exception] = []
    with ThreadPoolExecutor(max_workers=min(3, len(normalized_queries))) as retry_pool:
        future_map = {
            retry_pool.submit(
                find_broll,
                query,
                search_limit,
                orientation=orientation,
            ): query_idx
            for query_idx, query in enumerate(normalized_queries)
        }
        for future in as_completed(future_map):
            query_idx = future_map[future]
            try:
                search_results[query_idx] = future.result() or []
            except Exception as exc:
                search_errors.append(exc)

    non_transient_search_error = next(
        (
            exc
            for exc in search_errors
            if not _is_transient_pexels_provider_error(exc)
        ),
        None,
    )
    if non_transient_search_error is not None:
        raise non_transient_search_error

    if not search_results and search_errors:
        # A burst of parallel read-only searches can be disconnected by the
        # provider even though the same endpoint is healthy. Give every query
        # one bounded sequential second chance before declaring that provider
        # evidence is unavailable. This never lowers the visual-quality gate
        # and never creates a paid-media request.
        sequential_errors: list[Exception] = []
        for query_idx, query in enumerate(normalized_queries):
            try:
                search_results[query_idx] = (
                    find_broll(
                        query,
                        search_limit,
                        orientation=orientation,
                    ) or []
                )
            except Exception as exc:
                if not _is_transient_pexels_provider_error(exc):
                    raise
                sequential_errors.append(exc)
        if not search_results:
            final_errors = sequential_errors or search_errors
            raise PexelsRetryError(
                f'Pexels retry search failed for scene {scene_idx}'
            ) from final_errors[0]

    success_target = min(5, max(1, int(max_candidates)))
    attempt_limit = min(7, success_target + 2)
    ranked = _select_ranked_broll_candidates(
        [
            (query, search_results.get(query_idx, []))
            for query_idx, query in enumerate(normalized_queries)
        ],
        (
            _broll_retry_blocked_ids(scene_idx, seen_ids, credits, active_scene_visuals)
            if active_scene_visuals is not None else seen_ids
        ),
        attempt_limit,
        minimum_duration=max(0.1, float(minimum_duration)),
        allow_seen_fallback=False,
        allow_short_fallback=allow_short_fallback,
        orientation=orientation,
        query_diverse_first=(selected_by == 'pre_runway_budget_rescue'),
    )
    if not ranked:
        return []

    safe_prefix = re.sub(r'[^a-zA-Z0-9_-]+', '_', file_prefix)[:32] or 'qc'
    download_errors: list[Exception] = []

    def download_one(candidate_idx: int, query: str, item: dict):
        path = work / f'{safe_prefix}_s{scene_idx:02d}_{candidate_idx:02d}.mp4'
        download_broll(item, path)
        return candidate_idx, query, item, path

    downloaded: dict[int, tuple[str, dict, Path]] = {}
    initial_batch = list(enumerate(ranked[:success_target]))
    with ThreadPoolExecutor(max_workers=min(5, len(initial_batch))) as download_pool:
        future_map = {
            download_pool.submit(download_one, candidate_idx, query, item): candidate_idx
            for candidate_idx, (query, item) in initial_batch
        }
        for future in as_completed(future_map):
            try:
                candidate_idx, query, item, path = future.result()
                downloaded[candidate_idx] = (query, item, path)
            except Exception as exc:
                download_errors.append(exc)

    non_transient_download_error = next(
        (
            exc
            for exc in download_errors
            if not _is_transient_pexels_provider_error(exc)
        ),
        None,
    )
    if non_transient_download_error is not None:
        raise non_transient_download_error

    # A transient CDN failure must not silently shrink the QC pool. Backfill
    # from at most two further ranked candidates, stopping at the success cap.
    for candidate_idx, (query, item) in enumerate(ranked[success_target:], start=success_target):
        if len(downloaded) >= success_target:
            break
        try:
            _candidate_idx, _query, _item, path = download_one(candidate_idx, query, item)
            downloaded[candidate_idx] = (_query, _item, path)
        except Exception as exc:
            if not _is_transient_pexels_provider_error(exc):
                raise
            download_errors.append(exc)

    replacements: list[dict] = []
    for candidate_idx in sorted(downloaded):
        query, item, path = downloaded[candidate_idx]
        candidate_id = item.get('pexels_id') or item.get('download_url')
        if candidate_id:
            seen_ids.add(candidate_id)
        try:
            source_duration = float(item.get('duration') or 0)
        except Exception:
            source_duration = 0.0
        replacements.append({
            'path': str(path),
            'pexels_id': (
                candidate_id if type(candidate_id) is int and candidate_id > 0 else None
            ),
            'start_fraction': 0.35,
            'source_duration': source_duration,
            'source_type': 'stock',
            'stock_provider': 'pexels',
        })
        credits.append({
            'source': 'Pexels',
            'scene_index': scene_idx,
            'query': query,
            'creator_name': item.get('creator_name'),
            'creator_url': item.get('creator_url'),
            'page_url': item.get('page_url'),
            'pexels_id': item.get('pexels_id'),
            'selected_by': selected_by,
        })

    if ranked and not replacements and download_errors:
        raise PexelsRetryError(
            f'Pexels retry download failed for scene {scene_idx}'
        ) from download_errors[0]
    return replacements


def _retry_bad_scene(
    scene_idx: int,
    retry_queries: list[str],
    seen_ids: set,
    work: Path,
    credits: list[dict],
    file_prefix: str = 'qc',
    max_replacements: int = 1,
    minimum_duration: float = 5.0,
    allow_short_fallback: bool = True,
    tolerate_pexels_failure: bool = False,
    orientation: str = 'landscape',
    active_scene_visuals: list[list[str | dict]] | None = None,
) -> list[dict]:
    if (
        active_scene_visuals is not None and 0 <= scene_idx < len(active_scene_visuals)
        and any(isinstance(spec, dict) and spec.get('curated_pinned') is True
                for spec in active_scene_visuals[scene_idx])
    ):
        # A server-curated cut may fail QA, but must never silently search for
        # another asset. Its original rejection remains the terminal outcome.
        return []
    safe_prefix = re.sub(r'[^a-zA-Z0-9_-]+', '_', file_prefix)[:32] or 'qc'
    selected_by = (
        'final_visual_qc_rescue' if safe_prefix == 'final_qc_rescue'
        else 'pre_runway_budget_rescue' if safe_prefix == 'pre_runway_budget_rescue'
        else 'pre_runway_stock_contract' if safe_prefix == 'pre_runway_stock_contract'
        else 'pre_runway_duration_refill' if safe_prefix == 'duration_refill'
        else 'visual_qc_retry'
    )
    try:
        return _download_ranked_broll_candidates(
            scene_idx,
            retry_queries[:2],
            seen_ids,
            work,
            credits,
            file_prefix=safe_prefix,
            selected_by=selected_by,
            max_candidates=max_replacements,
            search_limit=18,
            minimum_duration=minimum_duration,
            allow_short_fallback=allow_short_fallback,
            orientation=orientation,
            active_scene_visuals=active_scene_visuals,
        )
    except PexelsRetryError:
        if not tolerate_pexels_failure:
            raise
        return []


def _final_pexels_rescue_queries(
    scene: dict,
    review: dict,
    *,
    forced_stock_fallback: bool,
) -> list[str]:
    """Keep final rescue bounded while allowing a recovered provider retry."""
    raw_retry_queries = review.get('retry_queries') or []
    if isinstance(raw_retry_queries, str):
        raw_retry_queries = [raw_retry_queries]
    retry_queries = [
        str(query).strip()
        for query in raw_retry_queries[:2]
        if str(query).strip()
    ]
    if retry_queries or not forced_stock_fallback:
        return retry_queries

    raw_visual_queries = scene.get('visual_queries') or []
    if isinstance(raw_visual_queries, str):
        raw_visual_queries = [raw_visual_queries]
    return [
        str(query).strip()
        for query in raw_visual_queries[:2]
        if str(query).strip()
    ]


def _prepaid_stock_rescue_queries(scene: dict, review: dict) -> list[str]:
    """Use at most two distinct critique queries, or authored hints if absent."""
    for raw_queries in (
        review.get('retry_queries'), scene.get('visual_queries'),
    ):
        if isinstance(raw_queries, str):
            raw_queries = [raw_queries]
        if not isinstance(raw_queries, list):
            continue
        queries: list[str] = []
        seen: set[str] = set()
        for raw_query in raw_queries:
            if not isinstance(raw_query, str):
                continue
            query = ' '.join(raw_query.split())
            key = query.casefold()
            if not query or key in seen:
                continue
            queries.append(query)
            seen.add(key)
            if len(queries) == 2:
                break
        if queries:
            return queries
    return []


def _prepaid_stock_rescue_candidates(
    ranked_candidates: list[dict],
    submission_cap: int,
    *,
    options: dict,
    duration_minutes: float,
    scenes: list[dict],
    evidence_sources: list[dict] | None,
    quality_threshold: int,
) -> list[dict]:
    """Rescue rejected stock before reserving paid slots in a tiny documentary."""
    overflow = ranked_candidates[submission_cap:]
    if not (
        options.get('mode') == 'production'
        and options.get('format') == 'shorts'
        and str(options.get('content_style') or '').strip().casefold() == 'documentary'
        and duration_minutes == 0.5
        and 1 <= len(scenes) <= 6
        and type(quality_threshold) is int and 0 <= quality_threshold <= 100
    ):
        return overflow
    from app.services.source_evidence import normalize_evidence_sources

    try:
        normalize_evidence_sources(evidence_sources, min_count=1, max_count=5)
    except (TypeError, ValueError):
        return overflow
    selected = []
    seen: set[int] = set()
    for candidate in ranked_candidates:
        index = candidate.get('scene_index')
        score = candidate.get('stock_score')
        if (
            type(index) is int and 0 <= index < len(scenes) and index not in seen
            and not str(scenes[index].get('ai_prompt') or '').strip()
            and type(score) in (int, float) and math.isfinite(score)
            and score < quality_threshold
        ):
            selected.append(candidate)
            seen.add(index)
    return selected


def _visual_path(spec: str | dict) -> str:
    if isinstance(spec, dict):
        return str(spec.get('path') or '')
    return str(spec)


def _is_generated_visual_spec(spec: str | dict | None) -> bool:
    if not isinstance(spec, dict):
        return False
    return (
        spec.get('generated') is True
        or str(spec.get('source_type') or '').casefold() == 'generated'
        or (
            spec.get('forbid_loop') is True
            and spec.get('preserve_start_fraction') is True
        )
    )


def _reviewed_visual_spec(
    specs: list[str | dict],
    review: dict,
) -> str | dict | None:
    if not specs:
        return None
    try:
        candidate_idx = int(review.get('best_candidate_index', 0))
    except Exception:
        return None
    if candidate_idx < 0 or candidate_idx >= len(specs):
        return None
    return specs[candidate_idx]


def _manual_qa_visual_source_type(
    visual_spec: str | dict | None,
) -> str | None:
    if (
        not isinstance(visual_spec, dict)
        or not str(visual_spec.get('path') or '').strip()
    ):
        return None
    if _is_generated_visual_spec(visual_spec):
        return 'generated'
    if (
        str(visual_spec.get('source_type') or '').casefold() == 'stock'
        and str(visual_spec.get('stock_provider') or '').casefold()
        == 'pexels'
    ):
        return 'stock'
    return None


def _manual_qa_preview_passes(
    options: dict,
    duration_minutes: float,
    scene: dict,
    review: dict,
    visual_spec: str | dict | None,
) -> bool:
    """Fail closed for a private-review-only, exact 30-second preview clip."""
    quality_threshold = options.get('quality_threshold')
    if (
        options.get('mode') != 'preview'
        or duration_minutes != 0.5
        or type(quality_threshold) is not int
        or quality_threshold != MANUAL_QA_PUBLISH_QUALITY_THRESHOLD
    ):
        return False
    if isinstance(visual_spec, dict) and (
        visual_spec.get('synthetic_motion_only') is True
        or str(
            visual_spec.get('generation_provider') or ''
        ).strip().casefold() == 'gemini_image_motion'
    ):
        # A generated still with a deterministic pan/zoom is useful only as a
        # placeholder.  It cannot use the private manual-QA score exception to
        # survive into a result that the quality loop treats as reviewable.
        return False
    source_type = _manual_qa_visual_source_type(visual_spec)
    if source_type is None:
        return False
    floor = (
        MANUAL_QA_PREVIEW_GENERATED_FLOOR
        if source_type == 'generated'
        else MANUAL_QA_PREVIEW_STOCK_FLOOR
    )
    score = review.get('score')
    return (
        type(score) is int
        and floor <= score < MANUAL_QA_PUBLISH_QUALITY_THRESHOLD
        and review.get('evidence_gate_passed') is True
        and review.get('editorial_gate_passed') is True
        and review.get('identity_gate_passed') is True
        and review.get('subject_visible') is True
        and review.get('spoken_action_visible') is True
        and review.get('unexplained_reset') is False
        and all(
            review.get(field) is False
            for field in _MANUAL_QA_CLEAR_VISUAL_FIELDS
        )
    )


def _manual_qa_preview_record(
    scene_idx: int,
    review: dict,
    visual_spec: str | dict | None,
) -> dict:
    retry_queries = review.get('retry_queries') or []
    if isinstance(retry_queries, str):
        retry_queries = [retry_queries]
    if not isinstance(retry_queries, list):
        retry_queries = []
    return {
        'scene_index': int(scene_idx),
        'score': int(review.get('score', 0)),
        'source_type': _manual_qa_visual_source_type(visual_spec),
        'retry_queries': [
            str(query).strip()[:240]
            for query in retry_queries[:2]
            if str(query).strip()
        ],
        'manual_reason': str(review.get('reason') or '')[:500],
    }


_MANUAL_QA_DIAGNOSTIC_BOOLEAN_FIELDS = (
    'evidence_gate_passed',
    'editorial_gate_passed',
    'identity_gate_passed',
    'manufactured_replica_required',
    'authored_identity_or_material_conflict_visible',
    'manufactured_object_cues_visible',
    'subject_visible',
    'spoken_action_visible',
    'thermal_claim_applicable',
    'thermal_evidence_visible',
    'physical_causality_applicable',
    'target_contact_visible',
    'connection_action_applicable',
    'moving_connector_visible',
    'receiving_interface_visible',
    'connector_visibly_joins_target',
    'connection_persists_after_release',
    'state_change_applicable',
    'state_changed_after_action',
    'final_state_persists',
    'unexplained_reset',
    'location_continuity_applicable',
    'location_continuity_matches',
    *_MANUAL_QA_CLEAR_VISUAL_FIELDS,
)


def _manual_qa_visual_identity(
    visual_spec: str | dict | None,
) -> tuple[object, ...] | None:
    """Internal content-and-cut identity; never serialize source paths."""
    source_type = _manual_qa_visual_source_type(visual_spec)
    if source_type is None:
        return None
    if not isinstance(visual_spec, dict):
        return None
    path = str(visual_spec.get('path') or '').strip()
    if not path:
        return None
    try:
        clip = Path(path)
        size = clip.stat().st_size
        digest = hashlib.sha256()
        with clip.open('rb') as file_handle:
            for chunk in iter(lambda: file_handle.read(1024 * 1024), b''):
                digest.update(chunk)
        start_fraction = round(
            float(visual_spec.get('start_fraction', 0.35)),
            6,
        )
        source_duration = round(
            float(visual_spec.get('source_duration', 0.0)),
            6,
        )
    except (OSError, TypeError, ValueError):
        return None
    if (
        size < 1
        or not 0.0 <= start_fraction <= 0.95
        or source_duration < 0.0
    ):
        return None
    return (
        digest.hexdigest(),
        size,
        start_fraction,
        source_duration,
        source_type,
        str(visual_spec.get('stock_provider') or '').casefold(),
        str(visual_spec.get('generation_provider') or '').casefold(),
        visual_spec.get('forbid_loop') is True,
        visual_spec.get('preserve_full_clip') is True,
    )


def _manual_qa_failure_diagnostic(
    scene_idx: int,
    review: dict,
    visual_spec: str | dict | None,
) -> dict:
    """Return an allow-listed, secret-safe exact-QC failure record."""
    source_type = _manual_qa_visual_source_type(visual_spec)
    diagnostic: dict = {
        'scene_index': int(scene_idx),
        'failure_code': 'manual_qa_exact_revalidation_failed',
        'source_type': source_type,
        'manual_qa_floor': (
            MANUAL_QA_PREVIEW_GENERATED_FLOOR
            if source_type == 'generated'
            else MANUAL_QA_PREVIEW_STOCK_FLOOR
            if source_type == 'stock'
            else None
        ),
    }
    for field in (
        'score',
        'raw_score',
        'best_candidate_index',
        'best_moment_index',
    ):
        value = review.get(field)
        diagnostic[field] = value if type(value) is int else None
    for field in _MANUAL_QA_DIAGNOSTIC_BOOLEAN_FIELDS:
        value = review.get(field)
        diagnostic[field] = value if type(value) is bool else None
    moments = review.get('evidence_moment_indices')
    diagnostic['evidence_moment_indices'] = (
        list(moments)
        if (
            isinstance(moments, list)
            and all(type(value) is int for value in moments)
        )
        else None
    )
    return diagnostic


def _manual_qa_review_matches_locked_cut(
    review: dict,
    visual_spec: str | dict | None,
) -> bool:
    """Require a one-candidate tie-breaker to select the pre-approved cut."""
    if not isinstance(visual_spec, dict):
        return False
    if review.get('best_candidate_index') != 0:
        return False
    try:
        reviewed_fraction = round(
            float(review.get('best_start_fraction')),
            6,
        )
        locked_fraction = round(
            float(visual_spec.get('start_fraction', 0.35)),
            6,
        )
    except (TypeError, ValueError):
        return False
    return reviewed_fraction == locked_fraction


def _manual_qa_preview_decisions(
    options: dict,
    duration_minutes: float,
    scenes: list[dict],
    reviews: dict[int, dict],
    scene_visuals: list[list[str | dict]],
    scene_indices: list[int] | set[int],
) -> tuple[dict[int, dict], list[int]]:
    """Classify exact low-score clips without mutating pipeline state."""
    accepted: dict[int, dict] = {}
    rejected: list[int] = []
    for scene_idx in sorted(set(scene_indices)):
        if (
            scene_idx < 0
            or scene_idx >= len(scenes)
            or scene_idx >= len(scene_visuals)
        ):
            rejected.append(scene_idx)
            continue
        review = reviews.get(scene_idx) or {}
        selected_spec = _reviewed_visual_spec(
            scene_visuals[scene_idx],
            review,
        )
        if not _manual_qa_preview_passes(
            options,
            duration_minutes,
            scenes[scene_idx],
            review,
            selected_spec,
        ):
            rejected.append(scene_idx)
            continue
        accepted[scene_idx] = _manual_qa_preview_record(
            scene_idx,
            review,
            selected_spec,
        )
    return accepted, rejected


def _truncate_utf16(text: str, limit: int = 1000) -> str:
    result: list[str] = []
    units = 0
    for char in str(text or ''):
        char_units = 2 if ord(char) > 0xFFFF else 1
        if units + char_units > limit:
            break
        result.append(char)
        units += char_units
    return ''.join(result).strip()


def _sanitize_provider_visual_text(value: object) -> str:
    """Remove presentation bait from derived provider-only visual prompts."""
    text = str(value or '').strip()
    substitutions = (
        (
            r'\b(?:for\s+youtube\s+shorts?|'
            r'youtube\s+shorts?[-\s]+style|'
            r'for\s+tiktok|tiktok[-\s]+style|'
            r'for\s+instagram\s+reels?|'
            r'instagram\s+reels?[-\s]+style)\b',
            'clean documentary footage',
        ),
        (r'(?<![\w@])@[A-Za-z0-9._-]{2,64}(?![A-Za-z0-9._-])', ''),
        (
            r'\b(?:subscribe|follow|like|share)\s+'
            r'(?:button|icon|badge|overlay)\b',
            '',
        ),
        (
            r'\b(?:shorts?|reels?)\s+'
            r'(?:logo|icon|badge|watermark|overlay|interface|ui)\b',
            '',
        ),
    )
    for pattern, replacement in substitutions:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', text).strip(' .,:;-')


def _identity_proof_clause(review: object) -> str:
    """Protect exact-subject evidence only after an identity hard-gate."""
    if not isinstance(review, dict):
        return ''
    identity_evidence_required = (
        review.get('subject_visible') is False
        or review.get('authored_identity_or_material_conflict_visible') is True
        or (
            review.get('recurring_identity_continuity_applicable') is True
            and review.get('recurring_identity_continuity_matches') is not True
        )
    )
    if not identity_evidence_required:
        return ''
    return (
        'IDENTITY PROOF: exact subject large in frame, never physically '
        'enlarged; authored scale; visible comparable close-up; no substitute. '
    )


def _runway_prompt_for_scene(
    scene: dict,
    review: dict | None,
    aspect_ratio: str = '16:9',
) -> str:
    aspect_ratio = str(aspect_ratio or '').strip()
    if aspect_ratio not in {'16:9', '9:16'}:
        raise ValueError('Generation aspect ratio must be 16:9 or 9:16')
    original = _sanitize_provider_visual_text(scene.get('ai_prompt'))
    review = review if isinstance(review, dict) else {}
    identity_evidence_clause = _identity_proof_clause(review)
    retry_queries = review.get('retry_queries') or []
    if isinstance(retry_queries, str):
        retry_queries = [retry_queries]
    hints = [
        _sanitize_provider_visual_text(q)
        for q in retry_queries
        if _sanitize_provider_visual_text(q)
    ][:2]
    has_repair_evidence = bool(hints)
    if not hints:
        visual_queries = scene.get('visual_queries') or []
        if isinstance(visual_queries, str):
            visual_queries = [visual_queries]
        hints = [
            _sanitize_provider_visual_text(q)
            for q in visual_queries
            if _sanitize_provider_visual_text(q)
        ][:2]
    if not original and not hints:
        return ''
    # Keep deterministic headroom below the provider's 1000 UTF-16-unit hard
    # limit. Runway's Gen-4.5 guidance recommends simple, direct, positive
    # visual action instead of filling the entire boundary with constraints.
    prompt_limit = 900

    narration = _truncate_utf16(
        _sanitize_provider_visual_text(scene.get('narration')),
        180,
    )
    visible_action = _truncate_utf16('; '.join(hint[:100] for hint in hints), 150)
    primary_event = _truncate_utf16(
        ' / '.join(
            value for value in (visible_action, narration) if value
        ),
        280,
    )
    combined = f'{narration} {original} {visible_action}'.lower()
    mechanism_guardrails: list[str] = []
    connection_claim = bool(re.search(
        r'\b(?:insert(?:s|ed|ing)?|plug(?:s|ged|ging)?|attach(?:es|ed|ing)?|'
        r'fasten(?:s|ed|ing)?|buckle(?:s|d|ing)?|latch(?:es|ed|ing)?|'
        r'connect(?:s|ed|ing)?)\b|'
        r'\b(?:toka(?:ya|yı|yi|sı|si)?|soket(?:e|i)?|fiş(?:e|i)?|'
        r'yuva(?:ya|yı)?|kemer(?:i|ini)?)\b.{0,48}\b'
        r'(?:tak(?:ıyor|iyor|mak|ar|tı|ti|ıl|il)|sok(?:uyor|mak|ar|tu|ul)|'
        r'bağla(?:r|mak|dı|nıyor)?|yerleştir(?:iyor|mek|ir|di)?|'
        r'kilitle(?:r|mek|di|niyor)?)\b',
        combined,
    ))
    if connection_claim:
        mechanism_guardrails.append(
            'Connection proof: clearly show the distinct moving connector and '
            'receiving socket apart, visibly join them, release the hand, and '
            'hold the fully seated connection in clear view.'
        )
    oled_claim = bool(re.search(
        r'\b(?:oled|true[ -]black)\b|gerçek siyah|emissive\s+(?:pixel|display|screen)',
        combined,
    ))
    if oled_claim:
        mechanism_guardrails.append(
            'OLED proof: macro real subpixels; shaped-black emitters remain dark '
            'while adjacent RGB stays lit in the same held close-up.'
        )
        power_claim = bool(re.search(
            r'\b(?:power\s+(?:use|usage|draw|consumption)|energy\s+(?:use|usage|consumption)|'
            r'watt(?:age)?|uses?\s+less\s+(?:power|energy)|lower\s+power)\b|'
            r'(?:güç|enerji).{0,24}tüket|daha\s+az\s+(?:güç|enerji)|'
            r'(?:güç|enerji)\s+kullanım',
            combined,
        ))
        if power_claim:
            mechanism_guardrails.append(
                'Power proof: a real physical meter visibly falls in the same shot.'
            )

    opening = (
        'Raw-camera photorealistic 9:16 vertical documentary shot, '
        'edge-to-edge. No UI, @handle, caption, text, logo, watermark, '
        'border, letterbox or collage. Keep the primary subject and decisive '
        'action inside the central safe area. '
        if aspect_ratio == '9:16'
        else (
            'Raw-camera photorealistic 16:9 documentary shot. No UI, @handle, '
            'caption, text, logo, watermark, border, letterbox or collage. '
        )
    )
    temporal_clause = (
        f'PRIMARY EVENT: {_truncate_utf16(primary_event, 140)}. '
        'Begin with a clear START state, show the named PHYSICAL ACTION or '
        'CAUSE, then hold the visibly CHANGED RESULT in the same take. '
    ) if primary_event else ''
    review_targets = _truncate_utf16(
        '; '.join(hints),
        90,
    ) if has_repair_evidence and not mechanism_guardrails else ''
    repair_evidence_clause = (
        f'REVIEW-LED VISIBLE TARGETS: {review_targets}. Match their literal '
        'identity, real-world scale, material, condition and setting.'
        if review_targets
        else ''
    )
    raw_guardrail_clause = ' '.join(
        clause
        for clause in (*mechanism_guardrails, repair_evidence_clause)
        if clause
    )
    closing = 'Keep one clean, stable, unbranded documentary frame.'
    original_label = 'Core shot direction: '
    identity_guardrail = manufactured_replica_guardrail(scene)
    identity_clause = (
        f'{identity_guardrail} ' if identity_guardrail else ''
    )
    if identity_evidence_clause:
        # A failed identity gate needs a smaller protected layout than the
        # general-purpose prompt. Reserve a useful literal scene core before
        # admitting any optional mechanism text; never trim the identity or
        # artifact contracts from either end of the provider limit.
        repair_opening = (
            'Raw-camera 9:16 documentary shot. No UI, @handle, caption, text, '
            'logo, watermark, border, letterbox or collage. '
            if aspect_ratio == '9:16'
            else (
                'Raw-camera 16:9 documentary shot. No UI, @handle, caption, '
                'text, logo, watermark, border, letterbox or collage. '
            )
        )
        repair_core_source = ' / '.join(
            value for value in (visible_action, original, narration) if value
        )
        repair_prefix = (
            repair_opening
            + identity_evidence_clause
            + original_label
        )
        repair_suffix = (
            '. '
            + identity_clause
            + closing
        )
        minimum_core_units = 80
        required_units = len(
            (repair_prefix + repair_suffix).encode('utf-16-le')
        ) // 2
        if required_units + minimum_core_units > prompt_limit:
            raise ValueError(
                'Protected identity repair prompt exceeds provider limit'
            )

        optional_guardrail = ''
        if raw_guardrail_clause:
            candidate_guardrail = f'{raw_guardrail_clause} '
            candidate_units = len(
                candidate_guardrail.encode('utf-16-le')
            ) // 2
            if (
                required_units
                + candidate_units
                + minimum_core_units
                <= prompt_limit
            ):
                optional_guardrail = candidate_guardrail
                repair_suffix = (
                    '. '
                    + optional_guardrail
                    + identity_clause
                    + closing
                )
                required_units += candidate_units

        repair_core = _truncate_utf16(
            repair_core_source,
            prompt_limit - required_units,
        )
        if not repair_core:
            raise ValueError('Identity repair prompt requires scene content')
        repair_prompt = repair_prefix + repair_core + repair_suffix
        if len(repair_prompt.encode('utf-16-le')) // 2 > prompt_limit:
            raise ValueError(
                'Protected identity repair prompt exceeds provider limit'
            )
        return repair_prompt

    fixed_units = len(
        (
            opening
            + identity_evidence_clause
            + temporal_clause
            + original_label
            + identity_clause
            + closing
        ).encode(
            'utf-16-le'
        )
    ) // 2
    shared_budget = max(
        0,
        prompt_limit - fixed_units - 3,
    )
    original_units = len(original.encode('utf-16-le')) // 2
    minimum_original = min(original_units, 360)
    guardrail_units = len(raw_guardrail_clause.encode('utf-16-le')) // 2
    guardrail_budget = min(
        guardrail_units,
        220,
        max(0, shared_budget - minimum_original),
    )
    original_budget = max(0, shared_budget - guardrail_budget)
    original_value = (
        _truncate_utf16(original, original_budget)
        if original and original_budget else ''
    )
    original_clause = f'{original_label}{original_value}. ' if original_value else ''
    guardrail_value = _truncate_utf16(
        raw_guardrail_clause,
        guardrail_budget,
    )
    guardrail_clause = f'{guardrail_value} ' if guardrail_value else ''
    return _truncate_utf16(
        opening
        + identity_evidence_clause
        + temporal_clause
        + original_clause
        + identity_clause
        + guardrail_clause
        + closing,
        prompt_limit,
    )


def _image_motion_prompt_for_scene(
    scene: dict,
    review: dict | None = None,
    aspect_ratio: str = '16:9',
) -> str:
    """Build one literal, evidence-led documentary keyframe prompt."""
    aspect_ratio = str(aspect_ratio or '').strip()
    if aspect_ratio not in {'16:9', '9:16'}:
        raise ValueError('Generation aspect ratio must be 16:9 or 9:16')
    review = review if isinstance(review, dict) else {}
    identity_evidence_clause = _identity_proof_clause(review)
    identity_guardrail = manufactured_replica_guardrail(scene)
    retry_queries = review.get('retry_queries') or []
    if isinstance(retry_queries, str):
        retry_queries = [retry_queries]
    hints = [
        _sanitize_provider_visual_text(value)
        for value in retry_queries
        if _sanitize_provider_visual_text(value)
    ][:2]
    if not hints:
        visual_queries = scene.get('visual_queries') or []
        if isinstance(visual_queries, str):
            visual_queries = [visual_queries]
        hints = [
            _sanitize_provider_visual_text(value)
            for value in visual_queries
            if _sanitize_provider_visual_text(value)
        ][:2]

    opening = (
        'Raw-camera photorealistic 9:16 vertical documentary keyframe, '
        'edge-to-edge. No social UI, @handle, text, logo, watermark, border, '
        'letterbox, storyboard, collage or CGI. Keep the primary subject and '
        'decisive action fully visible inside the central safe area. Depict '
        'the most evidence-rich instant: '
        if aspect_ratio == '9:16'
        else (
            'Raw-camera photorealistic 16:9 documentary keyframe. No social '
            'UI, @handle, text, logo, watermark, border, letterbox, storyboard, '
            'collage or CGI. Depict the most evidence-rich instant: '
        )
    )
    closing = (
        ' Keep literal subject scale, material, condition and setting clear '
        'in natural light and depth, composed for a subtle centered push-in. '
        'No storyboard, collage, CGI, border or letterbox.'
    )
    narration = (
        ''
        if identity_guardrail
        else _truncate_utf16(
            _sanitize_provider_visual_text(scene.get('narration')),
            180,
        )
    )
    evidence = _truncate_utf16(
        '; '.join(_truncate_utf16(hint, 100) for hint in hints),
        100 if identity_guardrail else 180,
    )
    context_prefix = f'NARRATION: {narration}. ' if narration else ''
    if evidence:
        context_prefix += f'REVIEW-LED VISIBLE ATTRIBUTES: {evidence}. '
    context_prefix += 'CORE VISUAL: '
    identity_clause = (
        f' {identity_guardrail}' if identity_guardrail else ''
    )
    if identity_evidence_clause:
        repair_opening = (
            'Raw 9:16 documentary keyframe. No UI, @handle, text, logo, '
            'watermark, border, letterbox, storyboard, collage or CGI. '
            if aspect_ratio == '9:16'
            else (
                'Raw 16:9 documentary keyframe. No UI, @handle, text, logo, '
                'watermark, border, letterbox, storyboard, collage or CGI. '
            )
        )
        repair_core_source = ' / '.join(
            value
            for value in (
                evidence,
                _sanitize_provider_visual_text(scene.get('ai_prompt')),
                narration,
            )
            if value
        )
        repair_prefix = (
            repair_opening
            + identity_evidence_clause
            + 'CORE VISUAL: '
        )
        repair_suffix = (
            f'. {identity_guardrail}{closing}'
            if identity_guardrail
            else f'.{closing}'
        )
        minimum_core_units = 80
        required_units = len(
            (repair_prefix + repair_suffix).encode('utf-16-le')
        ) // 2
        if required_units + minimum_core_units > 1000:
            raise ValueError(
                'Protected identity keyframe prompt exceeds provider limit'
            )
        repair_core = _truncate_utf16(
            repair_core_source,
            1000 - required_units,
        )
        if not repair_core:
            raise ValueError('Identity repair keyframe requires scene content')
        repair_prompt = repair_prefix + repair_core + repair_suffix
        if len(repair_prompt.encode('utf-16-le')) // 2 > 1000:
            raise ValueError(
                'Protected identity keyframe prompt exceeds provider limit'
            )
        return repair_prompt

    fixed_units = len(
        (
            opening
            + identity_evidence_clause
            + context_prefix
            + identity_clause
            + closing
        ).encode('utf-16-le')
    ) // 2
    # Keep the artifact ban intact even when a provider or fixture normalizes
    # Unicode differently at its documented 1000-unit boundary.
    visual_budget = max(0, 1000 - fixed_units - 96)
    core_visual = _truncate_utf16(
        _sanitize_provider_visual_text(scene.get('ai_prompt')),
        visual_budget,
    )
    return _truncate_utf16(
        opening
        + identity_evidence_clause
        + context_prefix
        + core_visual
        + identity_clause
        + closing,
        1000,
    )


def _apply_visual_review(
    scene_visuals: list[list[str | dict]],
    scene_idx: int,
    review: dict,
    default_fraction: float = 0.25,
) -> None:
    if scene_idx < 0 or scene_idx >= len(scene_visuals):
        return
    specs = [spec for spec in scene_visuals[scene_idx] if _visual_path(spec)]
    if not specs:
        return
    try:
        best_idx = int(review.get('best_candidate_index', 0))
    except Exception:
        best_idx = 0
    best_idx = min(max(best_idx, 0), len(specs) - 1)
    chosen = dict(specs[best_idx]) if isinstance(specs[best_idx], dict) else {'path': _visual_path(specs[best_idx])}
    if chosen.get('preserve_start_fraction'):
        try:
            locked_fraction = float(chosen.get('start_fraction', 0.0))
        except Exception:
            locked_fraction = 0.0
        chosen['start_fraction'] = max(0.0, min(locked_fraction, 0.95))
        scene_visuals[scene_idx] = [chosen]
        return
    try:
        fraction = float(review.get('best_start_fraction', chosen.get('start_fraction', default_fraction)))
    except Exception:
        fraction = default_fraction
    chosen['start_fraction'] = max(0.0, min(fraction, 0.95))
    scene_visuals[scene_idx] = [chosen]


def _generated_visual_spec(
    path: str | Path,
    provider: str = 'unknown',
    provider_attempts: int = 1,
) -> dict:
    return {
        'path': str(path),
        'start_fraction': 0.0,
        'preserve_start_fraction': True,
        'forbid_loop': True,
        'generated': True,
        'source_type': 'generated',
        'generation_provider': str(provider),
        'generation_provider_attempts': max(1, int(provider_attempts)),
    }


def _render_target_duration(options: dict, requested_seconds: float) -> float | None:
    return (
        float(requested_seconds)
        if (
            options.get('mode') == 'preview'
            or (
                options.get('mode') == 'production'
                and options.get('format') == 'shorts'
            )
        )
        else None
    )


def _preview_duration_within_gate(
    actual_seconds: float,
    requested_seconds: float,
    voice_seconds: float,
) -> bool:
    try:
        actual = float(actual_seconds)
        requested = float(requested_seconds)
        voice = float(voice_seconds)
    except Exception:
        return False
    if actual <= 0 or requested <= 0 or voice <= 0:
        return False

    # MP3/AAC encoder padding and mux timebases can shift a short master by
    # several hundred milliseconds. Cap target drift at one second, but allow
    # at most 250 ms against the fitted voice so a final word cannot be hidden
    # by the broader target tolerance.
    target_tolerance = min(1.0, max(0.75, requested * 0.02))
    target_ok = abs(actual - requested) <= target_tolerance
    voice_complete = actual + 0.25 >= voice
    return target_ok and voice_complete


def _runway_generation_seconds(scene_duration: float) -> int:
    """Buy enough source footage for one pass after the renderer's speed-up."""
    try:
        required = max(0.0, float(scene_duration)) * 1.02 + 0.20
    except Exception:
        required = 5.0
    return max(5, min(10, int(math.ceil(required))))


def _runway_failure_diagnostic(
    stage: str,
    scene_index: int,
    exc: BaseException,
) -> dict:
    """Return the deliberately tiny, secret-safe Runway failure surface."""
    safe_stage = (
        stage
        if stage in {'initial_generation', 'final_repair'}
        else 'unknown'
    )
    try:
        safe_scene_index = int(scene_index)
    except Exception:
        safe_scene_index = -1
    exception_class = str(
        getattr(type(exc), '__name__', '') or 'Exception'
    )
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', exception_class):
        exception_class = 'Exception'
    diagnostic = {
        'stage': safe_stage,
        'scene_index': safe_scene_index,
        'exception_class': exception_class,
    }
    reason_code = str(getattr(exc, 'reason_code', '') or '').strip()
    if reason_code in {
        'bad_request',
        'invalid_argument',
        'invalid_audio',
        'invalid_duration',
        'invalid_model',
        'invalid_prompt_image',
        'invalid_prompt_text',
        'invalid_ratio',
        'invalid_request',
        'model_not_supported',
        'parameter_unknown',
        'prompt_empty',
        'prompt_safety',
        'prompt_too_long',
        'unsupported_model',
        'validation_error',
        'validation_of_body_failed',
    }:
        diagnostic['reason_code'] = reason_code
    return diagnostic


def _runway_failure_payload(
    attempts: int,
    failed_scene_indices: list[int],
    diagnostics: list[dict],
) -> dict:
    """Build the public failure payload from allow-listed diagnostic fields."""
    failed_scenes: list[int] = []
    for raw_index in failed_scene_indices:
        try:
            scene_index = int(raw_index)
        except Exception:
            continue
        if scene_index not in failed_scenes:
            failed_scenes.append(scene_index)
    failed_scenes.sort()
    failed_scene_set = set(failed_scenes)

    safe_diagnostics: list[dict] = []
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, dict):
            continue
        try:
            scene_index = int(diagnostic.get('scene_index'))
        except Exception:
            continue
        if scene_index not in failed_scene_set:
            continue
        stage = str(diagnostic.get('stage') or '')
        if stage not in {'initial_generation', 'final_repair'}:
            continue
        exception_class = str(
            diagnostic.get('exception_class') or 'Exception'
        )
        if not re.fullmatch(
            r'[A-Za-z_][A-Za-z0-9_]{0,127}',
            exception_class,
        ):
            exception_class = 'Exception'
        safe_diagnostic = {
            'stage': stage,
            'scene_index': scene_index,
            'exception_class': exception_class,
        }
        reason_code = str(diagnostic.get('reason_code') or '').strip()
        if reason_code in {
            'bad_request',
            'invalid_argument',
            'invalid_audio',
            'invalid_duration',
            'invalid_model',
            'invalid_prompt_image',
            'invalid_prompt_text',
            'invalid_ratio',
            'invalid_request',
            'model_not_supported',
            'parameter_unknown',
            'prompt_empty',
            'prompt_safety',
            'prompt_too_long',
            'unsupported_model',
            'validation_error',
            'validation_of_body_failed',
        }:
            safe_diagnostic['reason_code'] = reason_code
        safe_diagnostics.append(safe_diagnostic)

    try:
        safe_attempts = max(0, int(attempts))
    except Exception:
        safe_attempts = 0
    return {
        'attempts': safe_attempts,
        'failed_scenes': failed_scenes,
        'failures': safe_diagnostics,
    }


def _final_visual_rejection_diagnostics(
    *,
    scene_count: int,
    rejected_scene_indices: list[int],
    rescued_scene_indices: list[int],
    final_reviews: dict[int, dict],
    runway_attempts: int,
    initial_generation_failed_scene_indices: list[int],
    final_repair_failed_scene_indices: list[int],
    runway_failure_diagnostics: list[dict],
    repair_checkpoint_available: bool,
) -> dict | None:
    """Describe only quality failures that remain after the free rescue.

    Provider failures are useful diagnostics, but they are not themselves a
    terminal outcome once an exact replacement clip passes the final critic.
    Keeping that distinction here prevents a failed image-motion/repair chain
    from overriding a later successful stock rescue.
    """
    try:
        safe_scene_count = max(0, int(scene_count))
    except Exception:
        safe_scene_count = 0
    rejected = sorted({
        int(index)
        for index in rejected_scene_indices
        if str(index).lstrip('-').isdigit()
        and 0 <= int(index) < safe_scene_count
    })
    if not rejected:
        return None

    rejected_details = {
        index: {
            'score': int((final_reviews.get(index) or {}).get('score', 0)),
            'reason': str(
                (final_reviews.get(index) or {}).get('reason')
                or 'missing review'
            )[:180],
        }
        for index in rejected
    }
    provider_failed_rejections = sorted(
        set(rejected)
        & (
            {int(index) for index in initial_generation_failed_scene_indices}
            | {int(index) for index in final_repair_failed_scene_indices}
        )
    )
    diagnostics = {
        'stage': 'after_rescue',
        'accepted': safe_scene_count - len(rejected),
        'total': safe_scene_count,
        'replaced': len({int(index) for index in rescued_scene_indices}),
        'rejected': rejected_details,
        'repair_checkpoint_available': bool(repair_checkpoint_available),
    }
    if provider_failed_rejections:
        diagnostics['provider_generation_failures'] = (
            _runway_failure_payload(
                runway_attempts,
                provider_failed_rejections,
                runway_failure_diagnostics,
            )
        )
    return diagnostics


def _runway_single_pass_supported(scene_duration: float) -> bool:
    try:
        required = max(0.0, float(scene_duration)) * 1.02 + 0.20
    except Exception:
        return False
    return required <= 10.0


def _validate_runway_single_pass_candidates(
    scene_indices: list[int],
    scene_durations: list[float],
) -> None:
    unsupported = [
        int(scene_idx)
        for scene_idx in scene_indices
        if (
            scene_idx < 0
            or scene_idx >= len(scene_durations)
            or not _runway_single_pass_supported(
                scene_durations[scene_idx]
            )
        )
    ]
    if unsupported:
        raise FinalVisualQualityError(
            'AI scenes exceed Runway single-pass duration; split these '
            'storyboard scenes before paid generation: '
            + ','.join(str(index) for index in unsupported)
        )


def _preflight_runway_candidates_before_paid(
    scene_indices: list[int],
    scene_durations: list[float],
    approved_package: dict | None,
) -> None:
    """Fail before spend, while allowing automatic plans to be regenerated."""
    try:
        _validate_runway_single_pass_candidates(
            scene_indices,
            scene_durations,
        )
    except FinalVisualQualityError as exc:
        if approved_package is None:
            raise PreRunwayRetryableError(str(exc)) from exc
        raise


def _max_runway_scenes(options: dict, scene_count: int, duration_minutes: float) -> int:
    mix = options.get('visual_mix') or 'balanced'
    if options.get('mode') == 'preview':
        if duration_minutes > 0.6:
            return 0
        preview_limit = preview_paid_ai_limit(
            options,
            scene_count,
            duration_minutes,
        )
        return int(preview_limit or 0)
    if mix == 'real_first':
        return min(2, max(1, math.ceil(scene_count * 0.10)))
    if mix == 'ai_first':
        return min(6, max(2, math.ceil(scene_count * 0.40)))
    return min(4, max(1, math.ceil(scene_count * 0.24)))


def _short_preview_required_submission_cap(
    base_submission_cap: int,
    required_scene_count: int,
) -> int:
    """Bound a local completion allowance without widening normal routing."""
    base_cap = max(0, int(base_submission_cap))
    required_count = max(0, int(required_scene_count))
    return max(
        base_cap,
        min(required_count, SHORT_PREVIEW_REQUIRED_RUNWAY_CAP),
    )


def _allocate_short_preview_forced_stock_runway(
    ranked_candidates: list[dict],
    base_submission_cap: int,
    provider_outage_stock_scenes: set[int],
    stock_quality_fallback_scenes: set[int],
    quality_threshold: int,
) -> tuple[
    list[dict],
    list[dict],
    list[int],
    list[int],
    bool,
    bool,
    list[int],
]:
    """Reserve bounded emergency slots for forced short-preview STOCK scenes.

    Clips without an approved visual are mandatory and already approved
    AI-first upgrades are optional. A real-first preview may locally expand its
    ordinary base from one to at most two required scenes. Every later selected
    candidate is an explicit STOCK scene forced either by a typed provider
    outage or by at most three semantic failures after the full unchanged stock
    tournament.
    """
    base_cap = max(0, int(base_submission_cap))
    outage_indices = {int(index) for index in provider_outage_stock_scenes}
    quality_indices = {int(index) for index in stock_quality_fallback_scenes}
    overlapping_forced_scenes = sorted(outage_indices & quality_indices)
    outage_candidates: list[dict] = []
    quality_candidates: list[dict] = []
    required_base_candidates: list[dict] = []
    optional_base_candidates: list[dict] = []
    ranked_indices: set[int] = set()

    for candidate in ranked_candidates:
        scene_idx = int(candidate.get('scene_index', -1))
        ranked_indices.add(scene_idx)
        if scene_idx in outage_indices:
            outage_candidates.append(candidate)
            continue
        if scene_idx in quality_indices:
            quality_candidates.append(candidate)
            continue
        has_visual = bool(candidate.get('has_visual'))
        stock_score = int(candidate.get('stock_score', -1))
        if not has_visual or stock_score < int(quality_threshold):
            required_base_candidates.append(candidate)
        else:
            optional_base_candidates.append(candidate)

    required_submission_cap = _short_preview_required_submission_cap(
        base_cap,
        len(required_base_candidates),
    )
    selected_base = [
        *required_base_candidates,
        *optional_base_candidates,
    ][:required_submission_cap]
    outage_cap_exceeded = (
        len(outage_indices) > SHORT_PREVIEW_PROVIDER_OUTAGE_RUNWAY_CAP
    )
    quality_cap_exceeded = (
        len(quality_indices) > SHORT_PREVIEW_STOCK_QUALITY_RUNWAY_CAP
    )
    selected = [
        *selected_base,
        *outage_candidates[:SHORT_PREVIEW_PROVIDER_OUTAGE_RUNWAY_CAP],
        *quality_candidates[:SHORT_PREVIEW_STOCK_QUALITY_RUNWAY_CAP],
    ]
    missing_outage_scenes = sorted(outage_indices - ranked_indices)
    missing_quality_scenes = sorted(quality_indices - ranked_indices)
    return (
        selected,
        required_base_candidates,
        missing_outage_scenes,
        missing_quality_scenes,
        outage_cap_exceeded,
        quality_cap_exceeded,
        overlapping_forced_scenes,
    )


def _review_stock_tournament_round(
    scenes: list[dict],
    active_scenes: list[int],
    round_visuals: list[list[str | dict]],
    work: Path,
    round_index: int,
    topic: str,
    *,
    content_style: str = '',
    evidence_sources: list[dict] | None = None,
) -> dict[int, dict]:
    """Review independent stock scenes concurrently in bounded requests."""
    if len(active_scenes) != len(round_visuals):
        raise PreRunwayRetryableError(
            'Stock-tournament scene and visual batches are inconsistent'
        )

    def review_position(
        position: int,
        scene_idx: int,
        candidate_batch: list[str | dict],
    ) -> dict:
        scene_qc = review_scene_visuals(
            [scenes[scene_idx]],
            [candidate_batch],
            work / f'scene_{scene_idx:02d}',
            1,
            _missing_review_attempts=0,
            topic=topic,
            story_scenes=scenes,
            gemini_model_override=STOCK_TOURNAMENT_GEMINI_MODEL,
            content_style=content_style,
            evidence_sources=evidence_sources,
        )
        local_reviews = {
            int(review.get('scene_index')): review
            for review in (scene_qc.get('reviews') or [])
            if isinstance(review, dict)
            and str(review.get('scene_index', '')).lstrip('-').isdigit()
        }
        if 0 not in local_reviews:
            raise PreRunwayRetryableError(
                'Stock-tournament QC was incomplete before any paid submission: '
                + json.dumps(
                    {
                        'round': round_index,
                        'scene_index': scene_idx,
                        'missing_positions': [position],
                    },
                    separators=(',', ':'),
                )
            )
        return dict(local_reviews[0])

    round_reviews: dict[int, dict] = {}
    worker_count = min(
        STOCK_TOURNAMENT_REVIEW_MAX_WORKERS,
        max(1, len(active_scenes)),
    )
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_by_position = {
            position: executor.submit(
                review_position,
                position,
                scene_idx,
                candidate_batch,
            )
            for position, (scene_idx, candidate_batch) in enumerate(
                zip(active_scenes, round_visuals)
            )
        }
        # Resolve in input order so completion timing cannot change scene
        # mapping or which fail-closed error is surfaced first.
        for position in range(len(active_scenes)):
            round_reviews[position] = future_by_position[position].result()
    return round_reviews


def _fresh_scheduled_short_shots(
    task_id: str,
    spec: dict,
    *,
    approved_package,
    retry_dispatch_source_id,
    curated_stock_manifest,
    voice_replacement_source_id,
    paid_slots_used: int,
    full_rebuild_verified: bool = False,
) -> bool:
    """Keep fresh editorial checks before media or on a private full rebuild."""
    full_rebuild = full_rebuild_verified is True
    if (
        spec.get('mode') != 'production' or spec.get('format') != 'shorts'
        or spec.get('duration_minutes') != 0.5
        or spec.get('workflow') not in ({'auto', 'scene_repair'} if full_rebuild else {'auto'})
        or spec.get('production_scheduled') is not True
        or spec.get('publish_after_render') is not True
        or approved_package is not None
        or (retry_dispatch_source_id is not None and not full_rebuild and (
            not isinstance(retry_dispatch_source_id, str)
            or not _RECOVERED_MEDIA_SOURCE_PATTERN.fullmatch(retry_dispatch_source_id)
            or retry_dispatch_source_id == task_id
        ))
        or (full_rebuild and (
            not isinstance(retry_dispatch_source_id, str)
            or not _RECOVERED_MEDIA_SOURCE_PATTERN.fullmatch(retry_dispatch_source_id)
            or retry_dispatch_source_id == task_id
        ))
        or curated_stock_manifest is not None or voice_replacement_source_id is not None
        or type(paid_slots_used) is not int or paid_slots_used != 0
    ):
        return False
    if retry_dispatch_source_id is not None and not full_rebuild:
        if spec.get('content_plan_item_id'):
            from app.services.content_plan_research_resume import registered, verify_child
            if registered(retry_dispatch_source_id):
                verify_child(task_id, retry_dispatch_source_id, spec)
                return True
        from app.services.pre_media_editorial_retry import eligible
        return eligible(task_id, retry_dispatch_source_id, spec)
    try:
        from app.services.studio_state import get_job
        from app.services.fresh_story_binding import fresh_scheduled_spec_matches

        job = get_job(task_id)
        if (
            not isinstance(task_id, str) or not _RECOVERED_MEDIA_SOURCE_PATTERN.fullmatch(task_id)
            or not isinstance(job, dict) or job.get('task_id') != task_id
            or job.get('kind') != 'render' or not fresh_scheduled_spec_matches(job.get('spec'), spec)
        ):
            raise ValueError('Scheduler job binding is unavailable')
        return (
            job.get('parent_id') == (retry_dispatch_source_id if full_rebuild else None)
            and job.get('result') is None
            and job.get('state') in {'PENDING', 'STARTED', 'PROGRESS', 'RETRY'}
            and all(job.get(field) is None for field in (
                'audio_candidate_checkpoint', 'voice_candidate_reuse', 'voice_replacement',
                'repair_checkpoint', 'qa_workprint',
            ))
        )
    except Exception:
        raise FinalVisualQualityError('Fresh scheduled storyboard binding could not be verified') from None


def _prepare_full_video_rebuild(
    task_id: str, source_task_id: str | None, runtime_spec: dict, *,
    retry_dispatch_source_id: str | None, approved_package,
    curated_stock_manifest, voice_replacement_source_id,
) -> dict | None:
    """Verify a private full-rebuild grant; never infer one from model options."""
    if source_task_id is None:
        return None
    try:
        if (
            not isinstance(source_task_id, str)
            or not _RECOVERED_MEDIA_SOURCE_PATTERN.fullmatch(source_task_id)
            or not isinstance(task_id, str)
            or not _RECOVERED_MEDIA_SOURCE_PATTERN.fullmatch(task_id)
            or task_id == source_task_id
            or source_task_id != retry_dispatch_source_id
            or approved_package is not None or curated_stock_manifest is not None
            or voice_replacement_source_id is not None
        ):
            raise ValueError('Full rebuild requires its exact standalone retry')
        from app.services.full_video_rebuild import get_full_rebuild_policy

        policy = get_full_rebuild_policy(task_id, source_task_id, runtime_spec)
        if (
            not isinstance(policy, dict)
            or type(policy.get('version')) is not int or policy['version'] != 1
            or policy.get('mode') != 'full'
            or policy.get('source_task_id') != source_task_id
            or policy.get('child_task_id') != task_id
            or not isinstance(policy.get('spec_sha256'), str)
            or not _SHA256_PATTERN.fullmatch(policy['spec_sha256'])
            or type(policy.get('original_paid_create_cap')) is not int
            or not 2 <= policy['original_paid_create_cap'] <= 6
            or any(policy.get(field) is not True for field in (
                'fresh_story', 'fresh_voice', 'fresh_media', 'requires_full_qa',
            ))
        ):
            raise ValueError('Full rebuild policy is incomplete')
        return policy
    except Exception:
        raise FinalVisualQualityError('Full rebuild authorization could not be verified') from None


def _prepare_scheduled_short_shots(
    task_id: str, package: dict, topic: str, duration_minutes: float,
    language: str, options: dict,
) -> dict:
    """One optional prompt-only compression, durably reserved before its call."""
    def reserve_compression() -> None:
        from app.services.studio_state import _client

        if not isinstance(task_id, str) or not _RECOVERED_MEDIA_SOURCE_PATTERN.fullmatch(task_id):
            raise ValueError('Canonical task identity is required')
        reservation = json.dumps({
            'task_id': task_id, 'package_sha256': _recovery_package_sha256(package),
            'attempts': 1,
        }, sort_keys=True, separators=(',', ':'))
        # No expiry or reopen: a missing reply may already represent a paid
        # compression. A later delivery must not repeat that uncertain call.
        if _client().set(
            f'youtube_studio:shot_prompt_compression:v1:{task_id}', reservation, nx=True,
        ) is not True:
            raise ValueError('Prompt compression was already reserved or is unavailable')

    try:
        from app.services.director import ensure_scheduled_short_shot_prompts

        return ensure_scheduled_short_shot_prompts(
            package, topic, duration_minutes, language, options,
            fresh_scheduled=True, before_compression=reserve_compression,
        )
    except (SpendBlocked, AbacusGenerationError):
        raise
    except Exception:
        raise FinalVisualQualityError(
            'Scheduled shooting directions could not be validated before voice or paid media'
        ) from None


def _prepare_package(
    celery_task,
    task_id: str,
    topic: str,
    duration_minutes: float,
    language: str,
    options: dict,
    approved_package: dict | None,
    *,
    fresh_scheduled: bool = False,
) -> dict:
    if approved_package:
        if (
            (
                (options.get('mode') == 'preview' and duration_minutes <= 0.6)
                or (
                    options.get('mode') == 'production'
                    and options.get('format') == 'shorts'
                    and duration_minutes == 0.5
                )
            )
            and not short_story_package_is_approved(
                approved_package,
                topic,
            )
        ):
            raise FinalVisualQualityError(
                'Onaylı kısa storyboard güncel hikâye ve telaffuz '
                'denetiminden geçmiyor; ücretli medya başlatılmadı.'
            )
        set_stage(celery_task, task_id, 'approved_plan', 12, 'Onaylı storyboard kilitlendi.')
        package = (deepcopy(approved_package) if isinstance(approved_package.get('_recovered_generated_media'), dict)
                   and approved_package['_recovered_generated_media'].get('version') == 6
                   else dict(approved_package))
        if not (
            package.get('_recovered_generated_media') is not None
            or package.get('_recovered_voice') is not None
        ):
            package['studio_options'] = options
        # Recovery contracts bind the exact server-approved package hash,
        # including its original studio_options. ``scene_repair`` is runtime
        # dispatch metadata, not a storyboard mutation; replacing the original
        # options here would invalidate an otherwise authentic checkpoint.
        return package

    set_stage(celery_task, task_id, 'research', 7, 'Güncel araştırma ve ilk storyboard hazırlanıyor.')
    fresh_kwargs = {'fresh_scheduled': True} if fresh_scheduled is True else {}
    draft = research_and_script(topic, duration_minutes, language, options, **fresh_kwargs)
    set_stage(celery_task, task_id, 'director_qc', 14, 'Senaryo yönetmeni akışı, ritmi ve görsel dili düzeltiyor.')
    try:
        return direct_and_qc(draft, topic, duration_minutes, language, options, **fresh_kwargs)
    except Exception as exc:
        if fresh_scheduled is True:
            from app.services.planning_diagnostics import planning_failure_diagnostics

            # This path runs before voice/media and never grants approval.
            # Preserve rejected wording so a repair can use actual evidence
            # instead of paying for the same opaque failure again.
            try:
                update_job(task_id, prepaid_story_diagnostics=planning_failure_diagnostics(exc, draft))
            except Exception:
                pass  # diagnostics must not mask the original quality error
        raise


def _guard_retry_child_execution(
    celery_task,
    task_id: str,
    retry_dispatch_source_id: str | None,
) -> None:
    """Owner cancellation fences every delivery, including Celery autoretries."""
    if render_cancellation_requested(task_id) or retained_delivery_blocked(task_id):
        raise Ignore()
    retry_number = int(getattr(celery_task.request, 'retries', 0) or 0)
    if (
        retry_dispatch_source_id
        and retry_number == 0
        and not acquire_retry_child_execution(
            task_id,
            retry_dispatch_source_id,
        )
    ):
        raise Ignore()


def _prepare_saved_voice_retry(
    task_id: str,
    source_task_id: str | None,
    runtime_spec: dict,
    work: Path,
) -> dict | None:
    """Revalidate one same-spec UI retry without purchasing another voice."""
    if source_task_id and runtime_spec.get('content_plan_item_id'):
        from app.services import content_plan_voice_resume
        if content_plan_voice_resume.registered(source_task_id):
            content_plan_voice_resume.verify_child(task_id, source_task_id, runtime_spec)
        from app.services import content_plan_model_resume
        if content_plan_model_resume.registered(source_task_id):
            content_plan_model_resume.verify_child(task_id, source_task_id, runtime_spec)
            return None
        from app.services.content_plan_research_resume import registered, verify_child
        if registered(source_task_id):
            verify_child(task_id, source_task_id, runtime_spec)
            return None  # This exact root never submitted a voice or model request.
    if (source_task_id and runtime_spec.get('mode') == 'production'
            and runtime_spec.get('format') == 'landscape'
            and runtime_spec.get('duration_minutes') == 3):
        from app.services.longform_voice_retry import prepare, LongformVoiceRetryError

        try:
            return prepare(task_id, source_task_id, runtime_spec, work)
        except LongformVoiceRetryError:
            raise FinalAudioQualityError(
                'Long-form saved narration could not be revalidated; no replacement voice was generated'
            ) from None
    if (
        not source_task_id
        or runtime_spec.get('mode') != 'production'
        or runtime_spec.get('format') != 'shorts'
        or runtime_spec.get('duration_minutes') != 0.5
    ):
        return None
    from app.services.studio_state import get_job
    from app.services.voice_candidate_recovery import (
        load_voice_retry_candidate,
        require_unchanged_voice_narration,
    )
    from app.services.director import revalidate_immutable_short_story
    from app.services.voice import normalize_turkish_tts

    source = get_job(source_task_id)
    child = get_job(task_id)
    if not isinstance(source, dict) or not isinstance(child, dict):
        raise FinalAudioQualityError('Saved-voice retry source could not be verified')
    checkpoint = source.get('audio_candidate_checkpoint')
    if checkpoint is None:
        return None
    if (
        not isinstance(checkpoint, dict)
        or source.get('state') != 'FAILURE'
        or source.get('kind') != 'render'
        or source.get('retry_child_task_id') != task_id
        or child.get('parent_id') != source_task_id
        or source.get('spec') != runtime_spec
        or child.get('spec') != runtime_spec
        or runtime_spec.get('mode') != 'production'
        or runtime_spec.get('format') != 'shorts'
        or runtime_spec.get('duration_minutes') != 0.5
    ):
        raise FinalAudioQualityError('Saved-voice retry binding is invalid')
    cap = preview_total_paid_create_cap(runtime_spec, 0.5)
    if cap is None or _persisted_paid_create_slots(source_task_id, cap) != 0:
        raise FinalAudioQualityError('Saved-voice-only retry requires zero paid media submissions')
    try:
        candidate = load_voice_retry_candidate(source_task_id, task_id, checkpoint, work)
        source_package = candidate['package']
        source_scenes = source_package['scenes']
        expected_spoken = [
            normalize_turkish_tts(
                scene['narration'], ensure_terminal=(index + 1 == len(source_scenes)),
            )
            for index, scene in enumerate(source_scenes)
        ]
        if candidate['voice_result'].get('spoken_texts') != expected_spoken:
            # Old hash-bound candidates used the exact prior punctuation
            # normalizer (3,69 -> 3, 69). Accept that whole original spelling
            # only, not semantic similarity or arbitrary edited speech.
            legacy_spoken = [
                normalize_turkish_tts(
                    scene['narration'], ensure_terminal=(index + 1 == len(source_scenes)),
                    legacy_numeric_spacing=True,
                )
                for index, scene in enumerate(source_scenes)
            ] if runtime_spec.get('language') == 'tr' else None
            if candidate['voice_result'].get('spoken_texts') != legacy_spoken:
                raise ValueError('Saved spoken contract differs from the narration')
        options = {
            key: value for key, value in runtime_spec.items()
            if key not in {'topic', 'duration_minutes', 'language', 'channel_id'}
        }
        review_kwargs = {}
        if 'spoken_word_budget' in source_package:
            from app.services.director import validate_spoken_word_budget

            if (runtime_spec.get('language') != 'en'
                    or runtime_spec.get('production_scheduled') is not True):
                raise ValueError('Saved spoken budget does not match the runtime contract')
            review_kwargs['verified_spoken_word_budget'] = validate_spoken_word_budget(
                source_package['spoken_word_budget'],
            )
        from app.services.saved_voice_review import review_options
        review_kwargs.update(review_options(task_id, source_package, candidate['voice_result']))
        reviewed = revalidate_immutable_short_story(
            source_package,
            runtime_spec['topic'],
            0.5,
            runtime_spec['language'],
            options,
            immutable_candidate_narrations=[
                scene['narration'] for scene in source_package['scenes']
            ],
            **review_kwargs,
        )
        require_unchanged_voice_narration(source_package, reviewed)
        if not short_story_package_is_approved(reviewed, runtime_spec['topic']):
            raise ValueError('Current independent story approval is required')
    except Exception:
        raise FinalAudioQualityError(
            'Saved narration could not pass immutable story/source revalidation; '
            'no replacement voice was generated'
        ) from None
    update_job(task_id, voice_candidate_reuse={
        'source_task_id': source_task_id,
        'new_tts_requests': 0,
        'requires_full_qa': True,
    })
    # A claimed child may reuse the edited candidate, but never repeatedly
    # shorten the same narration through successive failed-job retries.
    if source.get('audio_pause_repair'):
        candidate['voice_result']['_internal_pause_repair_attempted'] = True
        update_job(task_id, audio_pause_repair=source['audio_pause_repair'])
    if source.get('voice_replacement'):
        update_job(task_id, voice_replacement=source['voice_replacement'])
    return {'package': reviewed, 'voice_result': candidate['voice_result'],
            'source_audio_sha256': candidate.get('audio_sha256')}


def _prepare_voice_replacement_request(task_id: str, source_task_id: str,
                                       spec: dict, saved_voice_retry: dict | None) -> dict:
    """Validate the private authorization after exact saved-story/audio checks."""
    from app.services.voice_replacement import get_voice_replacement_policy

    try:
        if not isinstance(saved_voice_retry, dict):
            raise ValueError('Saved source candidate is required')
        audio_sha256 = saved_voice_retry.get('source_audio_sha256')
        policy = get_voice_replacement_policy(task_id, source_task_id, audio_sha256, spec)
        if (policy.get('model_id') != 'eleven_multilingual_v2'
                or type(policy.get('max_attempts')) is not int or policy['max_attempts'] != 1):
            raise ValueError('Unexpected voice replacement policy')
        update_job(task_id, voice_candidate_reuse=None, voice_replacement=policy)
        return {'source_task_id': source_task_id, 'audio_sha256': audio_sha256, 'spec': dict(spec)}
    except Exception:
        raise FinalAudioQualityError('Voice replacement authorization could not be verified') from None


def _require_voice_replacement_checkpoint(task_id: str, voice_result: dict) -> None:
    """A paid replacement must be durably recoverable before any later work."""
    from app.services.studio_state import get_job

    try:
        job = get_job(task_id)
        checkpoint = job.get('audio_candidate_checkpoint') if isinstance(job, dict) else None
        if (not isinstance(checkpoint, dict) or job.get('audio_candidate_checkpoint_error')
                or checkpoint.get('status') != 'unapproved_candidate'
                or checkpoint.get('qa_approved') is not False or checkpoint.get('requires_full_qa') is not True
                or checkpoint.get('audio_sha256') != _audio_qa_fingerprint(voice_result['path'])):
            raise ValueError('Replacement candidate was not persisted')
    except Exception:
        raise FinalAudioQualityError('Voice replacement candidate checkpoint could not be verified') from None


def _preserved_longform_voice_for_retry(candidate: dict) -> dict:
    from app.services.longform_voice_retry import preserved_voice, LongformVoiceRetryError

    try:
        return preserved_voice(candidate)
    except LongformVoiceRetryError:
        raise FinalAudioQualityError(
            'Long-form saved audio or cues changed; no replacement voice was generated'
        ) from None


def _fit_saved_voice_for_retry(voice_result: dict, target_seconds: float) -> dict:
    from app.services.voice import fit_existing_narration_candidate

    try:
        return fit_existing_narration_candidate(voice_result, target_seconds)
    except Exception:
        raise FinalAudioQualityError(
            'Saved narration cannot be fitted within natural tempo limits; '
            'no replacement voice was generated'
        ) from None


def _audio_qa_fingerprint(audio_path: str | Path) -> str:
    """Bind the forthcoming timing review to a bounded, actual audio file."""
    checksum = hashlib.sha256()
    total = 0
    with Path(audio_path).open('rb') as stream:
        while chunk := stream.read(64 * 1024):
            total += len(chunk)
            if total > 14 * 1024 * 1024:
                raise FinalAudioQualityError('Short narration exceeds its review size limit')
            checksum.update(chunk)
    if total < 1024:
        raise FinalAudioQualityError('Short narration is incomplete')
    return checksum.hexdigest()


def _repair_voice_internal_pauses(voice_result: dict, target_seconds: float,
                                 audio_qc: dict, prosody_qc: dict) -> dict | None:
    """Optional local edit, never an approval or a provider retry."""
    from app.services.audio_pause_repair import repair_internal_pauses

    try:
        return repair_internal_pauses(
            voice_result, target_seconds,
            transcript_evidence=audio_qc, prosody_review=prosody_qc,
        )
    except Exception:
        # An unavailable edit keeps the failed review and the existing bounded
        # generation policy. No raw file/provider diagnostics reach the UI.
        return None


@celery.task(
    bind=True,
    autoretry_for=(Exception,),
    dont_autoretry_for=(
        ProductionContentError,
        ImmutableNarrationSceneBudgetError,
        UnsupportedLanguageError,
        SpendBlocked,
        AbacusGenerationError,
    ),
    retry_backoff=True,
    max_retries=1,
)
@spending_task
def plan_video_pipeline(
    self,
    topic: str,
    duration_minutes: float = 5,
    language: str = 'tr',
    channel_id: str | None = None,
    options: dict | None = None,
    retry_dispatch_source_id: str | None = None,
):
    language = normalize_pipeline_language(language)
    task_id = self.request.id
    _guard_retry_child_execution(
        self,
        task_id,
        retry_dispatch_source_id,
    )
    options = _normalized_options(options, duration_minutes)
    update_job(task_id, kind='plan', spec=_task_spec(topic, duration_minutes, language, channel_id, options))
    try:
        set_stage(self, task_id, 'research', 20, 'Araştırma ve ilk storyboard hazırlanıyor.')
        draft = research_and_script(topic, duration_minutes, language, options)
        set_stage(self, task_id, 'director_qc', 62, 'Senaryo yönetmeni bütünlüğü ve görsel planı denetliyor.')
        package = direct_and_qc(draft, topic, duration_minutes, language, options)
        result = {
            'status': 'plan_ready',
            'task_id': task_id,
            'title': package.get('title'),
            'scene_count': len(package.get('scenes') or []),
            'package': package,
        }
        mark_success(task_id, result, state='AWAITING_APPROVAL')
        return result
    except Exception as exc:
        if (
            # Every non-retryable planning error must also finish the stored
            # job; Celery will not deliver the advertised plan_retry stage.
            not isinstance(exc, (
                ProductionContentError, ImmutableNarrationSceneBudgetError, UnsupportedLanguageError, SpendBlocked,
                AbacusGenerationError,
            ))
            and int(getattr(self.request, 'retries', 0) or 0)
            < int(self.max_retries or 0)
        ):
            set_stage(
                self,
                task_id,
                'plan_retry',
                62,
                'Storyboard denetimi yenileniyor; ücretli medya üretimi başlamadı.',
            )
            raise
        mark_failure(task_id, exc)
        raise


@celery.task(
    bind=True,
    # Paid video-generation calls are intentionally at-most-once. The project-wide
    # Celery setting uses late acknowledgements, which can redeliver the whole
    # pipeline after a worker loss and duplicate an accepted provider charge.
    # Explicit pre-media retries below still enqueue normally through Celery.
    acks_late=False,
    autoretry_for=(Exception,),
    dont_autoretry_for=(
        ProductionContentError,
        FinalVisualQualityError,
        FinalAudioQualityError,
        GeminiOmniContinuityReferenceError,
        ImmutableNarrationSceneBudgetError,
        UnsupportedLanguageError,
        SpendBlocked,
        AbacusGenerationError,
    ),
    retry_backoff=True,
    max_retries=2,
)
@spending_task
def run_video_pipeline(
    self,
    topic: str,
    duration_minutes: float = 5,
    language: str = 'tr',
    channel_id: str | None = None,
    options: dict | None = None,
    approved_package: dict | None = None,
    retry_dispatch_source_id: str | None = None,
    curated_stock_manifest: dict | None = None,
    voice_replacement_source_id: str | None = None,
    full_rebuild_source_id: str | None = None,
):
    language = normalize_pipeline_language(language)
    task_id = self.request.id
    retry_number = int(getattr(self.request, 'retries', 0) or 0)
    _guard_retry_child_execution(
        self,
        task_id,
        retry_dispatch_source_id,
    )
    options = _normalized_options(options, duration_minutes)
    work = Path('/tmp/youtube_factory') / f'{task_id}_attempt_{retry_number}'
    work.mkdir(parents=True, exist_ok=True)
    update_job(task_id, kind='render', spec=_task_spec(topic, duration_minutes, language, channel_id, options))
    runway_attempts = 0
    total_paid_create_cap = preview_total_paid_create_cap(
        options, duration_minutes,
    )
    final_runway_repair_attempts = 0
    final_runway_repair_scenes: list[int] = []
    final_runway_repair_failures: list[int] = []
    generated_checkpoint_specs: dict[int, list[dict]] = {}
    generated_asset_candidate_journal: list[dict] = []
    staged_recovered_scenes: dict[str, list[dict]] | None = None
    staged_voice_contract: dict | None = None
    media_started = False

    try:
        full_rebuild_request = _prepare_full_video_rebuild(
            task_id, full_rebuild_source_id,
            _task_spec(topic, duration_minutes, language, channel_id, options),
            retry_dispatch_source_id=retry_dispatch_source_id,
            approved_package=approved_package, curated_stock_manifest=curated_stock_manifest,
            voice_replacement_source_id=voice_replacement_source_id,
        )
        if full_rebuild_request is not None and (
            type(total_paid_create_cap) is not int
            or total_paid_create_cap != full_rebuild_request['original_paid_create_cap']
        ):
            raise FinalVisualQualityError('Full rebuild paid-create budget differs from its authorization')
        if curated_stock_manifest is not None and (
            not isinstance(approved_package, dict) or not approved_package
            or not retry_dispatch_source_id
        ):
            raise FinalVisualQualityError('Curated recovery requires an approved server-dispatched package')
        if total_paid_create_cap is not None:
            paid_create_budget = _persisted_paid_create_budget(
                task_id, total_paid_create_cap,
            )
            total_paid_create_cap = paid_create_budget['cap']
            runway_attempts = paid_create_budget['used']
        if full_rebuild_request is not None and (
            total_paid_create_cap != full_rebuild_request['original_paid_create_cap']
        ):
            raise FinalVisualQualityError('Full rebuild paid-create budget differs from its authorization')
        if retry_dispatch_source_id and approved_package is None and full_rebuild_request is None:
            set_stage(self, task_id, 'director_qc', 14, 'Kaydedilmiş anlatım güncel hikâye denetiminden geçiriliyor.')
        saved_voice_retry = (
            _prepare_saved_voice_retry(
                task_id,
                retry_dispatch_source_id,
                _task_spec(topic, duration_minutes, language, channel_id, options),
                work,
            )
            if approved_package is None and full_rebuild_request is None else None
        )
        if saved_voice_retry and saved_voice_retry.get('preserve_audio_bytes') is True:
            # A verified standalone retry gets its own existing durable ledger;
            # the legacy parent's absent preview ledger and frozen spec stay intact.
            longform_budget = _persisted_paid_create_budget(task_id, 2)
            if longform_budget.get('cap') != 2 or longform_budget.get('used') != 0:
                raise FinalVisualQualityError('Long-form retry requires its fresh two-create media budget')
            total_paid_create_cap = 2
            runway_attempts = 0
        if voice_replacement_source_id is not None and (
            voice_replacement_source_id != retry_dispatch_source_id
            or approved_package is not None or curated_stock_manifest is not None
        ):
            raise FinalAudioQualityError('Voice replacement requires its exact claimed source')
        voice_replacement_request = (
            _prepare_voice_replacement_request(
                task_id, voice_replacement_source_id,
                _task_spec(topic, duration_minutes, language, channel_id, options), saved_voice_retry,
            ) if voice_replacement_source_id is not None else None
        )
        fresh_scheduled_shot_prompts = _fresh_scheduled_short_shots(
            task_id, _task_spec(topic, duration_minutes, language, channel_id, options),
            approved_package=approved_package, retry_dispatch_source_id=retry_dispatch_source_id,
            curated_stock_manifest=curated_stock_manifest,
            voice_replacement_source_id=voice_replacement_source_id, paid_slots_used=runway_attempts,
            full_rebuild_verified=full_rebuild_request is not None,
        )
        if full_rebuild_request is not None and not fresh_scheduled_shot_prompts:
            raise FinalVisualQualityError('Full rebuild requires an untouched authorized child')
        from app.services.planning_model_routing import fresh_planning_route

        with fresh_planning_route(enabled=fresh_scheduled_shot_prompts) as planning_route:
            if isinstance(planning_route, dict):
                update_job(task_id, story_planning_route=dict(planning_route))
            package = (
                saved_voice_retry['package'] if saved_voice_retry
                else _prepare_package(
                    self, task_id, topic, duration_minutes, language, options, approved_package,
                    **({'fresh_scheduled': True} if fresh_scheduled_shot_prompts else {}),
                )
            )
            if fresh_scheduled_shot_prompts:
                package = _prepare_scheduled_short_shots(
                    task_id, package, topic, duration_minutes, language, options,
                )
        raw_recovered_generated_media = package.pop(
            '_recovered_generated_media',
            None,
        )
        raw_recovered_voice = package.pop('_recovered_voice', None)
        if (
            (
                raw_recovered_generated_media is not None
                or raw_recovered_voice is not None
            )
            and approved_package is None
        ):
            raise FinalVisualQualityError(
                'Recovered media requires an approved storyboard'
            )
        package_sha256 = _recovery_package_sha256(package)
        recovered_generated_media = _validated_recovered_generated_media(
            raw_recovered_generated_media,
            len(package.get('scenes') or []),
            package_sha256,
        )
        recovered_voice = _validated_recovered_voice(
            raw_recovered_voice,
            len(package.get('scenes') or []),
            package_sha256,
        )
        if (isinstance(raw_recovered_generated_media, dict) and raw_recovered_generated_media.get('version') == 7
                and (not isinstance(curated_stock_manifest, dict)
                     or curated_stock_manifest.get('kind') != 'owner_plan_retained')):
            raise FinalVisualQualityError('Stock-only recovery requires its private owner-plan dispatch')
        if (
            recovered_voice
            and not recovered_generated_media
        ) or (
            recovered_generated_media
            and recovered_generated_media.get('version') in (2, 3, 4, 5, 6)
            and not recovered_voice
        ):
            raise FinalVisualQualityError(
                'Scene-repair recovery requires matching media and voice '
                'contracts'
            )
        if (
            recovered_generated_media
            and recovered_voice
            and recovered_generated_media['source_task_id']
            != recovered_voice['source_task_id']
        ):
            raise FinalVisualQualityError(
                'Recovered media and voice source tasks do not match'
            )
        scenes = package['scenes']
        selected_recovery = None
        if recovered_generated_media and recovered_generated_media.get('version') == 6:
            if (curated_stock_manifest is not None or full_rebuild_request is not None
                    or voice_replacement_source_id is not None or not recovered_voice
                    or recovered_voice.get('version') != 6 or type(total_paid_create_cap) is not int):
                raise FinalVisualQualityError('Selected recovery requires its own bounded voice/media contract')
            _validate_paid_create_allocation(
                [{'scene_index': index} for index in range(len(scenes))],
                recovered_generated_media, total_paid_create_cap, paid_slots_used=runway_attempts,
            )
            selected_recovery = _prepare_selected_worker_recovery(
                task_id, retry_dispatch_source_id,
                _task_spec(topic, duration_minutes, language, channel_id, options),
                approved_package, package, recovered_generated_media, recovered_voice, work,
            )
        if options.get('production_delivery') is not None:
            from app.services.production_delivery import delivery_requested, bind_delivery_plan

            delivery_requested(options, duration_minutes)
            if (getattr(settings, 'studio_spend_enforcement', False) is not True
                    or package.get('delivery_plan') != bind_delivery_plan(package)):
                raise FinalVisualQualityError('Long-form delivery package or budget is not commissioned')
        if recovered_generated_media and recovered_generated_media.get('version') == 4:
            if (
                options.get('mode') != 'production'
                or options.get('format') != 'shorts'
                or duration_minutes != 0.5
                or type(total_paid_create_cap) is not int
                or total_paid_create_cap < 1
            ):
                raise FinalVisualQualityError(
                    'V4 repair requires a bounded thirty-second production Short'
                )
            _validate_paid_create_allocation(
                [{'scene_index': index} for index in range(len(scenes))],
                recovered_generated_media, total_paid_create_cap,
                paid_slots_used=runway_attempts,
            )
        curated_source_job = (
            _mixed_recovery_source(
                task_id, retry_dispatch_source_id,
                _task_spec(topic, duration_minutes, language, channel_id, options),
                approved_package, recovered_generated_media, recovered_voice,
                total_paid_create_cap, paid_slots_used=runway_attempts,
            ) if recovered_generated_media and recovered_generated_media.get('version') == 5
            and curated_stock_manifest is None else (
                _curated_recovery_source(
                    task_id, retry_dispatch_source_id,
                    _task_spec(topic, duration_minutes, language, channel_id, options),
                    approved_package, curated_stock_manifest,
                    recovered_generated_media, recovered_voice,
                ) if curated_stock_manifest is not None else None
            )
        )
        _preflight_production_shorts_paid_plan(
            options,
            scenes,
            recovered_generated_media,
            total_paid_create_cap,
            paid_slots_used=runway_attempts,
        )
        scene_repair_recovery = bool(
            recovered_generated_media
            and recovered_generated_media.get('version') in (2, 4, 5, 6)
        )
        recovery_repair_scene_indices = set(
            recovered_generated_media.get('repair_scene_indices') or []
            if scene_repair_recovery
            else []
        )
        recovery_paid_scene_indices = (
            recovery_repair_scene_indices if selected_recovery is not None else (
                set(recovered_generated_media.get('scenes') or {})
                | recovery_repair_scene_indices
                if scene_repair_recovery else set()
            )
        )
        (work / 'package.json').write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding='utf-8')

        strict_short_preview_duration = (
            options.get('mode') == 'preview'
            and duration_minutes <= 0.6
        )
        generation_aspect_ratio = aspect_ratio_for_mode(
            options.get('mode'), options.get('format')
        )
        pexels_orientation = (
            'portrait'
            if generation_aspect_ratio == '9:16'
            else 'landscape'
        )
        set_stage(self, task_id, 'voice_and_visuals', 24, 'Anlatıcı ve görsel adaylar paralel hazırlanıyor.')
        # Even a failed/unknown voice response is media work. A later local
        # exception must not send the whole research/voice job through Celery
        # again merely because no paid video-generation slot was used.
        media_started = True
        with ThreadPoolExecutor(max_workers=2) as stage_pool:
            if selected_recovery is not None:
                voice_future = stage_pool.submit(deepcopy, selected_recovery['voice_result'])
            elif saved_voice_retry:
                if voice_replacement_request:
                    voice_future = stage_pool.submit(
                        _synthesize_voice_candidate, scenes, task_id, duration_minutes * 60,
                        language=language, voice_replacement_request=voice_replacement_request,
                    )
                elif saved_voice_retry.get('preserve_audio_bytes') is True:
                    voice_future = stage_pool.submit(_preserved_longform_voice_for_retry, saved_voice_retry)
                else:
                    voice_future = stage_pool.submit(
                        _fit_saved_voice_for_retry,
                        saved_voice_retry['voice_result'],
                        _effective_short_edit_target(
                            options, duration_minutes * 60,
                            saved_voice_retry['voice_result'].get('duration_after_fit'),
                        ),
                    )
            elif recovered_voice:
                voice_future = stage_pool.submit(
                    _download_recovered_voice_candidate,
                    recovered_voice,
                    work,
                )
            else:
                voice_future = stage_pool.submit(
                    _synthesize_voice_candidate,
                    scenes,
                    task_id,
                    duration_minutes * 60,
                    language=language,
                    **({'flexible_short': True} if options.get('production_scheduled') is True
                       and options.get('mode') == 'production' and options.get('format') == 'shorts'
                       and duration_minutes == 0.5 else {}),
                )
            broll_future = (
                stage_pool.submit(lambda: {
                    'scene_visuals': deepcopy(selected_recovery['scene_visuals']), 'credits': [],
                    'seen_ids': {spec['pexels_id'] for pool in selected_recovery['scene_visuals']
                                 for spec in pool if type(spec.get('pexels_id')) is int},
                }) if selected_recovery is not None else
                stage_pool.submit(
                    _collect_curated_recovery_visuals,
                    curated_stock_manifest, curated_source_job, approved_package,
                    recovered_generated_media, recovered_voice, task_id, work,
                ) if curated_source_job is not None
                else stage_pool.submit(
                    _collect_broll, scenes, work, strict_short_preview_duration,
                    orientation=pexels_orientation,
                )
            )
            voice_result = voice_future.result()
            _checkpoint_audio_candidate(task_id, package, voice_result)
            if voice_replacement_request:
                _require_voice_replacement_checkpoint(task_id, voice_result)
            broll_result = broll_future.result()

        voice_path = voice_result['path']
        scene_durations = voice_result['scene_durations']
        expected_spoken_narration = ' '.join(
            str(text or '').strip()
            for text in (voice_result.get('spoken_texts') or [])
            if str(text or '').strip()
        )
        set_stage(
            self,
            task_id,
            'audio_qc',
            38,
            'Anlatım metni, telaffuz ve kelime zamanları gerçek ses üzerinden denetleniyor.',
        )
        audio_generation_attempts = int(
            voice_result.get('_generation_attempts_used') or 1
        )
        audio_qc_retry_history: list[dict] = []
        selected_audio_generation_attempt = int(
            voice_result.get('_generation_attempt') or 0
        )
        audio_synthesis_quality_errors: list[dict] = list(
            voice_result.get('_synthesis_quality_errors') or []
        )
        short_form_prosody_required = bool(
            0 < duration_minutes * 60 <= 40
            and str(language or '').lower() in {'tr', 'en'}
        )
        audio_prosody_qc: dict = {
            'available': False,
            'pass': False,
            'provider': None,
            'reason': 'not_run',
            'scores': None,
            'issues': [],
            'summary': None,
        }
        audio_duration_qc: dict = {}
        audio_pause_repair_attempted = bool(
            voice_result.get('_internal_pause_repair_attempted')
            or voice_result.get('internal_pause_repair')
        )
        while True:
            audio_review_sha256 = (
                _audio_qa_fingerprint(voice_path)
                if short_form_prosody_required and not recovered_voice
                else None
            )
            audio_qc = _verify_audio_narration_with_retry(
                voice_path,
                expected_spoken_narration,
                language=language,
                task_id=task_id,
            )
            if audio_review_sha256:
                audio_qc = {**audio_qc, 'audio_sha256': audio_review_sha256}
            effective_edit_target_seconds = (
                selected_recovery['timing']['effective_edit_target_seconds']
                if selected_recovery is not None else _effective_short_edit_target(
                    options, duration_minutes * 60, voice_result.get('duration_after_fit'),
                )
            )
            audio_duration_qc = _short_preview_voice_duration_qc(
                voice_result,
                effective_edit_target_seconds,
            )
            transcript_passed = bool(
                audio_qc.get('available') is True
                and audio_qc.get('pass') is True
            )
            duration_passed = bool(
                audio_duration_qc.get('available') is True
                and audio_duration_qc.get('pass') is True
            )
            if (
                transcript_passed
                and duration_passed
                and short_form_prosody_required
            ):
                audio_prosody_qc = verify_audio_prosody(
                    voice_path,
                    expected_spoken_narration,
                    audio_duration_seconds=voice_result.get(
                        'duration_after_fit'
                    ),
                    transcript_evidence=audio_qc,
                    **({'language': 'en'} if language == 'en' else {}),
                )
            elif transcript_passed and duration_passed:
                audio_prosody_qc = {
                    'available': True,
                    'pass': True,
                    'provider': None,
                    'reason': 'not_applicable_language_or_long_form',
                    'scores': None,
                    'issues': [],
                    'summary': None,
                }
            else:
                audio_prosody_qc = {
                    'available': False,
                    'pass': False,
                    'provider': None,
                    'reason': (
                        'not_run_transcript_rejected'
                        if not transcript_passed
                        else 'not_run_duration_rejected'
                    ),
                    'scores': None,
                    'issues': [],
                    'summary': None,
                }
            prosody_passed = bool(
                audio_prosody_qc.get('available') is True
                and audio_prosody_qc.get('pass') is True
            )
            audio_qc_retry_history.append({
                'generation_attempt': int(
                    voice_result.get('_generation_attempt')
                    if voice_result.get('_generation_attempt') is not None
                    else audio_generation_attempts - 1
                ),
                'voice_model': voice_result.get('voice_model'),
                'voice_language_code': voice_result.get(
                    'voice_language_code'
                ),
                'transcript': {
                    'available': audio_qc.get('available') is True,
                    'pass': audio_qc.get('pass') is True,
                    'provider': audio_qc.get('provider'),
                    'score': audio_qc.get('score'),
                    'reason': audio_qc.get('reason'),
                },
                'duration': dict(audio_duration_qc),
                'prosody': dict(audio_prosody_qc),
            })
            if transcript_passed and duration_passed and prosody_passed:
                selected_audio_generation_attempt = (
                    audio_generation_attempts - 1
                )
                break
            if (
                not audio_pause_repair_attempted
                and not recovered_voice
                and short_form_prosody_required
                and language == 'tr'
                and transcript_passed and duration_passed
                and audio_prosody_qc.get('available') is True
                and audio_prosody_qc.get('pass') is False
            ):
                audio_pause_repair_attempted = True
                repaired_voice = _repair_voice_internal_pauses(
                    voice_result, effective_edit_target_seconds,
                    audio_qc, audio_prosody_qc,
                )
                if repaired_voice is not None:
                    voice_result = repaired_voice
                    voice_path = voice_result['path']
                    scene_durations = voice_result['scene_durations']
                    update_job(task_id, audio_pause_repair=voice_result['internal_pause_repair'])
                    _checkpoint_audio_candidate(task_id, package, voice_result)
                    set_stage(
                        self, task_id, 'audio_pause_recheck', 38,
                        'Kanıtlanmış cümle içi boşluklar düzeltildi; aynı ses yeniden denetleniyor.',
                    )
                    # The edit invalidates every old timestamp and verdict.
                    # Re-run the complete STT, duration and prosody gates on
                    # the edited file; do not spend a fresh synthesis seed.
                    continue
            audio_mismatch = (
                audio_qc.get('mismatch_details')
                if isinstance(audio_qc.get('mismatch_details'), dict)
                else {}
            )
            can_regenerate = (
                not recovered_voice
                and not saved_voice_retry
                and audio_generation_attempts < MAX_AUDIO_GENERATION_ATTEMPTS
                and audio_qc.get('available') is True
                and audio_duration_qc.get('available') is True
                # An independent script-length rejection cannot be repaired
                # by reseeding the same text, even when transcript QA also fails.
                and (duration_passed or audio_duration_qc.get('retryable') is True)
                and (
                    (
                        audio_qc.get('available') is True
                        and audio_qc.get('pass') is False
                    )
                    or (
                        audio_duration_qc.get('available') is True
                        and audio_duration_qc.get('pass') is False
                        and audio_duration_qc.get('retryable') is True
                    )
                    or (
                        transcript_passed
                        and duration_passed
                        and audio_prosody_qc.get('available') is True
                        and audio_prosody_qc.get('pass') is False
                    )
                )
            )
            if not can_regenerate:
                from app.services.production_failures import content_rejection

                try:
                    update_job(task_id, audio_qc_failure_evidence=_audio_qc_failure_evidence(
                        audio_qc_retry_history,
                    ))
                except Exception:
                    # Diagnostic persistence must never mask or relax rejection.
                    pass
                raise content_rejection(FinalAudioQualityError(
                    'Audio narration QA rejected before paid media: '
                    + json.dumps(
                        {
                            'available': bool(audio_qc.get('available')),
                            'score': audio_qc.get('score'),
                            'reason': audio_qc.get('reason'),
                            'generation_attempts': audio_generation_attempts,
                            'duration_qc': audio_duration_qc,
                            'prosody_qc': {
                                'available': audio_prosody_qc.get(
                                    'available'
                                ),
                                'pass': audio_prosody_qc.get('pass'),
                                'reason': audio_prosody_qc.get('reason'),
                                'scores': audio_prosody_qc.get('scores'),
                                'issues': (
                                    audio_prosody_qc.get('issues') or []
                                )[:3],
                            },
                            'attempt_reasons': [
                                {
                                    'generation_attempt': item.get(
                                        'generation_attempt'
                                    ),
                                    'transcript_reason': (
                                        item.get('transcript') or {}
                                    ).get('reason'),
                                    'duration_reason': (
                                        item.get('duration') or {}
                                    ).get('reason'),
                                    'prosody_reason': (
                                        item.get('prosody') or {}
                                    ).get('reason'),
                                }
                                for item in audio_qc_retry_history[-3:]
                            ],
                            'synthesis_quality_errors': (
                                audio_synthesis_quality_errors[-3:]
                            ),
                            'mismatch_details': {
                                'missing_words': (
                                    audio_mismatch.get('missing_words') or []
                                )[:8],
                                'unexpected_words': (
                                    audio_mismatch.get('unexpected_words') or []
                                )[:8],
                                'operations': (
                                    audio_mismatch.get('operations') or []
                                )[:4],
                                'sequence_ratio': audio_mismatch.get(
                                    'sequence_ratio'
                                ),
                            },
                        },
                        ensure_ascii=False,
                        separators=(',', ':'),
                    )
                ), 'audio_quality_exhausted' if any(
                    gate.get('available') is True and gate.get('pass') is False
                    for gate in (audio_qc, audio_duration_qc, audio_prosody_qc)
                ) else 'audio_review_unverified')
            set_stage(
                self,
                task_id,
                'audio_qc_retry',
                38,
                'Anlaşılır ama doğal duyulmayan ses farklı bir güvenli seed ile yeniden üretiliyor.',
            )
            voice_result = _synthesize_voice_candidate(
                scenes,
                task_id,
                duration_minutes * 60,
                start_attempt=audio_generation_attempts,
                language=language,
                **({'flexible_short': True} if options.get('production_scheduled') is True
                   and options.get('mode') == 'production' and options.get('format') == 'shorts'
                   and duration_minutes == 0.5 and not saved_voice_retry and not recovered_voice else {}),
            )
            _checkpoint_audio_candidate(task_id, package, voice_result)
            audio_generation_attempts = int(
                voice_result.get('_generation_attempts_used')
                or audio_generation_attempts + 1
            )
            audio_synthesis_quality_errors.extend(
                voice_result.get('_synthesis_quality_errors') or []
            )
            voice_path = voice_result['path']
            scene_durations = voice_result['scene_durations']
            expected_spoken_narration = ' '.join(
                str(text or '').strip()
                for text in (voice_result.get('spoken_texts') or [])
                if str(text or '').strip()
            )
        scene_visuals: list[list[str | dict]] = broll_result['scene_visuals']
        if strict_short_preview_duration:
            for scene_idx, specs in enumerate(scene_visuals):
                try:
                    required_source_duration = max(
                        5.0,
                        float(scene_durations[scene_idx]) + 0.35,
                    )
                except Exception:
                    required_source_duration = 5.0
                duration_safe_specs: list[str | dict] = []
                for spec in specs:
                    try:
                        source_duration = float(
                            spec.get('source_duration') or 0
                            if isinstance(spec, dict)
                            else 0
                        )
                    except Exception:
                        source_duration = 0.0
                    if source_duration >= required_source_duration:
                        duration_safe_specs.append(spec)
                scene_visuals[scene_idx] = duration_safe_specs
        credits = broll_result['credits']
        seen_ids = broll_result['seen_ids']
        if not recovered_generated_media and selected_recovery is None:
            from app.services.included_stock_pool import retain_stock_pool
            retain_stock_pool(task_id, package, scene_visuals, credits, seen_ids, work, phase='initial')
        # The fixed production Short uses distinct final stock clips. Keep
        # legacy preview/long-form reuse behavior unchanged.
        stock_reuse_visuals = (
            scene_visuals
            if options.get('mode') == 'production'
            and options.get('format') == 'shorts'
            and duration_minutes == 0.5
            else None
        )

        set_stage(self, task_id, 'visual_qc', 45, 'Her sahnenin aday görüntüleri gerçek kareler üzerinden karşılaştırılıyor.')
        visual_qc: dict = {'reviews': []}
        music_result: dict | None = None
        music_error: str | None = None
        should_generate_music = options.get('mode') == 'production' and options.get('music') == 'auto'

        with ThreadPoolExecutor(max_workers=2 if should_generate_music else 1) as quality_pool:
            if selected_recovery is not None:
                _selected_recovery_authorization(
                    task_id, retry_dispatch_source_id,
                    _task_spec(topic, duration_minutes, language, channel_id, options), approved_package,
                )
                qc_future = quality_pool.submit(
                    _review_selected_exact, selected_recovery, scenes, work, topic, options,
                    package.get('sources') or [],
                )
            elif recovered_generated_media and recovered_generated_media.get('version') == 5:
                qc_future = quality_pool.submit(
                    _review_mixed_retained_before_paid, scenes, scene_visuals, voice_result,
                    work, topic, options, package.get('sources') or [],
                )
            else:
                qc_future = quality_pool.submit(
                    review_scene_visuals,
                    scenes,
                    scene_visuals,
                    work,
                    len(scenes) if duration_minutes <= 1 or options.get('content_plan_item_id') else min(14, len(scenes)),
                    topic=topic,
                    story_scenes=scenes,
                    content_style=options.get('content_style', ''),
                    evidence_sources=package.get('sources') or [],
                )
            music_future = None
            if should_generate_music:
                music_future = quality_pool.submit(
                    generate_music_bed,
                    topic,
                    str(options.get('content_style') or 'documentary'),
                    work / 'music_bed.mp3',
                    loop_seconds=36.0,
                )
            visual_qc = qc_future.result()
            if music_future:
                try:
                    music_result = music_future.result()
                except Exception as exc:
                    music_error = str(exc)[:600]

        visual_replacements: list[dict] = []
        reviews_by_scene = {
            int(r.get('scene_index')): r
            for r in (visual_qc.get('reviews') or [])
            if isinstance(r, dict) and str(r.get('scene_index', '')).lstrip('-').isdigit()
        }
        quality_threshold = int(options.get('quality_threshold') or 80)

        for scene_idx, _scene in enumerate(scenes):
            if any(isinstance(spec, dict) and spec.get('curated_pinned') is True
                   for spec in scene_visuals[scene_idx]):
                # Keep the exact pinned selection, not the critic's preferred
                # moment from a different part of the full source clip.
                continue
            is_short_preview_authored_ai = (
                strict_short_preview_duration
                and bool(str(_scene.get('ai_prompt') or '').strip())
            )
            review = reviews_by_scene.get(scene_idx)
            paths = [p for p in scene_visuals[scene_idx] if _visual_path(p)]
            if not paths:
                scene_visuals[scene_idx] = []
                if is_short_preview_authored_ai:
                    raw_refill_queries = _scene.get('visual_queries') or []
                    if isinstance(raw_refill_queries, str):
                        raw_refill_queries = [raw_refill_queries]
                    refill_queries = [
                        str(query).strip()
                        for query in raw_refill_queries[:2]
                        if str(query).strip()
                    ]
                    duration_refill = _retry_bad_scene(
                        scene_idx,
                        refill_queries,
                        seen_ids,
                        work,
                        credits,
                        file_prefix='duration_refill',
                        max_replacements=3,
                        minimum_duration=max(
                            5.0,
                            float(scene_durations[scene_idx]) + 0.35,
                        ),
                        allow_short_fallback=False,
                        tolerate_pexels_failure=True,
                        orientation=pexels_orientation,
                    )
                    scene_visuals[scene_idx] = duration_refill
                    if duration_refill:
                        visual_replacements.append({
                            'scene_index': scene_idx,
                            'score': -1,
                            'reason': 'Initial candidates were shorter than the narration.',
                            'old_best': '',
                            'replacement_count': len(duration_refill),
                            'stage': 'pre_runway_duration_refill',
                        })
                continue

            if not review:
                first_spec = dict(paths[0]) if isinstance(paths[0], dict) else {'path': _visual_path(paths[0])}
                first_spec['start_fraction'] = 0.25
                scene_visuals[scene_idx] = [first_spec]
                continue

            best_idx = int(review.get('best_candidate_index', 0))
            score = int(review.get('score', 0))
            best_idx = min(max(best_idx, 0), len(paths) - 1)
            best_path = _visual_path(paths[best_idx])
            try:
                best_fraction = float(review.get('best_start_fraction', 0.25))
            except Exception:
                best_fraction = 0.25
            best_fraction = max(0.0, min(best_fraction, 0.95))
            best_spec = (
                dict(paths[best_idx])
                if isinstance(paths[best_idx], dict)
                else {'path': best_path}
            )
            best_spec['start_fraction'] = best_fraction

            if score >= quality_threshold:
                scene_visuals[scene_idx] = [best_spec]
                continue
            is_short_preview_stock = (
                options.get('mode') == 'preview'
                and duration_minutes <= 0.6
                and not str(_scene.get('ai_prompt') or '').strip()
            )
            if is_short_preview_stock:
                # Preserve the evidence-backed incumbent. The bounded stock
                # tournament below compares it with a wider deterministic pool.
                scene_visuals[scene_idx] = [best_spec]
                continue

            retry_queries = [str(q).strip() for q in (review.get('retry_queries') or [])[:2] if str(q).strip()]
            replacements = _retry_bad_scene(
                scene_idx,
                retry_queries,
                seen_ids,
                work,
                credits,
                minimum_duration=max(5.0, float(scene_durations[scene_idx]) + 0.35),
                allow_short_fallback=not strict_short_preview_duration,
                tolerate_pexels_failure=is_short_preview_authored_ai,
                orientation=pexels_orientation,
                active_scene_visuals=stock_reuse_visuals,
            )
            scene_visuals[scene_idx] = [*replacements, best_spec][:3]
            if replacements:
                visual_replacements.append({
                    'scene_index': scene_idx,
                    'score': score,
                    'reason': review.get('reason'),
                    'old_best': best_path,
                    'replacement_count': len(replacements),
                })

        set_stage(self, task_id, 'ai_scene', 61, 'Güncel stok kalitesi ölçülüyor; en zor sahneler özgün görüntüye ayrılıyor.')
        runway_failure_diagnostics: list[dict] = []
        runway_failed_scenes: list[int] = []
        runway_generated_scenes: list[int] = []
        image_motion_submission_scenes: set[int] = set()
        omni_unsafe_submission_scenes: set[int] = set()
        generated_video_provider_records: list[dict] = []
        runway_scenes_used = 0
        runway_submission_cap = _max_runway_scenes(options, len(scenes), duration_minutes)
        from app.services.commissioning_video import completion_capacity, completion_repairs
        commissioning_capacity = completion_capacity(
            options, duration_minutes, len(scenes), total_paid_create_cap, runway_attempts,
        )
        if commissioning_capacity is not None:
            runway_submission_cap = commissioning_capacity
        if total_paid_create_cap is not None:
            runway_submission_cap = min(
                runway_submission_cap,
                total_paid_create_cap,
            )

        if not recovered_generated_media and selected_recovery is None:
            from app.services.included_stock_pool import retain_stock_pool
            retain_stock_pool(task_id, package, scene_visuals, credits, seen_ids, work, phase='before_generation')

        # Score the exact clips selected after stock retries. Paid Runway slots
        # are ranked by current evidence, never scene order or stale scores.
        pre_runway_qc = visual_qc if (
            recovered_generated_media and recovered_generated_media.get('version') in (5, 6)
        ) else review_scene_visuals(
            scenes,
            scene_visuals,
            work / 'pre_runway_visual_qc',
            len(scenes),
            topic=topic,
            story_scenes=scenes,
            content_style=options.get('content_style', ''),
            evidence_sources=package.get('sources') or [],
        )
        current_reviews = {
            int(review.get('scene_index')): review
            for review in (pre_runway_qc.get('reviews') or [])
            if isinstance(review, dict)
            and str(review.get('scene_index', '')).lstrip('-').isdigit()
        }
        missing_pre_runway_reviews = [
            int(idx)
            for idx in (pre_runway_qc.get('missing_review_indices') or [])
            if str(idx).lstrip('-').isdigit()
        ]
        if missing_pre_runway_reviews:
            raise PreRunwayRetryableError(
                'Pre-Runway visual QC was incomplete before any paid submission: '
                + json.dumps({'missing_scene_indices': missing_pre_runway_reviews}, separators=(',', ':'))
            )

        is_bounded_short_preview = (
            options.get('mode') == 'preview'
            and duration_minutes <= 0.6
            and runway_submission_cap > 0
        )
        is_private_image_motion_preview = (
            is_bounded_short_preview
            and duration_minutes == 0.5
            and options.get('quality_threshold')
            == MANUAL_QA_PUBLISH_QUALITY_THRESHOLD
        )
        # The publish lifecycle enforces private visibility for every initial
        # preview upload; public release remains a separate confirmed action.
        is_private_ai_first_omni_preview = (
            is_bounded_short_preview
            and (options.get('visual_mix') or 'balanced') == 'ai_first'
        )
        provider_outage_stock_scenes: set[int] = set()
        stock_quality_fallback_scenes: set[int] = set()
        manual_qa_preview_scenes: set[int] = set()
        manual_qa_preview_scores: dict[int, int] = {}
        manual_qa_preview_records: dict[int, dict] = {}
        manual_qa_preview_identities: dict[int, tuple[object, ...]] = {}

        def register_manual_qa_preview(
            scene_idx: int,
            review: dict,
            visual_spec: str | dict | None,
        ) -> None:
            manual_qa_preview_scenes.add(scene_idx)
            manual_qa_preview_scores[scene_idx] = int(review['score'])
            manual_qa_preview_records[scene_idx] = (
                _manual_qa_preview_record(
                    scene_idx,
                    review,
                    visual_spec,
                )
            )
            identity = _manual_qa_visual_identity(visual_spec)
            if identity is None:
                raise FinalVisualQualityError(
                    'Manual-QA accepted a visual without an exact identity'
                )
            manual_qa_preview_identities[scene_idx] = identity
            # Forced sets describe unresolved paid fallbacks. Preserve their
            # history separately, but never expose an accepted manual-QA clip
            # as simultaneously unresolved.
            provider_outage_stock_scenes.discard(scene_idx)
            stock_quality_fallback_scenes.discard(scene_idx)

        # A stock-routed short-preview scene is a contract: real footage
        # must clear the same semantic gate before any paid Runway request.
        # The sole exception is a typed provider-access failure after Pexels'
        # bounded retries; that scene remains unapproved and must instead pass
        # generated-clip QC. Compare every reachable stock candidate in at most
        # two batches of three, preserving the best result.
        if is_bounded_short_preview:
            stock_contract_candidates = [
                scene_idx
                for scene_idx, scene in enumerate(scenes)
                if not str(scene.get('ai_prompt') or '').strip()
                and (
                    scene_idx not in current_reviews
                    or not any(_visual_path(spec) for spec in scene_visuals[scene_idx])
                    or int(current_reviews[scene_idx].get('score', 0)) < quality_threshold
                )
            ]
            stock_candidate_pools: dict[int, list[dict]] = {}
            stock_best_results: dict[int, dict] = {}

            for scene_idx in stock_contract_candidates:
                review = current_reviews.get(scene_idx) or {}
                incumbent_specs = [
                    dict(spec) if isinstance(spec, dict) else {'path': _visual_path(spec), 'start_fraction': 0.35}
                    for spec in scene_visuals[scene_idx][:1]
                    if _visual_path(spec)
                ]
                incumbent = incumbent_specs[0] if incumbent_specs else None
                stock_best_results[scene_idx] = {
                    'score': int(review.get('score', -1)),
                    'review': dict(review) if review else None,
                    'spec': incumbent,
                }

                raw_retry_queries = review.get('retry_queries') or []
                if isinstance(raw_retry_queries, str):
                    raw_retry_queries = [raw_retry_queries]
                raw_visual_queries = scenes[scene_idx].get('visual_queries') or []
                if isinstance(raw_visual_queries, str):
                    raw_visual_queries = [raw_visual_queries]
                query_pool: list[str] = []
                query_keys: set[str] = set()
                for raw_query in [*raw_retry_queries, *raw_visual_queries]:
                    query = str(raw_query or '').strip()
                    key = query.casefold()
                    if not query or key in query_keys:
                        continue
                    query_keys.add(key)
                    query_pool.append(query)
                    if len(query_pool) >= 3:
                        break

                try:
                    new_specs = _download_ranked_broll_candidates(
                        scene_idx,
                        query_pool,
                        seen_ids,
                        work,
                        credits,
                        file_prefix='pre_runway_stock_tournament',
                        selected_by='pre_runway_stock_tournament',
                        max_candidates=5,
                        search_limit=24,
                        minimum_duration=max(
                            5.0,
                            float(scene_durations[scene_idx]) + 0.35,
                        ),
                    )
                except PexelsRetryError:
                    # This typed error is emitted only after the bounded search
                    # or download attempts are exhausted. Do not approve the
                    # incumbent and do not catch any unrelated exception: mark
                    # this explicit STOCK scene for generated-clip fallback.
                    provider_outage_stock_scenes.add(scene_idx)
                    incumbent_specs = []
                    incumbent = None
                    scene_visuals[scene_idx] = []
                    stock_best_results[scene_idx] = {
                        'score': -1,
                        'review': dict(review) if review else None,
                        'spec': None,
                    }
                    new_specs = []
                frozen_pool: list[dict] = []
                frozen_paths: set[str] = set()
                for spec in [*incumbent_specs, *new_specs]:
                    path = _visual_path(spec)
                    if not path or path in frozen_paths:
                        continue
                    frozen_paths.add(path)
                    frozen_pool.append(
                        dict(spec) if isinstance(spec, dict)
                        else {'path': path, 'start_fraction': 0.35}
                    )
                stock_candidate_pools[scene_idx] = frozen_pool
                if new_specs:
                    visual_replacements.append({
                        'scene_index': scene_idx,
                        'score': int(review.get('score', -1)),
                        'reason': review.get('reason'),
                        'old_best': _visual_path(incumbent) if incumbent else '',
                        'replacement_count': len(new_specs),
                        'stage': 'pre_runway_stock_tournament',
                    })

            for round_index, (candidate_start, candidate_end) in enumerate(((0, 3), (3, 6)), start=1):
                active_scenes = [
                    scene_idx
                    for scene_idx in stock_contract_candidates
                    if int(stock_best_results[scene_idx].get('score', -1)) < quality_threshold
                    and stock_candidate_pools.get(scene_idx, [])[candidate_start:candidate_end]
                    and (
                        candidate_start > 0
                        or len(stock_candidate_pools.get(scene_idx, [])) > 1
                        or stock_best_results[scene_idx].get('spec') is None
                    )
                ]
                if not active_scenes:
                    continue

                set_stage(
                    self,
                    task_id,
                    f'pre_runway_stock_tournament_{round_index}',
                    62,
                    f'Stok sahneleri alaka sıralı adaylarla karşılaştırılıyor ({round_index}/2).',
                )
                round_visuals = [
                    stock_candidate_pools[scene_idx][candidate_start:candidate_end]
                    for scene_idx in active_scenes
                ]
                round_reviews = _review_stock_tournament_round(
                    scenes,
                    active_scenes,
                    round_visuals,
                    work / f'pre_runway_stock_tournament_{round_index}',
                    round_index,
                    topic,
                    content_style=options.get('content_style', ''),
                    evidence_sources=package.get('sources') or [],
                )

                for position, scene_idx in enumerate(active_scenes):
                    local_review = dict(round_reviews[position])
                    candidate_batch = list(round_visuals[position])
                    reviewed_wrapper = [candidate_batch]
                    _apply_visual_review(
                        reviewed_wrapper,
                        0,
                        local_review,
                        default_fraction=0.35,
                    )
                    chosen_spec = reviewed_wrapper[0][0] if reviewed_wrapper[0] else None
                    score = int(local_review.get('score', -1))
                    if chosen_spec is not None and (
                        stock_best_results[scene_idx].get('spec') is None
                        or score > int(stock_best_results[scene_idx].get('score', -1))
                    ):
                        mapped_review = dict(local_review)
                        mapped_review['scene_index'] = scene_idx
                        mapped_review['best_candidate_index'] = 0
                        mapped_review['best_start_fraction'] = chosen_spec.get('start_fraction', 0.35)
                        stock_best_results[scene_idx] = {
                            'score': score,
                            'review': mapped_review,
                            'spec': chosen_spec,
                        }

            for scene_idx in stock_contract_candidates:
                best_result = stock_best_results[scene_idx]
                best_spec = best_result.get('spec')
                best_review = best_result.get('review')
                scene_visuals[scene_idx] = [best_spec] if best_spec else []
                if best_review:
                    current_reviews[scene_idx] = best_review
                visual_replacements.append({
                    'scene_index': scene_idx,
                    'score': int(best_result.get('score', -1)),
                    'reason': (best_review or {}).get('reason'),
                    'old_best': '',
                    'replacement_count': len(stock_candidate_pools.get(scene_idx, [])),
                    'stage': 'pre_runway_stock_tournament_result',
                })

            failed_stock_contracts = []
            for scene_idx, scene in enumerate(scenes):
                if str(scene.get('ai_prompt') or '').strip():
                    continue
                review = current_reviews.get(scene_idx) or {}
                has_visual = any(
                    _visual_path(spec)
                    for spec in scene_visuals[scene_idx]
                )
                score = int(review.get('score', -1))
                if has_visual and score >= quality_threshold:
                    continue
                if scene_idx in provider_outage_stock_scenes:
                    # Provider outage is not a stock-quality pass. The scene is
                    # carried into the paid allocation and must still clear the
                    # exact generated-clip gate below.
                    continue
                raw_retry_queries = review.get('retry_queries') or []
                if isinstance(raw_retry_queries, str):
                    raw_retry_queries = [raw_retry_queries]
                failed_stock_contracts.append({
                    'scene_index': scene_idx,
                    'stock_score': score,
                    'has_visual': has_visual,
                    'reason': str(review.get('reason') or 'missing review')[:240],
                    'retry_queries': [
                        str(query).strip()[:120]
                        for query in raw_retry_queries[:3]
                        if str(query).strip()
                    ],
                })

            remaining_stock_contract_failures: list[dict] = []
            for failure in failed_stock_contracts:
                scene_idx = int(failure['scene_index'])
                review = current_reviews.get(scene_idx) or {}
                selected_spec = _reviewed_visual_spec(
                    scene_visuals[scene_idx],
                    review,
                )
                if _manual_qa_preview_passes(
                    options,
                    duration_minutes,
                    scenes[scene_idx],
                    review,
                    selected_spec,
                ):
                    register_manual_qa_preview(
                        scene_idx,
                        review,
                        selected_spec,
                    )
                    continue
                remaining_stock_contract_failures.append(failure)
            failed_stock_contracts = remaining_stock_contract_failures

            semantic_stock_quality_failures = [
                failure
                for failure in failed_stock_contracts
                if bool(failure.get('has_visual'))
                and int(failure.get('stock_score', -1)) >= 0
                and int(failure['scene_index']) in current_reviews
            ]
            unroutable_stock_failures = [
                failure
                for failure in failed_stock_contracts
                if failure not in semantic_stock_quality_failures
            ]
            stock_quality_cap_exceeded = (
                len(semantic_stock_quality_failures)
                > SHORT_PREVIEW_STOCK_QUALITY_RUNWAY_CAP
            )
            if (
                unroutable_stock_failures
                or stock_quality_cap_exceeded
            ):
                stock_contract_message = (
                    'Short-preview stock-quality fallback exceeds its hard cap '
                    'or lacks semantic stock evidence before any paid submission: '
                    + json.dumps(
                        {
                            'quality_threshold': quality_threshold,
                            'stock_quality_emergency_cap': (
                                SHORT_PREVIEW_STOCK_QUALITY_RUNWAY_CAP
                            ),
                            'stock_quality_cap_exceeded': (
                                stock_quality_cap_exceeded
                            ),
                            'unroutable_failures': unroutable_stock_failures,
                            'failures': failed_stock_contracts,
                        },
                        ensure_ascii=False,
                        separators=(',', ':'),
                    )
                )
                if stock_quality_cap_exceeded or approved_package is not None:
                    raise FinalVisualQualityError(stock_contract_message)
                raise PreRunwayRetryableError(stock_contract_message)

            for failure in semantic_stock_quality_failures:
                scene_idx = int(failure['scene_index'])
                old_best = (
                    _visual_path(scene_visuals[scene_idx][0])
                    if scene_visuals[scene_idx]
                    else ''
                )
                # The full bounded tournament has completed without clearing
                # the unchanged threshold. Quarantine its incumbent so a paid
                # generated clip (or a later re-qualified final rescue) is
                # mandatory; the low-score stock clip can never be rendered.
                scene_visuals[scene_idx] = []
                stock_quality_fallback_scenes.add(scene_idx)
                visual_replacements.append({
                    'scene_index': scene_idx,
                    'score': int(failure.get('stock_score', -1)),
                    'reason': failure.get('reason'),
                    'old_best': old_best,
                    'replacement_count': 0,
                    'stage': 'pre_runway_stock_quality_fallback',
                })

            # Do not spend an emergency slot if an incumbent unexpectedly
            # cleared the unchanged threshold despite the provider outage.
            provider_outage_stock_scenes = {
                scene_idx
                for scene_idx in provider_outage_stock_scenes
                if not (
                    any(_visual_path(spec) for spec in scene_visuals[scene_idx])
                    and int((current_reviews.get(scene_idx) or {}).get('score', -1))
                    >= quality_threshold
                )
            }
            manual_prepass_forced_overlap = manual_qa_preview_scenes & (
                provider_outage_stock_scenes
                | stock_quality_fallback_scenes
            )
            if manual_prepass_forced_overlap:
                raise FinalVisualQualityError(
                    'Manual-QA stock prepass must remain disjoint from forced '
                    'Runway allocation: '
                    + json.dumps(
                        {
                            'scene_indices': sorted(
                                manual_prepass_forced_overlap
                            )
                        },
                        separators=(',', ':'),
                    )
                )

        provider_outage_stock_fallback_history = set(
            provider_outage_stock_scenes
        )
        stock_quality_fallback_history = set(
            stock_quality_fallback_scenes
        )

        def rank_runway_candidates() -> tuple[dict[int, str], list[dict]]:
            prompts: dict[int, str] = {}
            ranked: list[dict] = []
            for candidate_scene_idx, scene in enumerate(scenes):
                if selected_recovery is not None and candidate_scene_idx not in recovery_repair_scene_indices:
                    continue
                candidate_review = current_reviews.get(candidate_scene_idx)
                if selected_recovery is not None:
                    prompt = selected_recovery['repair_prompts'][candidate_scene_idx]
                elif (
                    options.get('mode') == 'production' and options.get('format') == 'shorts'
                    and duration_minutes == 0.5 and recovered_generated_media
                    and recovered_generated_media.get('version') in (4, 5)
                    and candidate_scene_idx in recovery_repair_scene_indices
                ):
                    # Validate every explicit repair direction before the paid
                    # loop. Old search/critic staging must not overwrite or
                    # truncate the newly approved authored shooting contract.
                    from app.services.production_shot_prompt import build_production_shot_prompt
                    prompt = build_production_shot_prompt(scene)
                elif (
                    options.get('production_scheduled') is True
                    and fresh_scheduled_shot_prompts and scene.get('ai_prompt') is not None
                ):
                    # The entire authored direction was preflighted before
                    # TTS; later stock reviews must not replace its action.
                    from app.services.production_shot_prompt import build_production_shot_prompt
                    prompt = build_production_shot_prompt(scene)
                else:
                    prompt = (
                        _runway_prompt_for_scene(
                            scene,
                            candidate_review,
                            generation_aspect_ratio,
                        )
                        if (
                            not is_bounded_short_preview
                            or str(scene.get('ai_prompt') or '').strip()
                            or candidate_scene_idx in provider_outage_stock_scenes
                            or candidate_scene_idx in stock_quality_fallback_scenes
                            or candidate_scene_idx in recovery_paid_scene_indices
                        )
                        else ''
                    )
                if not prompt:
                    continue
                prompts[candidate_scene_idx] = prompt
                if candidate_review and scene_visuals[candidate_scene_idx]:
                    _apply_visual_review(scene_visuals, candidate_scene_idx, candidate_review)
                has_visual = any(_visual_path(spec) for spec in scene_visuals[candidate_scene_idx])
                stock_score = int((candidate_review or {}).get('score', -1))
                if should_rank_runway_candidate(
                    options,
                    duration_minutes,
                    authored_ai_prompt=bool(
                        str(scene.get('ai_prompt') or '').strip()
                    ),
                    has_visual=has_visual,
                    stock_score=stock_score,
                    quality_threshold=quality_threshold,
                ):
                    ranked.append({
                        'scene_index': candidate_scene_idx,
                        'has_visual': has_visual,
                        'stock_score': stock_score,
                        'provider_outage_stock_fallback': (
                            candidate_scene_idx in provider_outage_stock_scenes
                        ),
                        'stock_quality_fallback': (
                            candidate_scene_idx in stock_quality_fallback_scenes
                        ),
                    })
            ranked.sort(key=lambda item: (
                0 if not item['has_visual'] else 1,
                item['stock_score'],
                item['scene_index'],
            ))
            return prompts, ranked

        prompt_candidates, ranked_runway_candidates = rank_runway_candidates()

        # Give sourced documentary stock scenes one bounded rescue before paid
        # generation, even when every rejected scene fits the frozen budget.
        # The selector keeps overflow-only behavior outside that narrow scope;
        # an empty selection performs no stock access or additional review.
        # All normal allocation, recovery and visual QA gates remain unchanged.
        if (
            (
                is_bounded_short_preview
                or (
                    options.get('mode') == 'production'
                    and options.get('format') == 'shorts'
                    and duration_minutes == 0.5
                    and type(total_paid_create_cap) is int
                    and total_paid_create_cap > 0
                )
            )
            and not scene_repair_recovery
            and not provider_outage_stock_scenes
            and not stock_quality_fallback_scenes
        ):
            overflow_candidates = _prepaid_stock_rescue_candidates(
                ranked_runway_candidates,
                (
                    total_paid_create_cap
                    if options.get('mode') == 'production'
                    else runway_submission_cap
                ),
                options=options,
                duration_minutes=duration_minutes,
                scenes=scenes,
                evidence_sources=package.get('sources') or [],
                quality_threshold=quality_threshold,
            )
            budget_rescued_scenes: list[int] = []
            for candidate in overflow_candidates:
                scene_idx = int(candidate['scene_index'])
                review = current_reviews.get(scene_idx) or {}
                retry_queries = _prepaid_stock_rescue_queries(
                    scenes[scene_idx], review,
                )
                replacements = _retry_bad_scene(
                    scene_idx,
                    retry_queries,
                    seen_ids,
                    work,
                    credits,
                    file_prefix='pre_runway_budget_rescue',
                    max_replacements=2,
                    minimum_duration=max(
                        5.0,
                        float(scene_durations[scene_idx]) + 0.35,
                    ),
                    allow_short_fallback=False,
                    orientation=pexels_orientation,
                    active_scene_visuals=stock_reuse_visuals,
                )
                if not replacements:
                    continue
                existing_specs = list(scene_visuals[scene_idx])
                scene_visuals[scene_idx] = [*replacements, *existing_specs][:3]
                budget_rescued_scenes.append(scene_idx)
                visual_replacements.append({
                    'scene_index': scene_idx,
                    'score': int(review.get('score', 0)),
                    'reason': review.get('reason'),
                    'old_best': _visual_path(existing_specs[0]) if existing_specs else '',
                    'replacement_count': len(replacements),
                    'stage': 'pre_runway_budget_rescue',
                })

            if budget_rescued_scenes:
                # This is a third, distinct reviewer input. Keep its exact
                # clips across retries just like the first two pools; an
                # unknown review must not silently receive different frames.
                if not recovered_generated_media and selected_recovery is None:
                    from app.services.included_stock_pool import retain_stock_pool
                    retain_stock_pool(task_id, package, scene_visuals, credits, seen_ids,
                                      work, phase='budget_rescue')
                set_stage(
                    self,
                    task_id,
                    'pre_runway_budget_rescue',
                    62,
                    'Ücretli üretimden önce uygun stok sahneleri daha kesin aramalarla yeniden aranıyor.',
                )
                budget_rescue_qc = review_scene_visuals(
                    [scenes[idx] for idx in budget_rescued_scenes],
                    [scene_visuals[idx] for idx in budget_rescued_scenes],
                    work / 'pre_runway_budget_rescue',
                    len(budget_rescued_scenes),
                    topic=topic,
                    story_scenes=scenes,
                    content_style=options.get('content_style', ''),
                    evidence_sources=package.get('sources') or [],
                )
                budget_reviews = {
                    int(review.get('scene_index')): review
                    for review in (budget_rescue_qc.get('reviews') or [])
                    if isinstance(review, dict)
                    and str(review.get('scene_index', '')).lstrip('-').isdigit()
                }
                missing_budget_positions = [
                    position
                    for position in range(len(budget_rescued_scenes))
                    if position not in budget_reviews
                ]
                if missing_budget_positions:
                    raise PreRunwayRetryableError(
                        'Pre-Runway stock rescue QC was incomplete before any paid submission: '
                        + json.dumps({'missing_positions': missing_budget_positions}, separators=(',', ':'))
                    )
                for position, scene_idx in enumerate(budget_rescued_scenes):
                    rescued_review = budget_reviews.get(position)
                    if not rescued_review:
                        continue
                    mapped_review = dict(rescued_review)
                    mapped_review['scene_index'] = scene_idx
                    current_reviews[scene_idx] = mapped_review
                    if scene_visuals[scene_idx]:
                        _apply_visual_review(scene_visuals, scene_idx, mapped_review, default_fraction=0.35)
                prompt_candidates, ranked_runway_candidates = rank_runway_candidates()

        _record_prepaid_visual_diagnostics(
            task_id,
            scene_visuals,
            current_reviews,
            ranked_runway_candidates,
            total_paid_create_cap,
            paid_slots_used=runway_attempts,
            quality_threshold=quality_threshold,
        )
        _checkpoint_overbudget_visuals(
            task_id, scenes, scene_visuals, current_reviews,
            ranked_runway_candidates, work, total_paid_create_cap,
            paid_slots_used=runway_attempts,
            quality_threshold=quality_threshold,
        )
        runway_required_submission_cap = runway_submission_cap
        runway_effective_submission_cap = runway_submission_cap
        selected_runway: list[dict]
        if scene_repair_recovery:
            missing_recovery_prompts = sorted(
                recovery_paid_scene_indices - set(prompt_candidates)
            )
            if missing_recovery_prompts:
                raise FinalVisualQualityError(
                    'Scene-repair recovery is missing a safe generation '
                    'prompt: '
                    + ','.join(
                        str(index) for index in missing_recovery_prompts
                    )
                )
            selected_runway = [
                {
                    'scene_index': scene_idx,
                    'has_visual': any(
                        _visual_path(spec)
                        for spec in scene_visuals[scene_idx]
                    ),
                    'stock_score': int(
                        (current_reviews.get(scene_idx) or {}).get(
                            'score',
                            -1,
                        )
                    ),
                    'provider_outage_stock_fallback': False,
                    'stock_quality_fallback': False,
                }
                for scene_idx in sorted(recovery_paid_scene_indices)
            ]
            runway_required_submission_cap = len(selected_runway)
            runway_effective_submission_cap = len(selected_runway)
        elif (
            options.get('mode') == 'production'
            and options.get('format') == 'shorts'
            and duration_minutes == 0.5
            and recovered_generated_media
            and recovered_generated_media.get('version') == 3
            and recovered_generated_media.get('recovery_only') is True
        ):
            # This validated recovery-only contract selects already-paid
            # assets, not new generation. Fresh stock rankings must neither
            # drop a recovered scene nor add another paid scene. Every scene
            # still goes through the normal exact final visual gate/rescue.
            selected_runway = [
                {
                    'scene_index': scene_idx,
                    'has_visual': any(
                        _visual_path(spec)
                        for spec in scene_visuals[scene_idx]
                    ),
                    'stock_score': int(
                        (current_reviews.get(scene_idx) or {}).get('score', -1)
                    ),
                    'provider_outage_stock_fallback': False,
                    'stock_quality_fallback': False,
                }
                for scene_idx in sorted(recovered_generated_media['scenes'])
            ]
            runway_required_submission_cap = len(selected_runway)
            runway_effective_submission_cap = len(selected_runway)
        elif is_bounded_short_preview:
            (
                selected_runway,
                required_base_candidates,
                missing_outage_scenes,
                missing_quality_scenes,
                outage_cap_exceeded,
                quality_cap_exceeded,
                overlapping_forced_scenes,
            ) = _allocate_short_preview_forced_stock_runway(
                ranked_runway_candidates,
                runway_submission_cap,
                provider_outage_stock_scenes,
                stock_quality_fallback_scenes,
                quality_threshold,
            )
            runway_required_submission_cap = (
                _short_preview_required_submission_cap(
                    runway_submission_cap,
                    len(required_base_candidates),
                )
            )
            runway_effective_submission_cap = min(
                len(scenes),
                runway_required_submission_cap
                + min(
                    len(provider_outage_stock_scenes),
                    SHORT_PREVIEW_PROVIDER_OUTAGE_RUNWAY_CAP,
                )
                + min(
                    len(stock_quality_fallback_scenes),
                    SHORT_PREVIEW_STOCK_QUALITY_RUNWAY_CAP,
                ),
            )
            allocation_is_incomplete = (
                len(required_base_candidates)
                > runway_required_submission_cap
                or bool(missing_outage_scenes)
                or bool(missing_quality_scenes)
                or outage_cap_exceeded
                or quality_cap_exceeded
                or bool(overlapping_forced_scenes)
                or len(selected_runway) > runway_effective_submission_cap
            )
            if allocation_is_incomplete:
                preflight_message = (
                    'Short-preview media allocation cannot produce a '
                    'complete video within its bounded paid allocation: '
                    + json.dumps(
                        {
                            'base_submission_cap': runway_submission_cap,
                            'required_submission_cap': (
                                runway_required_submission_cap
                            ),
                            'required_scene_completion_cap': (
                                SHORT_PREVIEW_REQUIRED_RUNWAY_CAP
                            ),
                            'effective_submission_cap': runway_effective_submission_cap,
                            'provider_outage_emergency_cap': (
                                SHORT_PREVIEW_PROVIDER_OUTAGE_RUNWAY_CAP
                            ),
                            'stock_quality_emergency_cap': (
                                SHORT_PREVIEW_STOCK_QUALITY_RUNWAY_CAP
                            ),
                            'provider_outage_stock_scenes': sorted(
                                provider_outage_stock_scenes
                            ),
                            'stock_quality_fallback_scenes': sorted(
                                stock_quality_fallback_scenes
                            ),
                            'missing_outage_scenes': missing_outage_scenes,
                            'missing_quality_scenes': missing_quality_scenes,
                            'overlapping_forced_scenes': (
                                overlapping_forced_scenes
                            ),
                            'required_non_forced_scenes': [
                                int(item['scene_index'])
                                for item in required_base_candidates
                            ],
                        },
                        separators=(',', ':'),
                    )
                )
                raise FinalVisualQualityError(preflight_message)
        elif (
            options.get('mode') == 'production'
            and options.get('format') == 'shorts'
            and total_paid_create_cap is not None
        ):
            # Production ranks only absent/rejected visuals. Validate all
            # required replacements before truncating or buying any of them.
            _validate_paid_create_allocation(
                ranked_runway_candidates,
                None,
                total_paid_create_cap,
                paid_slots_used=runway_attempts,
            )
            selected_runway = list(ranked_runway_candidates)
            runway_required_submission_cap = len(selected_runway)
            runway_effective_submission_cap = len(selected_runway)
        else:
            selected_runway = ranked_runway_candidates[:runway_submission_cap]
        if is_private_ai_first_omni_preview:
            # Omni continuity is causal: the first accepted generated clip is
            # the sole visual anchor for later explicitly related AI scenes.
            selected_runway = sorted(
                selected_runway,
                key=lambda item: int(item['scene_index']),
            )
        _validate_paid_create_allocation(
            selected_runway,
            recovered_generated_media,
            total_paid_create_cap,
            paid_slots_used=runway_attempts,
        )
        selected_runway_indices = {item['scene_index'] for item in selected_runway}
        runway_rank = {
            item['scene_index']: position + 1
            for position, item in enumerate(ranked_runway_candidates)
        }
        runway_allocation = []
        for scene_idx in sorted(prompt_candidates):
            current_review = current_reviews.get(scene_idx) or {}
            has_visual = any(_visual_path(spec) for spec in scene_visuals[scene_idx])
            stock_score = int(current_review.get('score', -1))
            selected = scene_idx in selected_runway_indices
            runway_allocation.append({
                'scene_index': scene_idx,
                'stock_score': stock_score,
                'has_visual': has_visual,
                'rank': runway_rank.get(scene_idx),
                'selected': selected,
                'reason': (
                    'provider_outage_stock_fallback'
                    if selected and scene_idx in provider_outage_stock_scenes
                    else 'stock_quality_fallback'
                    if selected and scene_idx in stock_quality_fallback_scenes
                    else 'selected_for_generation' if selected
                    else 'stock_approved' if has_visual and stock_score >= quality_threshold
                    else 'submission_cap'
                ),
            })

        _preflight_runway_candidates_before_paid(
            [int(item['scene_index']) for item in selected_runway],
            scene_durations,
            approved_package,
        )

        _require_recovered_media_coverage(
            recovered_generated_media,
            [int(item['scene_index']) for item in selected_runway],
        )
        video_scene_budget = prepare_video_scene_budget(
            _recovery_package_sha256(package), scene_durations, generation_aspect_ratio,
            scene_count=len(scenes),
        )

        set_stage(
            self,
            task_id,
            (
                'ai_scene_repair'
                if scene_repair_recovery
                else 'ai_scene_recovery'
                if recovered_generated_media
                else 'ai_scene_generation'
            ),
            64,
            (
                'Kabul edilen üretimler geri yükleniyor; yalnızca reddedilen '
                'sahneler yeniden üretiliyor.'
                if scene_repair_recovery
                else 'Kayıtlı ücretli sahneler doğrulanarak geri yükleniyor.'
                if recovered_generated_media
                else 'Seçilen özgün sahneler sırayla üretiliyor.'
            ),
        )

        omni_continuity_reference_image_path: Path | None = None
        omni_continuity_anchor_scene_idx: int | None = None
        for candidate in selected_runway:
            scene_idx = int(candidate['scene_index'])
            if selected_recovery is not None:
                _selected_recovery_authorization(
                    task_id, retry_dispatch_source_id,
                    _task_spec(topic, duration_minutes, language, channel_id, options), approved_package,
                )
                if (scene_idx not in recovery_repair_scene_indices
                        or _recovery_package_sha256(package) != selected_recovery['manifest']['package_sha256']
                        or voice_result != selected_recovery['voice_result']):
                    raise FinalVisualQualityError('Selected recovery changed before a new scene request')
            stock_fallback = list(scene_visuals[scene_idx])
            if recovered_generated_media and recovered_generated_media.get('version') == 4:
                # V4 fixes an exact retained/repair partition. Preliminary
                # stock reviews may inform direction, never replace its assets.
                stock_fallback = []
                scene_visuals[scene_idx] = []
            curated_preloaded = bool(
                len(stock_fallback) == 1 and isinstance(stock_fallback[0], dict)
                and stock_fallback[0].get('curated_pinned') is True
                and stock_fallback[0].get('generation_recovered') is True
            )
            if curated_preloaded:
                stock_fallback = []
            if recovered_generated_media and recovered_generated_media.get('version') == 5:
                # Keep the preloaded generated identity only; neither the
                # repair slots nor the pinned real stock can be substituted.
                stock_fallback = []
                scene_visuals[scene_idx] = []
            recovered_scene_entries = (
                recovered_generated_media.get('scenes', {}).get(scene_idx)
                if recovered_generated_media
                else None
            )
            if recovered_scene_entries:
                recovered_specs: list[dict] = []
                for recovered_idx, raw_entry in enumerate(
                    recovered_scene_entries
                ):
                    if recovered_generated_media.get('version') in (2, 3, 4, 5):
                        entry = raw_entry
                    else:
                        entry = {
                            'key': raw_entry,
                            'provider': recovered_generated_media[
                                'provider'
                            ],
                            'provider_attempts': 1,
                            'synthetic_motion_only': False,
                            'motion_recipe_version': None,
                            'source_media_type': None,
                            'size': None,
                            'sha256': None,
                        }
                    object_key = entry['key']
                    recovered_path = (
                        work
                        / f'recovered_s{scene_idx:02d}_{recovered_idx:02d}.mp4'
                    )
                    try:
                        if not curated_preloaded:
                            download_file(object_key, recovered_path)
                        _validate_recovered_generated_clip(
                            recovered_path,
                            minimum_duration=max(
                                5.0,
                                float(scene_durations[scene_idx]) + 0.35,
                            ),
                            expected_size=entry.get('size'),
                            expected_sha256=entry.get('sha256'),
                        )
                    except FinalVisualQualityError:
                        raise
                    except Exception as exc:
                        raise FinalVisualQualityError(
                            'Recovered generated-media clip download failed '
                            f'for scene {scene_idx}'
                        ) from exc
                    recovered_spec = _generated_visual_spec(
                        recovered_path,
                        provider=entry['provider'],
                        provider_attempts=entry['provider_attempts'],
                    )
                    recovered_spec.update({
                        'generation_recovered': True,
                        'recovered_from_task_id': (
                            recovered_generated_media['source_task_id']
                        ),
                    })
                    if curated_preloaded:
                        recovered_spec['curated_pinned'] = True
                    if entry.get('synthetic_motion_only') is True:
                        recovered_spec.update({
                            'synthetic_motion_only': True,
                            'motion_recipe_version': entry.get(
                                'motion_recipe_version'
                            ),
                            'source_media_type': 'image',
                        })
                    recovered_specs.append(recovered_spec)
                    generated_checkpoint_specs.setdefault(
                        scene_idx,
                        [],
                    ).append(dict(recovered_spec))
                if (
                    is_private_ai_first_omni_preview
                    and omni_continuity_reference_image_path is None
                    and recovered_specs
                    and _omni_continuity_reference_needed(
                        scene_idx,
                        scenes,
                        selected_runway_indices,
                    )
                ):
                    continuity_path = work / 'omni_continuity_reference.jpg'
                    create_gemini_omni_continuity_reference(
                        recovered_specs[0]['path'],
                        continuity_path,
                    )
                    omni_continuity_reference_image_path = continuity_path
                    omni_continuity_anchor_scene_idx = scene_idx
                scene_visuals[scene_idx] = [
                    *recovered_specs,
                    *stock_fallback,
                ][:3]
                runway_scenes_used += 1
                runway_generated_scenes.append(scene_idx)
                generated_video_provider_records.append({
                    'stage': 'recovered_generation',
                    'scene_index': scene_idx,
                    'provider': recovered_specs[0][
                        'generation_provider'
                    ],
                    'provider_attempts': recovered_specs[0][
                        'generation_provider_attempts'
                    ],
                    'recovered_from_task_id': (
                        recovered_generated_media['source_task_id']
                    ),
                    'recovered_candidate_count': len(recovered_specs),
                })
                continue

            if (
                recovered_generated_media
                and not (
                    scene_repair_recovery
                    and scene_idx in recovery_repair_scene_indices
                )
            ):
                raise FinalVisualQualityError(
                    'Recovered generated-media scene is unavailable'
                )

            runway_attempts = _reserve_paid_create_slot(
                runway_attempts,
                total_paid_create_cap,
                task_id=task_id,
            )
            try:
                generation_seconds = _runway_generation_seconds(
                    scene_durations[scene_idx]
                )
                scene_continuity_reference = None
                if (
                    omni_continuity_reference_image_path is not None
                    and omni_continuity_anchor_scene_idx is not None
                    and _omni_continuity_reference_applies(
                        scenes[omni_continuity_anchor_scene_idx],
                        scenes[scene_idx],
                    )
                ):
                    scene_continuity_reference = (
                        omni_continuity_reference_image_path
                    )
                with spending_scene(video_scene_budget, scene_idx):
                    generated_scene = generate_scene(
                        prompt_candidates[scene_idx],
                        duration=generation_seconds,
                        allow_image_motion=(
                            total_paid_create_cap is None
                            and is_private_image_motion_preview
                        ),
                        image_prompt=_image_motion_prompt_for_scene(
                            scenes[scene_idx],
                            current_reviews.get(scene_idx),
                            generation_aspect_ratio,
                        ),
                        prefer_gemini_omni=(
                            is_private_ai_first_omni_preview
                        ),
                        continuity_reference_image=(
                            scene_continuity_reference
                        ),
                        aspect_ratio=generation_aspect_ratio,
                        allow_paid_terminal_resubmit=(
                            total_paid_create_cap is None
                        ),
                    )
                if generated_scene.get('provider') == 'gemini_image_motion':
                    # Record the paid image submission before any local
                    # decode/render step. A local failure must not make the
                    # same scene eligible for a second image create.
                    image_motion_submission_scenes.add(scene_idx)
                if is_private_ai_first_omni_preview:
                    # A provider request is accepted before local copy and
                    # continuity-reference work. Reserve the scene until every
                    # local step succeeds so a failure cannot submit a second
                    # paid request through Omni or its bounded fallbacks.
                    omni_unsafe_submission_scenes.add(scene_idx)
                runway_path = work / f'runway_s{scene_idx:02d}.mp4'
                download_generated_scene(
                    generated_scene,
                    runway_path,
                )
                if (
                    is_private_ai_first_omni_preview
                    and omni_continuity_reference_image_path is None
                    and _omni_continuity_reference_needed(
                        scene_idx,
                        scenes,
                        selected_runway_indices,
                    )
                ):
                    continuity_path = work / 'omni_continuity_reference.jpg'
                    create_gemini_omni_continuity_reference(
                        runway_path,
                        continuity_path,
                    )
                    omni_continuity_reference_image_path = continuity_path
                    omni_continuity_anchor_scene_idx = scene_idx
                runway_spec = _generated_visual_spec(
                    runway_path,
                    provider=str(generated_scene['provider']),
                    provider_attempts=int(
                        generated_scene.get('provider_attempts') or 1
                    ),
                )
                if recovered_generated_media and recovered_generated_media.get('version') in (5, 6):
                    runway_spec['curated_pinned'] = True
                if generated_scene.get('synthetic_motion') is True:
                    runway_spec.update({
                        'synthetic_motion_only': True,
                        'motion_recipe_version': generated_scene.get(
                            'motion_recipe_version'
                        ),
                        'source_media_type': 'image',
                    })
                scene_visuals[scene_idx] = [runway_spec, *stock_fallback][:3]
                generated_checkpoint_specs.setdefault(
                    scene_idx,
                    [],
                ).append(dict(runway_spec))
                _checkpoint_generated_asset(
                    task_id, work, package, voice_result, runway_spec, scene_idx,
                    'initial_generation', options, duration_minutes, generated_asset_candidate_journal,
                )
                runway_scenes_used += 1
                runway_generated_scenes.append(scene_idx)
                generated_video_provider_records.append({
                    'stage': 'initial_generation',
                    'scene_index': scene_idx,
                    'provider': str(generated_scene['provider']),
                    'provider_attempts': int(
                        generated_scene.get('provider_attempts') or 1
                    ),
                    'quota_fallback_from': generated_scene.get(
                        'quota_fallback_from'
                    ),
                    'quota_fallback_chain': generated_scene.get(
                        'quota_fallback_chain'
                    ),
                    'fallback_reason': generated_scene.get('fallback_reason'),
                    'provider_fallback_from': generated_scene.get(
                        'provider_fallback_from'
                    ),
                    'provider_request_id': generated_scene.get(
                        'provider_request_id'
                    ),
                    'source_media_type': generated_scene.get(
                        'source_media_type'
                    ),
                    'synthetic_motion': generated_scene.get(
                        'synthetic_motion'
                    ),
                    'motion_recipe_version': generated_scene.get(
                        'motion_recipe_version'
                    ),
                    'image_model': generated_scene.get('image_model'),
                    'image_sha256': generated_scene.get('image_sha256'),
                    'prompt_sha256': generated_scene.get('prompt_sha256'),
                })
                omni_unsafe_submission_scenes.discard(scene_idx)
            except SpendBlocked:
                # A budget denial stops this worker before another scene,
                # critic or repair can make a paid request. Preserve its reason
                # for the outer terminal handler and retained checkpoints.
                raise
            except Exception as exc:
                if isinstance(exc, GeminiOmniContinuityReferenceError):
                    # A paid first clip exists but cannot safely anchor later
                    # related scenes. Stop instead of buying inconsistent
                    # follow-ups or silently discarding the valid clip.
                    raise
                if isinstance(exc, GeminiImageAttemptedError):
                    image_motion_submission_scenes.add(scene_idx)
                from app.services.commissioning_video import CommissionedVideoUnavailable
                if isinstance(exc, (GeminiOmniTerminalError, CommissionedVideoUnavailable)):
                    # A completed no-clip outcome may use stock but must not
                    # re-enter another paid generation for this scene.
                    omni_unsafe_submission_scenes.add(scene_idx)
                runway_failed_scenes.append(scene_idx)
                runway_failure_diagnostics.append(
                    _runway_failure_diagnostic(
                        'initial_generation',
                        scene_idx,
                        exc,
                    )
                )

        if is_bounded_short_preview:
            try:
                (
                    staged_recovered_scenes,
                    staged_voice_contract,
                ) = _stage_scene_repair_artifacts(
                    task_id=task_id,
                    package=package,
                    voice_result=voice_result,
                    scene_durations=scene_durations,
                    generated_checkpoint_specs=(
                        generated_checkpoint_specs
                    ),
                )
            except Exception:
                # The primary render remains fail-closed on quality. Storage
                # checkpointing is retried only if the critic rejects it.
                staged_recovered_scenes = None
                staged_voice_contract = None

        # Re-review the exact clips that will be rendered. Retry search results
        # and generated clips never bypass the final semantic quality gate.
        set_stage(self, task_id, 'final_visual_qc', 69, 'Seçilen final görüntüler anlatıyla son kez eşleştiriliyor.')
        final_review_visuals = scene_visuals
        if selected_recovery is not None:
            _selected_recovery_authorization(
                task_id, retry_dispatch_source_id,
                _task_spec(topic, duration_minutes, language, channel_id, options), approved_package,
            )
            final_visual_qc = _review_selected_exact(
                selected_recovery, scenes, work, topic, options, package.get('sources') or [],
                scene_visuals=scene_visuals,
            )
        elif curated_source_job is not None:
            try:
                from app.services.curated_stock_review import exact_review_visuals
                final_review_visuals = exact_review_visuals(
                    scenes=scenes, scene_visuals=scene_visuals,
                    scene_durations=scene_durations, voice_path=voice_path, work_dir=work,
                    **({'natural_short': True} if isinstance(curated_stock_manifest, dict)
                       and curated_stock_manifest.get('kind') == 'owner_plan_retained' else {}),
                )
            except Exception:
                raise FinalVisualQualityError('Curated exact-cut review inputs could not be verified') from None
        if selected_recovery is None:
            final_visual_qc = review_scene_visuals(
                scenes,
                final_review_visuals,
                work / 'final_visual_qc',
                len(scenes),
                topic=topic,
                story_scenes=scenes,
                content_style=options.get('content_style', ''),
                evidence_sources=package.get('sources') or [],
            )
        final_reviews = {
            int(r.get('scene_index')): r
            for r in (final_visual_qc.get('reviews') or [])
            if isinstance(r, dict) and str(r.get('scene_index', '')).lstrip('-').isdigit()
        }
        manual_qa_prepass_scenes = set(manual_qa_preview_scenes)
        manual_qa_prepass_scores = dict(manual_qa_preview_scores)
        manual_qa_prepass_identities = dict(manual_qa_preview_identities)
        manual_qa_preview_scenes.clear()
        manual_qa_preview_scores.clear()
        manual_qa_preview_records.clear()
        manual_qa_preview_identities.clear()
        manual_qa_preserve_exact_cut_scenes: set[int] = set()
        final_manual_reviews_applied: set[int] = set()
        terminal_manual_qa_candidates: list[tuple[int, dict, dict]] = []
        for scene_idx in sorted(manual_qa_prepass_scenes):
            review = final_reviews.get(scene_idx) or {}
            score = int(review.get('score', -1))
            if score >= quality_threshold:
                continue
            selected_spec = _reviewed_visual_spec(
                scene_visuals[scene_idx],
                review,
            )
            if _manual_qa_preview_passes(
                options,
                duration_minutes,
                scenes[scene_idx],
                review,
                selected_spec,
            ):
                _apply_visual_review(scene_visuals, scene_idx, review)
                review = dict(review)
                review['best_candidate_index'] = 0
                final_reviews[scene_idx] = review
                register_manual_qa_preview(
                    scene_idx,
                    review,
                    (
                        scene_visuals[scene_idx][0]
                        if scene_visuals[scene_idx]
                        else None
                    ),
                )
                final_manual_reviews_applied.add(scene_idx)
                continue
            terminal_manual_qa_candidates.append(
                (scene_idx, selected_spec, review)
            )

        # Resolve only a bounded 1-1 critic disagreement: the exact same clip
        # and cut already passed preflight, the final critic still sees both
        # the named subject and action, and no hard artifact/reset veto exists.
        # A blind third review must independently clear every unchanged gate.
        adjudication_reviews: dict[int, dict] = {}
        eligible_adjudication_candidates = []
        if len(terminal_manual_qa_candidates) <= 2:
            eligible_adjudication_candidates = [
                (scene_idx, selected_spec, final_review)
                for scene_idx, selected_spec, final_review
                in terminal_manual_qa_candidates
                if (
                    _manual_qa_visual_identity(selected_spec)
                    == manual_qa_prepass_identities.get(scene_idx)
                    and final_review.get('subject_visible') is True
                    and final_review.get('spoken_action_visible') is True
                    and final_review.get('identity_gate_passed') is True
                    and final_review.get('unexplained_reset') is False
                    and all(
                        final_review.get(field) is False
                        for field in _MANUAL_QA_CLEAR_VISUAL_FIELDS
                    )
                )
            ]
        if eligible_adjudication_candidates:
            adjudication_qc = review_scene_visuals(
                [
                    scenes[scene_idx]
                    for scene_idx, _, _ in eligible_adjudication_candidates
                ],
                [
                    [selected_spec]
                    for _, selected_spec, _ in eligible_adjudication_candidates
                ],
                work / 'manual_qa_final_adjudication',
                len(eligible_adjudication_candidates),
                _missing_review_attempts=0,
                topic=topic,
                story_scenes=scenes,
                content_style=options.get('content_style', ''),
                evidence_sources=package.get('sources') or [],
            )
            for local_review in adjudication_qc.get('reviews') or []:
                if not isinstance(local_review, dict):
                    continue
                local_index = local_review.get('scene_index')
                if (
                    type(local_index) is int
                    and 0 <= local_index < len(eligible_adjudication_candidates)
                ):
                    scene_idx = eligible_adjudication_candidates[local_index][0]
                    mapped = dict(local_review)
                    mapped['scene_index'] = scene_idx
                    adjudication_reviews[scene_idx] = mapped

        remaining_manual_qa_candidates: list[tuple[int, dict, dict]] = []
        for scene_idx, selected_spec, final_review in (
            terminal_manual_qa_candidates
        ):
            adjudication_review = adjudication_reviews.get(scene_idx) or {}
            adjudication_score = int(adjudication_review.get('score', -1))
            adjudication_passes = (
                _manual_qa_review_matches_locked_cut(
                    adjudication_review,
                    selected_spec,
                )
                and (
                    adjudication_score >= quality_threshold
                    or _manual_qa_preview_passes(
                        options,
                        duration_minutes,
                        scenes[scene_idx],
                        adjudication_review,
                        selected_spec,
                    )
                )
            )
            if adjudication_passes:
                accepted_review = dict(adjudication_review)
                accepted_review['score'] = min(
                    int(manual_qa_prepass_scores[scene_idx]),
                    adjudication_score,
                )
                if accepted_review['score'] >= quality_threshold:
                    continue
                register_manual_qa_preview(
                    scene_idx,
                    accepted_review,
                    selected_spec,
                )
                manual_qa_preserve_exact_cut_scenes.add(scene_idx)
                continue
            remaining_manual_qa_candidates.append(
                (scene_idx, selected_spec, final_review)
            )
        terminal_manual_qa_candidates = remaining_manual_qa_candidates

        terminal_manual_qa_failures = [
            _manual_qa_failure_diagnostic(
                scene_idx,
                adjudication_reviews.get(scene_idx) or final_review,
                selected_spec,
            )
            for scene_idx, selected_spec, final_review
            in terminal_manual_qa_candidates
        ]
        terminal_manual_qa_failure_scene_indices: set[int] = set()
        unroutable_terminal_manual_qa_failures: list[dict] = []
        for candidate, diagnostic in zip(
            terminal_manual_qa_candidates,
            terminal_manual_qa_failures,
        ):
            scene_idx, selected_spec, _final_review = candidate
            if _manual_qa_visual_source_type(selected_spec) == 'stock':
                terminal_manual_qa_failure_scene_indices.add(scene_idx)
            else:
                unroutable_terminal_manual_qa_failures.append(diagnostic)
        if unroutable_terminal_manual_qa_failures:
            raise FinalVisualQualityError(
                'Manual-QA preview failed exact final revalidation: '
                + json.dumps(
                    {'failures': unroutable_terminal_manual_qa_failures},
                    ensure_ascii=False,
                    separators=(',', ':'),
                )
            )
        # Generated clips do not exist during the stock prepass. Admit them
        # only after the exact final clip has supplied all hard-evidence and
        # artifact booleans. Forced stock fallbacks use the generated floor;
        # a recovered, positively-proven Pexels clip uses the stock floor.
        final_manual_candidates = [
            scene_idx
            for scene_idx, review in final_reviews.items()
            if (
                scene_idx not in manual_qa_prepass_scenes
                and int(review.get('score', 0)) < quality_threshold
            )
        ]
        accepted_final_manual, _rejected_final_manual = (
            _manual_qa_preview_decisions(
                options,
                duration_minutes,
                scenes,
                final_reviews,
                scene_visuals,
                final_manual_candidates,
            )
        )
        for scene_idx in accepted_final_manual:
            review = dict(final_reviews[scene_idx])
            _apply_visual_review(scene_visuals, scene_idx, review)
            review['best_candidate_index'] = 0
            final_reviews[scene_idx] = review
            register_manual_qa_preview(
                scene_idx,
                review,
                (
                    scene_visuals[scene_idx][0]
                    if scene_visuals[scene_idx]
                    else None
                ),
            )
            final_manual_reviews_applied.add(scene_idx)
        rejected_final_scenes = [
            idx for idx in range(min(len(scenes), len(scene_visuals)))
            if (
                idx not in final_reviews
                or (selected_recovery is not None
                    and idx in final_visual_qc['selected_recovery_rejected_indices'])
                or (
                    int(final_reviews[idx].get('score', 0)) < quality_threshold
                    and idx not in manual_qa_preview_scenes
                )
            )
        ]
        for scene_idx, review in final_reviews.items():
            if (
                selected_recovery is None
                and scene_idx not in rejected_final_scenes
                and scene_idx not in manual_qa_preserve_exact_cut_scenes
                and scene_idx not in final_manual_reviews_applied
            ):
                _apply_visual_review(scene_visuals, scene_idx, review)

        # Quarantine every exact-stock failure before repair selection. The
        # repair cap may select only a subset (or none in recovery mode), but
        # no rejected incumbent may survive into the later fresh-stock rescue.
        terminal_manual_qa_old_best: dict[int, str] = {}
        for scene_idx in sorted(terminal_manual_qa_failure_scene_indices):
            incumbent_specs = list(scene_visuals[scene_idx])
            terminal_manual_qa_old_best[scene_idx] = (
                _visual_path(incumbent_specs[0])
                if incumbent_specs
                else ''
            )
            scene_visuals[scene_idx] = []

        # A final critic has now seen the exact generated clips. This pass may
        # repair one production Short scene or at most two authored preview
        # scenes, always inside the same remaining durable total budget.
        final_runway_repair_candidates = (
            []
            if recovered_generated_media
            else preview_runway_repair_indices(
                options,
                duration_minutes,
                rejected_final_scenes,
                scenes,
                runway_generated_scenes,
                final_reviews,
                exact_revalidation_scene_indices=(
                    terminal_manual_qa_failure_scene_indices
                ),
                paid_create_attempts=runway_attempts,
                paid_create_cap=total_paid_create_cap,
            )
        )
        if not recovered_generated_media:
            commissioning_repairs = completion_repairs(
                options, duration_minutes, scenes, rejected_final_scenes, final_reviews,
                total_paid_create_cap, runway_attempts,
            )
            if commissioning_repairs is not None:
                final_runway_repair_candidates = commissioning_repairs
        final_runway_repair_candidates = [
            scene_idx
            for scene_idx in final_runway_repair_candidates
            if scene_idx not in omni_unsafe_submission_scenes
        ]
        if is_private_ai_first_omni_preview:
            final_runway_repair_candidates = sorted(
                final_runway_repair_candidates
            )
        final_runway_repair_candidate_indices = set(
            final_runway_repair_candidates
        )
        # Keep accepted footage and voice while correcting only rejected scenes.
        # Every extra round needs active commissioning authority and remaining
        # durable create slots; unknown provider outcomes still stop immediately.
        for repair_round in range(1, 7):
            round_repair_scenes: list[int] = []
            final_runway_repair_candidate_indices = set(final_runway_repair_candidates)
            _preflight_runway_candidates_before_paid(
                [int(index) for index in final_runway_repair_candidates],
                scene_durations,
                approved_package,
            )
            if final_runway_repair_candidates:
                set_stage(
                    self,
                    task_id,
                    'final_visual_qc_ai_repair',
                    71,
                    'Reddedilen özgün sahnelerde hareket kanıtı hedefli olarak yenileniyor.',
            )
            for scene_idx in final_runway_repair_candidates:
                review = final_reviews.get(scene_idx) or {}
                existing_specs = list(scene_visuals[scene_idx])
                old_best = (
                    terminal_manual_qa_old_best.get(scene_idx)
                    or (_visual_path(existing_specs[0]) if existing_specs else '')
                )
                repair_prompt = _runway_prompt_for_scene(
                    scenes[scene_idx],
                    review,
                    generation_aspect_ratio,
                )
                if not repair_prompt:
                    continue
                if repair_round > 1:
                    repair_prompt += (f'\nFresh correction candidate, revision {repair_round}. '
                        'Preserve the required action and remove the defects identified above.')
                final_runway_repair_attempts += 1
                runway_attempts = _reserve_paid_create_slot(
                    runway_attempts,
                    total_paid_create_cap,
                    task_id=task_id,
                )
                try:
                    generation_seconds = _runway_generation_seconds(
                        scene_durations[scene_idx]
                    )
                    scene_continuity_reference = None
                    if (
                        omni_continuity_reference_image_path is not None
                        and omni_continuity_anchor_scene_idx is not None
                        and _omni_continuity_reference_applies(
                            scenes[omni_continuity_anchor_scene_idx],
                            scenes[scene_idx],
                        )
                    ):
                        scene_continuity_reference = (
                            omni_continuity_reference_image_path
                        )
                    with spending_scene(video_scene_budget, scene_idx):
                        repair_scene = generate_scene(
                            repair_prompt,
                            duration=generation_seconds,
                            allow_image_motion=(
                                total_paid_create_cap is None
                                and is_private_image_motion_preview
                                and scene_idx not in image_motion_submission_scenes
                            ),
                            image_prompt=_image_motion_prompt_for_scene(
                                scenes[scene_idx],
                                review,
                                generation_aspect_ratio,
                            ),
                            prefer_gemini_omni=(
                                is_private_ai_first_omni_preview
                            ),
                            continuity_reference_image=(
                                scene_continuity_reference
                            ),
                            aspect_ratio=generation_aspect_ratio,
                            allow_paid_terminal_resubmit=(
                                total_paid_create_cap is None
                            ),
                        )
                    if repair_scene.get('provider') == 'gemini_image_motion':
                        image_motion_submission_scenes.add(scene_idx)
                    if is_private_ai_first_omni_preview:
                        omni_unsafe_submission_scenes.add(scene_idx)
                    repair_path = work / (
                        f'runway_repair_s{scene_idx:02d}.mp4' if repair_round == 1
                        else f'runway_repair_r{repair_round}_s{scene_idx:02d}.mp4'
                    )
                    download_generated_scene(
                        repair_scene,
                        repair_path,
                    )
                    if (
                        is_private_ai_first_omni_preview
                        and omni_continuity_reference_image_path is None
                        and _omni_continuity_reference_needed(
                            scene_idx,
                            scenes,
                            final_runway_repair_candidate_indices,
                        )
                    ):
                        continuity_path = work / 'omni_continuity_reference.jpg'
                        create_gemini_omni_continuity_reference(
                            repair_path,
                            continuity_path,
                        )
                        omni_continuity_reference_image_path = continuity_path
                        omni_continuity_anchor_scene_idx = scene_idx
                    repair_spec = _generated_visual_spec(
                        repair_path,
                        provider=str(repair_scene['provider']),
                        provider_attempts=int(
                            repair_scene.get('provider_attempts') or 1
                        ),
                    )
                    if repair_scene.get('synthetic_motion') is True:
                        repair_spec.update({
                            'synthetic_motion_only': True,
                            'motion_recipe_version': repair_scene.get(
                                'motion_recipe_version'
                            ),
                            'source_media_type': 'image',
                        })
                    scene_visuals[scene_idx] = (
                        [repair_spec, *existing_specs][:3] if repair_round == 1 else [repair_spec]
                    )
                    generated_checkpoint_specs.setdefault(
                        scene_idx,
                        [],
                    ).append(dict(repair_spec))
                    _checkpoint_generated_asset(
                        task_id, work, package, voice_result, repair_spec, scene_idx,
                        'final_repair', options, duration_minutes, generated_asset_candidate_journal,
                    )
                    round_repair_scenes.append(scene_idx)
                    if scene_idx not in final_runway_repair_scenes:
                        final_runway_repair_scenes.append(scene_idx)
                    generated_video_provider_records.append({
                        'stage': 'final_repair',
                        'scene_index': scene_idx,
                        'provider': str(repair_scene['provider']),
                        'provider_attempts': int(
                            repair_scene.get('provider_attempts') or 1
                        ),
                        'quota_fallback_from': repair_scene.get(
                            'quota_fallback_from'
                        ),
                        'quota_fallback_chain': repair_scene.get(
                            'quota_fallback_chain'
                        ),
                        'provider_fallback_from': repair_scene.get(
                            'provider_fallback_from'
                        ),
                        'provider_request_id': repair_scene.get(
                            'provider_request_id'
                        ),
                        'fallback_reason': repair_scene.get('fallback_reason'),
                        'source_media_type': repair_scene.get(
                            'source_media_type'
                        ),
                        'synthetic_motion': repair_scene.get('synthetic_motion'),
                        'motion_recipe_version': repair_scene.get(
                            'motion_recipe_version'
                        ),
                        'image_model': repair_scene.get('image_model'),
                        'image_sha256': repair_scene.get('image_sha256'),
                        'prompt_sha256': repair_scene.get('prompt_sha256'),
                    })
                    omni_unsafe_submission_scenes.discard(scene_idx)
                    visual_replacements.append({
                        'scene_index': scene_idx,
                        'score': int(review.get('score', 0)),
                        'old_best': old_best,
                        'replacement_count': 1,
                        'stage': 'final_visual_qc_ai_repair',
                    })
                except SpendBlocked:
                    raise
                except Exception as exc:
                    if isinstance(exc, GeminiOmniContinuityReferenceError):
                        raise
                    if isinstance(exc, GeminiImageAttemptedError):
                        image_motion_submission_scenes.add(scene_idx)
                    from app.services.commissioning_video import CommissionedVideoUnavailable
                    if isinstance(exc, (GeminiOmniTerminalError, CommissionedVideoUnavailable)):
                        omni_unsafe_submission_scenes.add(scene_idx)
                    final_runway_repair_failures.append(scene_idx)
                    runway_failure_diagnostics.append(
                        _runway_failure_diagnostic(
                            'final_repair',
                            scene_idx,
                            exc,
                        )
                    )

            # Give every still-rejected clip one bounded free stock rescue. AI
            # scenes already changed above go straight back to exact-clip QC.
            rescued_final_scenes: list[int] = list(round_repair_scenes)
            for scene_idx in rejected_final_scenes:
                if recovered_generated_media and recovered_generated_media.get('version') in (4, 5, 6):
                    # No stock substitution or undeclared second generation after
                    # a failed exact V4 clip; retain the normal final-QA failure.
                    continue
                if scene_idx in round_repair_scenes:
                    continue
                review = final_reviews.get(scene_idx) or {}
                retry_queries = _final_pexels_rescue_queries(
                    scenes[scene_idx],
                    review,
                    forced_stock_fallback=(
                        scene_idx in provider_outage_stock_scenes
                        or scene_idx in stock_quality_fallback_scenes
                    ),
                )
                old_best = (
                    terminal_manual_qa_old_best.get(scene_idx)
                    or (
                        _visual_path(scene_visuals[scene_idx][0])
                        if scene_visuals[scene_idx]
                        else ''
                    )
                )
                try:
                    replacements = _retry_bad_scene(
                        scene_idx, retry_queries, seen_ids, work, credits,
                        file_prefix='final_qc_rescue',
                        minimum_duration=max(
                            5.0,
                            float(scene_durations[scene_idx]) + 0.35,
                        ),
                        allow_short_fallback=not is_bounded_short_preview,
                        tolerate_pexels_failure=(
                            scene_idx in provider_outage_stock_scenes
                            or scene_idx in stock_quality_fallback_scenes
                        ),
                        orientation=pexels_orientation,
                        active_scene_visuals=stock_reuse_visuals,
                    )
                except httpx.HTTPStatusError as exc:
                    # This is optional rescue, not evidence that a rejected clip
                    # passed. Stop this run's stock searches without retrying an
                    # access failure; preserve the real reviews and reach the
                    # ordinary rejection/checkpoint/workprint branch below.
                    try:
                        update_job(task_id, final_stock_rescue_unavailable={
                            'provider': 'pexels',
                            'scene_index': scene_idx,
                            'status': 'unavailable',
                            'http_status': exc.response.status_code,
                        })
                    except Exception:
                        pass  # Diagnostic storage cannot erase the QA outcome.
                    break
                if not replacements:
                    continue
                existing_specs = list(scene_visuals[scene_idx])
                if scene_idx in runway_generated_scenes and existing_specs:
                    scene_visuals[scene_idx] = [existing_specs[0], *replacements, *existing_specs[1:]][:3]
                else:
                    scene_visuals[scene_idx] = [*replacements, *existing_specs][:3]
                rescued_final_scenes.append(scene_idx)
                visual_replacements.append({
                    'scene_index': scene_idx,
                    'score': int(review.get('score', 0)),
                    'reason': review.get('reason'),
                    'old_best': old_best,
                    'replacement_count': len(replacements),
                    'stage': 'final_visual_qc_rescue',
                })

            if rescued_final_scenes:
                set_stage(self, task_id, 'final_visual_qc_rescue', 74, 'Reddedilen sahneler daha kesin görüntülerle son kez denetleniyor.')
                rescue_qc = review_scene_visuals(
                    [scenes[idx] for idx in rescued_final_scenes],
                    [scene_visuals[idx] for idx in rescued_final_scenes],
                    (work / 'final_visual_qc_rescue' if repair_round == 1
                     else work / f'final_visual_qc_rescue_r{repair_round}'),
                    len(rescued_final_scenes),
                    topic=topic,
                    story_scenes=scenes,
                    content_style=options.get('content_style', ''),
                    evidence_sources=package.get('sources') or [],
                )
                rescue_reviews = {
                    int(r.get('scene_index')): r
                    for r in (rescue_qc.get('reviews') or [])
                    if isinstance(r, dict) and str(r.get('scene_index', '')).lstrip('-').isdigit()
                }
                # Preserve the already-approved decisions; only replace the review
                # for each clip that actually changed during the rescue.
                for scene_idx in rescued_final_scenes:
                    final_reviews.pop(scene_idx, None)
                for position, scene_idx in enumerate(rescued_final_scenes):
                    if position not in rescue_reviews:
                        continue
                    mapped_review = dict(rescue_reviews[position])
                    mapped_review['scene_index'] = scene_idx
                    final_reviews[scene_idx] = mapped_review
                    selected_spec = _reviewed_visual_spec(
                        scene_visuals[scene_idx],
                        mapped_review,
                    )
                    rescued_manual_qa_pass = _manual_qa_preview_passes(
                        options,
                        duration_minutes,
                        scenes[scene_idx],
                        mapped_review,
                        selected_spec,
                    )
                    if (
                        int(mapped_review.get('score', 0)) >= quality_threshold
                        or rescued_manual_qa_pass
                    ):
                        _apply_visual_review(scene_visuals, scene_idx, mapped_review, default_fraction=0.35)
                        mapped_review = dict(mapped_review)
                        mapped_review['best_candidate_index'] = 0
                        final_reviews[scene_idx] = mapped_review
                        selected_spec = (
                            scene_visuals[scene_idx][0]
                            if scene_visuals[scene_idx]
                            else None
                        )
                    if rescued_manual_qa_pass:
                        register_manual_qa_preview(
                            scene_idx,
                            mapped_review,
                            selected_spec,
                        )
                final_visual_qc = {
                    'reviews': [final_reviews[idx] for idx in sorted(final_reviews)],
                    'moment_fractions': rescue_qc.get('moment_fractions'),
                }
                rejected_final_scenes = [
                    idx for idx in range(min(len(scenes), len(scene_visuals)))
                    if (
                        idx not in final_reviews
                        or (
                            int(final_reviews[idx].get('score', 0))
                            < quality_threshold
                            and idx not in manual_qa_preview_scenes
                        )
                    )
                ]

            if recovered_generated_media or not rejected_final_scenes:
                break
            additional_repairs = completion_repairs(
                options, duration_minutes, scenes, rejected_final_scenes, final_reviews,
                total_paid_create_cap, runway_attempts,
            )
            # A known provider failure can use the stock rescue above, but must
            # not cause another paid attempt on that scene in this same job.
            final_runway_repair_candidates = [index for index in (additional_repairs or [])
                if index not in final_runway_repair_failures
                and index not in omni_unsafe_submission_scenes]
            if not final_runway_repair_candidates:
                break

        visual_qc['final_reviews'] = final_visual_qc.get('reviews') or []
        if rejected_final_scenes:
            repair_checkpoint_available = False
            if is_bounded_short_preview:
                try:
                    repair_checkpoint_available = (
                        _persist_scene_repair_checkpoint(
                            task_id=task_id,
                            package=package,
                            voice_result=voice_result,
                            scene_durations=scene_durations,
                            generated_checkpoint_specs=(
                                generated_checkpoint_specs
                            ),
                            rejected_scene_indices=(
                                rejected_final_scenes
                            ),
                            staged_recovered_scenes=(
                                staged_recovered_scenes
                            ),
                            staged_voice_contract=(
                                staged_voice_contract
                            ),
                        )
                    )
                except Exception:
                    # Checkpointing is an optimization, never a reason to
                    # conceal or replace the real visual-quality rejection.
                    update_job(task_id, repair_available=False)
            diagnostics = _final_visual_rejection_diagnostics(
                scene_count=len(scenes),
                rejected_scene_indices=rejected_final_scenes,
                rescued_scene_indices=rescued_final_scenes,
                final_reviews=final_reviews,
                runway_attempts=runway_attempts,
                initial_generation_failed_scene_indices=(
                    runway_failed_scenes
                ),
                final_repair_failed_scene_indices=(
                    final_runway_repair_failures
                ),
                runway_failure_diagnostics=runway_failure_diagnostics,
                repair_checkpoint_available=(
                    repair_checkpoint_available
                ),
            )
            if diagnostics is None:
                raise FinalVisualQualityError(
                    'Final visual quality rejection state is inconsistent'
                )
            _checkpoint_selected_visuals(
                task_id, work, duration_minutes=duration_minutes,
                effective_edit_target_seconds=effective_edit_target_seconds,
                package=package, scene_visuals=scene_visuals, final_reviews=final_reviews,
                rejected_scene_indices=rejected_final_scenes, quality_threshold=quality_threshold,
                voice_result=voice_result, scene_durations=scene_durations, options=options,
                audio_qc=audio_qc, audio_duration_qc=audio_duration_qc,
                audio_prosody_qc=audio_prosody_qc,
            )
            _checkpoint_qa_workprint(
                task_id, work,
                duration_minutes=duration_minutes,
                effective_edit_target_seconds=effective_edit_target_seconds,
                scenes=scenes, scene_visuals=scene_visuals,
                final_reviews=final_reviews, voice_result=voice_result,
                scene_durations=scene_durations, narration=package['narration'],
                options=options, audio_qc=audio_qc,
                audio_duration_qc=audio_duration_qc, audio_prosody_qc=audio_prosody_qc,
            )
            from app.services.production_failures import content_rejection

            raise content_rejection(FinalVisualQualityError(
                'Final visual quality gate rejected: '
                + json.dumps(diagnostics, ensure_ascii=False, separators=(',', ':'))
            ), 'visual_quality_exhausted')

        unresolved_scenes = [idx for idx, specs in enumerate(scene_visuals) if not any(_visual_path(s) for s in specs)]
        if unresolved_scenes:
            raise RuntimeError(f'Visual quality gate rejected unresolved scenes: {unresolved_scenes}')
        if stock_reuse_visuals is not None:
            _require_unique_selected_stock(scene_visuals)

        # Synthetic camera motion over a still image can satisfy pixel-motion
        # probes without proving real temporal action. Even when the semantic
        # critic approves it, keep that exact clip private-review-only.
        for scene_idx in range(min(len(scenes), len(scene_visuals))):
            selected_spec = (
                scene_visuals[scene_idx][0]
                if scene_visuals[scene_idx]
                else None
            )
            if (
                isinstance(selected_spec, dict)
                and selected_spec.get('generation_provider')
                == 'gemini_image_motion'
            ):
                review = final_reviews.get(scene_idx)
                if not isinstance(review, dict):
                    raise FinalVisualQualityError(
                        'Gemini image-motion clip is missing final review'
                    )
                register_manual_qa_preview(
                    scene_idx,
                    review,
                    selected_spec,
                )

        final_manual_forced_overlap = manual_qa_preview_scenes & (
            provider_outage_stock_scenes
            | stock_quality_fallback_scenes
        )
        if final_manual_forced_overlap:
            raise FinalVisualQualityError(
                'Accepted manual-QA scenes cannot remain unresolved forced '
                'fallbacks: '
                + json.dumps(
                    {'scene_indices': sorted(final_manual_forced_overlap)},
                    separators=(',', ':'),
                )
            )
        manual_qa_scene_indices = sorted(manual_qa_preview_scenes)
        manual_qa_exact_revalidation_history = []
        for failure in terminal_manual_qa_failures:
            scene_idx = int(failure['scene_index'])
            history_item = dict(failure)
            history_item.update({
                'resolved': True,
                'resolution': (
                    'generated_repair'
                    if scene_idx in final_runway_repair_scenes
                    else 'fresh_stock_rescue'
                    if scene_idx in rescued_final_scenes
                    else 'revalidated'
                ),
            })
            manual_qa_exact_revalidation_history.append(history_item)
        manual_qa_scene_scores = {
            str(scene_idx): int(manual_qa_preview_scores[scene_idx])
            for scene_idx in manual_qa_scene_indices
        }
        manual_qa_scene_reviews = [
            manual_qa_preview_records[scene_idx]
            for scene_idx in manual_qa_scene_indices
        ]
        manual_qa_scene_source_types = {
            str(item['scene_index']): item['source_type']
            for item in manual_qa_scene_reviews
        }
        manual_qa_scene_retry_queries = {
            str(item['scene_index']): item['retry_queries']
            for item in manual_qa_scene_reviews
        }
        manual_qa_scene_reasons = {
            str(item['scene_index']): item['manual_reason']
            for item in manual_qa_scene_reviews
        }
        manual_qa_floors = {
            'stock': MANUAL_QA_PREVIEW_STOCK_FLOOR,
            'generated': MANUAL_QA_PREVIEW_GENERATED_FLOOR,
        }
        manual_qa_required = bool(manual_qa_scene_indices)
        quality_disposition = (
            'manual_qa_preview'
            if manual_qa_required
            else 'automated_qc_pass'
        )
        visual_specs = [spec for specs in scene_visuals for spec in specs if _visual_path(spec)]
        if not visual_specs:
            raise RuntimeError('No quality-approved visuals were available')

        final_audio_path = voice_path
        audio_design = {
            'music_requested': should_generate_music,
            'music_generated': bool(music_result),
            'music_error': music_error,
        }
        if music_result:
            set_stage(self, task_id, 'audio_design', 69, 'Anlatıcı ile özgün müzik dengeleniyor.')
            try:
                final_audio_path = mix_voice_and_music(
                    voice_path,
                    music_result['path'],
                    work / 'final_audio.mp3',
                )
                audio_design.update({
                    'music_model': music_result.get('model'),
                    'music_duration': music_result.get('duration'),
                    'mixed': True,
                })
            except Exception as exc:
                final_audio_path = voice_path
                audio_design.update({'mixed': False, 'mix_error': str(exc)[:600]})

        initial_reviews = visual_qc.get('reviews') or []
        initial_scores = [
            int(review.get('score', 0))
            for review in initial_reviews
            if isinstance(review, dict) and str(review.get('score', '')).isdigit()
        ]
        initial_avg_visual_score = round(sum(initial_scores) / len(initial_scores), 1) if initial_scores else None
        reviews = [final_reviews[idx] for idx in sorted(final_reviews)]
        scores = [
            int(review.get('score', 0))
            for review in reviews
            if isinstance(review, dict) and str(review.get('score', '')).isdigit()
        ]
        avg_visual_score = round(sum(scores) / len(scores), 1) if scores else None
        visual_qc['initial_average_score'] = initial_avg_visual_score
        visual_qc['average_final_score'] = avg_visual_score

        requested_seconds = duration_minutes * 60
        render_target_duration = _render_target_duration(
            options,
            effective_edit_target_seconds,
        )
        if selected_recovery is not None:
            from app.services.selected_visual_recovery import verify_selected_final_inputs
            _selected_recovery_authorization(
                task_id, retry_dispatch_source_id,
                _task_spec(topic, duration_minutes, language, channel_id, options), approved_package,
            )
            if (render_target_duration != selected_recovery['timing']['target_frames'] / 30
                    or _recovery_package_sha256(package) != selected_recovery['manifest']['package_sha256']
                    or voice_result != selected_recovery['voice_result'] or final_audio_path != voice_path
                    or final_visual_qc['selected_recovery_rejected_indices']):
                raise FinalVisualQualityError('Selected recovery render inputs changed after review')
            try:
                verify_selected_final_inputs(selected_recovery, work, scene_visuals,
                                             final_visual_qc['selected_recovery_qa_inputs'])
            except Exception:
                raise FinalVisualQualityError('Selected recovery exact render identity changed after review') from None
        set_stage(self, task_id, 'render', 76, 'Onaylı ses ve sahneler final kurguya alınıyor.')
        rendered = render_video(
            voice_path=final_audio_path,
            visual_paths=visual_specs,
            narration=package['narration'],
            output_path=work / 'final.mp4',
            scenes=scenes,
            scene_durations=scene_durations,
            scene_visual_paths=scene_visuals,
            target_duration=render_target_duration,
            output_resolution=resolution_for_mode(
                options.get('mode'), options.get('format'),
            ),
            **({'capture_scene_windows': True} if options.get('production_delivery') else {}),
        )

        actual_seconds = float(rendered.get('duration') or 0)
        if options.get('mode') == 'preview':
            duration_ok = _preview_duration_within_gate(
                actual_seconds,
                requested_seconds,
                voice_result.get('duration_after_fit'),
            )
        else:
            duration_basis = (effective_edit_target_seconds
                if options.get('production_scheduled') is True and options.get('format') == 'shorts'
                and requested_seconds == 30 else requested_seconds)
            duration_ok = duration_basis * 0.70 <= actual_seconds <= duration_basis * 1.22
        if not duration_ok:
            raise ProductionContentError(f'Final duration gate rejected render: {actual_seconds:.1f}s for requested {requested_seconds:.1f}s', code='render_quality_exhausted')

        if strict_short_preview_duration or (
            options.get('mode') == 'production'
            and options.get('format') == 'shorts'
            and duration_minutes == 0.5
        ):
            final_render_qc = _strict_short_preview_render_qc(
                rendered,
                effective_edit_target_seconds,
                voice_result.get('duration_after_fit'),
            )
            if final_render_qc.get('reason') == 'final_frame_count_mismatch':
                raise ProductionContentError(
                    'Final frame gate rejected render: '
                    f"{final_render_qc.get('actual_frames')} frames, expected "
                    f"{final_render_qc.get('expected_frames')}",
                    code='render_quality_exhausted',
                )
            if final_render_qc.get('pass') is not True:
                raise ProductionContentError(
                    'Final breathing-room gate rejected render: '
                    + json.dumps(
                        final_render_qc,
                        ensure_ascii=False,
                        separators=(',', ':'),
                    ),
                    code='render_quality_exhausted',
                )

        max_freeze_seconds = float(rendered.get('max_freeze_seconds') or 0)
        freeze_limit = 5.0 if options.get('mode') == 'preview' else 6.0
        if max_freeze_seconds > freeze_limit:
            raise ProductionContentError(
                f'Final motion gate rejected {max_freeze_seconds:.1f}s static interval '
                f'(limit {freeze_limit:.1f}s)',
                code='render_quality_exhausted',
            )

        thumbnail_fields = _persist_final_thumbnail(
            task_id, work, rendered, options, quality_disposition, manual_qa_required,
        )
        set_stage(self, task_id, 'upload', 92, 'Final master ve üretim dosyaları kalıcı depolamaya yükleniyor.')
        object_key = f'videos/{task_id}/final.mp4'
        upload_file(rendered['path'], object_key, 'video/mp4')
        from app.services.production_delivery import persist_delivery_manifest

        delivery_fields = persist_delivery_manifest(
            task_id, package, rendered, options, duration_minutes, work,
        )

        caption_key = None
        caption_url = None
        if options.get('subtitles') == 'sidecar':
            safe_language = re.sub(r'[^a-zA-Z0-9_-]+', '', language or 'tr') or 'tr'
            caption_key = f'videos/{task_id}/captions.{safe_language}.srt'
            upload_file(rendered['srt'], caption_key, 'application/x-subrip')
            caption_url = presigned_download_url(caption_key, 86400)

        paid_create_slots_used = (
            runway_attempts if total_paid_create_cap is not None else None
        )
        paid_create_slots_remaining = (
            max(0, total_paid_create_cap - runway_attempts)
            if total_paid_create_cap is not None
            else None
        )
        metadata_key = f'videos/{task_id}/metadata.json'
        meta_path = work / 'metadata.json'
        meta_path.write_text(json.dumps({
            'task_id': task_id,
            **thumbnail_fields,
            'topic': topic,
            'channel_id': channel_id,
            'requested_duration_minutes': duration_minutes,
            **delivery_fields,
            'effective_edit_target_seconds': effective_edit_target_seconds,
            'studio_options': options,
            'narration_word_count': package.get('narration_word_count'),
            'target_word_range': package.get('target_word_range'),
            'title': package.get('title'),
            'thumbnail_text': package.get('thumbnail_text'),
            'description': package.get('description'),
            'tags': package.get('tags', []),
            'hashtags': package.get('hashtags', []),
            'sources': package.get('sources', []),
            'director_qc': package.get('director_qc', []),
            'stock_scene_qc': package.get('stock_scene_qc'),
            'visual_qc': visual_qc,
            'quality_disposition': quality_disposition,
            'manual_qa_required': manual_qa_required,
            'manual_qa_floor': MANUAL_QA_PREVIEW_STOCK_FLOOR,
            'manual_qa_floors': manual_qa_floors,
            'manual_qa_scene_indices': manual_qa_scene_indices,
            'manual_qa_scene_scores': manual_qa_scene_scores,
            'manual_qa_scene_source_types': manual_qa_scene_source_types,
            'manual_qa_scene_retry_queries': manual_qa_scene_retry_queries,
            'manual_qa_scene_reasons': manual_qa_scene_reasons,
            'manual_qa_scene_reviews': manual_qa_scene_reviews,
            'manual_qa_exact_revalidation_history': (
                manual_qa_exact_revalidation_history
            ),
            'publish_quality_threshold': (
                MANUAL_QA_PUBLISH_QUALITY_THRESHOLD
            ),
            'visual_replacements': visual_replacements,
            'initial_average_visual_qc_score': initial_avg_visual_score,
            'average_visual_qc_score': avg_visual_score,
            'scenes': scenes,
            'scene_visual_specs': scene_visuals,
            'scene_durations': scene_durations,
            'spoken_texts': voice_result.get('spoken_texts', []),
            'voice_name': voice_result.get('voice_name'),
            'voice_model': voice_result.get('voice_model'),
            'voice_language_code': voice_result.get('voice_language_code'),
            'voice_duration_before_fit': voice_result.get('duration_before_fit'),
            'voice_duration_after_fit': voice_result.get('duration_after_fit'),
            'voice_tempo_rate': voice_result.get('tempo_rate'),
            'voice_removed_silence_seconds': voice_result.get(
                'removed_silence_seconds'
            ),
            'voice_compacted_boundary_pause_count': voice_result.get(
                'compacted_boundary_pause_count'
            ),
            'voice_compacted_trailing_silence': voice_result.get(
                'compacted_trailing_silence'
            ),
            'audio_generation_attempts': audio_generation_attempts,
            'selected_audio_generation_attempt': (
                selected_audio_generation_attempt
            ),
            'audio_qc_retry_history': audio_qc_retry_history,
            'audio_pause_repair': voice_result.get('internal_pause_repair'),
            'audio_synthesis_quality_errors': (
                audio_synthesis_quality_errors
            ),
            'audio_qc': audio_qc,
            'audio_duration_qc': audio_duration_qc,
            'audio_prosody_qc': audio_prosody_qc,
            'audio_design': audio_design,
            'stock_credits': credits,
            'preview_total_paid_create_cap': total_paid_create_cap,
            'paid_create_slots_used': paid_create_slots_used,
            'paid_create_slots_remaining': paid_create_slots_remaining,
            'runway_submission_cap': runway_submission_cap,
            'runway_required_submission_cap': (
                runway_required_submission_cap
            ),
            'runway_effective_submission_cap': runway_effective_submission_cap,
            'required_scene_completion_cap': (
                SHORT_PREVIEW_REQUIRED_RUNWAY_CAP
            ),
            'provider_outage_emergency_cap': (
                SHORT_PREVIEW_PROVIDER_OUTAGE_RUNWAY_CAP
            ),
            'stock_quality_emergency_cap': (
                SHORT_PREVIEW_STOCK_QUALITY_RUNWAY_CAP
            ),
            'provider_outage_stock_scene_indices': sorted(
                provider_outage_stock_scenes
            ),
            'provider_outage_stock_fallback_history_scene_indices': sorted(
                provider_outage_stock_fallback_history
            ),
            'stock_quality_fallback_scene_indices': sorted(
                stock_quality_fallback_scenes
            ),
            'stock_quality_fallback_history_scene_indices': sorted(
                stock_quality_fallback_history
            ),
            'runway_attempts': runway_attempts,
            'video_generation_provider_records': generated_video_provider_records,
            'runway_submission_scene_indices': [item['scene_index'] for item in selected_runway],
            'runway_success_scene_indices': sorted(runway_generated_scenes),
            'runway_failure_scene_indices': sorted(runway_failed_scenes),
            'runway_scenes_used': runway_scenes_used,
            'final_runway_repair_attempts': final_runway_repair_attempts,
            'final_runway_repair_scene_indices': sorted(final_runway_repair_scenes),
            'final_runway_repair_failure_scene_indices': sorted(final_runway_repair_failures),
            'runway_allocation': runway_allocation,
            'runway_failure_diagnostics': runway_failure_diagnostics,
            'caption_key': caption_key,
            'burned_subtitles': False,
            'render': rendered,
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        upload_file(meta_path, metadata_key, 'application/json')

        result = {
            'status': 'complete',
            'stage': 'complete',
            'progress': 100,
            'task_id': task_id,
            **thumbnail_fields,
            'channel_id': channel_id,
            **delivery_fields,
            'title': package.get('title'),
            'publish_metadata': {
                'title': package.get('title'),
                'thumbnail_key': thumbnail_fields['thumbnail_key'],
                'description': package.get('description'),
                'thumbnail_text': package.get('thumbnail_text'),
                'tags': package.get('tags', []),
                'hashtags': package.get('hashtags', []),
                'sources': package.get('sources', []),
            },
            'video_key': object_key,
            'download_url': presigned_download_url(object_key, 86400),
            'metadata_key': metadata_key,
            'caption_key': caption_key,
            'caption_url': caption_url,
            'burned_subtitles': False,
            'text_layers': 0,
            'duration': rendered.get('duration'),
            'effective_edit_target_seconds': effective_edit_target_seconds,
            'shots': rendered.get('shots'),
            'scenes': len(scenes),
            'unique_visuals': rendered.get('unique_visuals'),
            'quality_disposition': quality_disposition,
            'manual_qa_required': manual_qa_required,
            'manual_qa_floor': MANUAL_QA_PREVIEW_STOCK_FLOOR,
            'manual_qa_floors': manual_qa_floors,
            'manual_qa_scene_indices': manual_qa_scene_indices,
            'manual_qa_scene_scores': manual_qa_scene_scores,
            'manual_qa_scene_source_types': manual_qa_scene_source_types,
            'manual_qa_scene_retry_queries': manual_qa_scene_retry_queries,
            'manual_qa_scene_reasons': manual_qa_scene_reasons,
            'manual_qa_scene_reviews': manual_qa_scene_reviews,
            'manual_qa_exact_revalidation_history': (
                manual_qa_exact_revalidation_history
            ),
            'publish_quality_threshold': (
                MANUAL_QA_PUBLISH_QUALITY_THRESHOLD
            ),
            'preview_total_paid_create_cap': total_paid_create_cap,
            'paid_create_slots_used': paid_create_slots_used,
            'paid_create_slots_remaining': paid_create_slots_remaining,
            'runway_submission_cap': runway_submission_cap,
            'runway_required_submission_cap': (
                runway_required_submission_cap
            ),
            'runway_effective_submission_cap': runway_effective_submission_cap,
            'required_scene_completion_cap': (
                SHORT_PREVIEW_REQUIRED_RUNWAY_CAP
            ),
            'provider_outage_emergency_cap': (
                SHORT_PREVIEW_PROVIDER_OUTAGE_RUNWAY_CAP
            ),
            'stock_quality_emergency_cap': (
                SHORT_PREVIEW_STOCK_QUALITY_RUNWAY_CAP
            ),
            'provider_outage_stock_scene_indices': sorted(
                provider_outage_stock_scenes
            ),
            'provider_outage_stock_fallback_history_scene_indices': sorted(
                provider_outage_stock_fallback_history
            ),
            'stock_quality_fallback_scene_indices': sorted(
                stock_quality_fallback_scenes
            ),
            'stock_quality_fallback_history_scene_indices': sorted(
                stock_quality_fallback_history
            ),
            'runway_attempts': runway_attempts,
            'video_generation_provider_records': generated_video_provider_records,
            'runway_submission_scene_indices': [item['scene_index'] for item in selected_runway],
            'runway_success_scene_indices': sorted(runway_generated_scenes),
            'runway_scenes_used': runway_scenes_used,
            'final_runway_repair_attempts': final_runway_repair_attempts,
            'final_runway_repair_scene_indices': sorted(final_runway_repair_scenes),
            'final_runway_repair_failure_scene_indices': sorted(final_runway_repair_failures),
            'resolution': rendered.get('resolution'),
            'scene_synced': rendered.get('scene_synced'),
            'director_qc_applied': bool(package.get('director_qc')),
            'stock_scene_qc_applied': bool(package.get('stock_scene_qc')),
            'visual_qc_reviews': len(reviews),
            'visual_replacements': len(visual_replacements),
            'initial_average_visual_qc_score': initial_avg_visual_score,
            'average_visual_qc_score': avg_visual_score,
            'narration_word_count': package.get('narration_word_count'),
            'voice_model': voice_result.get('voice_model'),
            'voice_language_code': voice_result.get('voice_language_code'),
            'voice_duration_before_fit': voice_result.get('duration_before_fit'),
            'voice_duration_after_fit': voice_result.get('duration_after_fit'),
            'voice_tempo_rate': voice_result.get('tempo_rate'),
            'voice_removed_silence_seconds': voice_result.get(
                'removed_silence_seconds'
            ),
            'voice_compacted_boundary_pause_count': voice_result.get(
                'compacted_boundary_pause_count'
            ),
            'voice_compacted_trailing_silence': voice_result.get(
                'compacted_trailing_silence'
            ),
            'audio_qc': audio_qc,
            'audio_duration_qc': audio_duration_qc,
            'audio_prosody_qc': audio_prosody_qc,
            'audio_generation_attempts': audio_generation_attempts,
            'selected_audio_generation_attempt': (
                selected_audio_generation_attempt
            ),
            'audio_qc_retry_history': audio_qc_retry_history,
            'audio_pause_repair': voice_result.get('internal_pause_repair'),
            'audio_synthesis_quality_errors': (
                audio_synthesis_quality_errors
            ),
            'audio_design': audio_design,
            'studio_options': options,
        }
        mark_success(task_id, result)
        _queue_automatic_publish_if_enabled(task_id, options)
        if delivery_fields.get('delivery_manifest_key'):
            try:
                from app.services.production_delivery_runtime import queue_delivery_family

                queue_delivery_family(task_id)
            except Exception:
                pass  # The cloud tick retries discovery, never parent generation.
        return result
    except Exception as exc:
        try:
            from app.services.failed_master_workprint import checkpoint
            checkpoint(task_id, work, options=options, duration_minutes=duration_minutes)
        except Exception:
            pass  # Preserve the original terminal error even if preview storage fails.
        if total_paid_create_cap is not None:
            try:
                # Refresh the public failure count from the authoritative
                # ledger, including an ambiguous reservation response.
                runway_attempts = _persisted_paid_create_slots(
                    task_id, total_paid_create_cap,
                )
            except FinalVisualQualityError:
                pass
        # Match Celery's non-retryable errors so a budget stop is persisted as
        # terminal; otherwise plan_retry can keep a production slot forever.
        terminal_pre_media_error = isinstance(
            exc,
            (
                ProductionContentError,
                FinalVisualQualityError,
                FinalAudioQualityError,
                GeminiOmniContinuityReferenceError,
                ImmutableNarrationSceneBudgetError,
                UnsupportedLanguageError,
                SpendBlocked,
                AbacusGenerationError,
            ),
        )
        if options.get('production_delivery') is not None and not terminal_pre_media_error:
            bounded_error = FinalVisualQualityError(
                f'Long-form delivery stopped without automatic master rebuild: {type(exc).__name__}'
            )
            mark_failure(task_id, bounded_error)
            raise bounded_error from exc
        if (retry_dispatch_source_id and options.get('mode') == 'production'
                and options.get('format') == 'landscape' and duration_minutes == 3
                and not terminal_pre_media_error):
            bounded_error = FinalVisualQualityError(
                f'Long-form retained job stopped without automatic restart: {type(exc).__name__}'
            )
            mark_failure(task_id, bounded_error)
            raise bounded_error from exc
        if full_rebuild_source_id is not None and not terminal_pre_media_error:
            # One explicit rebuild must not silently restart the whole pipeline
            # after an ambiguous planning, voice or provider response. Ordinary
            # retry behavior and per-provider bounded attempts are unchanged.
            bounded_error = FinalVisualQualityError(
                f'Full rebuild stopped without automatic restart: {type(exc).__name__}'
            )
            mark_failure(task_id, bounded_error)
            raise bounded_error from exc
        if media_started and not terminal_pre_media_error:
            bounded_error = SpendBlocked('production_media_outcome_unverified')
            mark_failure(task_id, bounded_error)
            raise bounded_error from exc
        if (
            runway_attempts == 0
            and not terminal_pre_media_error
            and int(getattr(self.request, 'retries', 0) or 0) < int(self.max_retries or 0)
        ):
            set_stage(
                self,
                task_id,
                'plan_retry',
                6,
                'Görsel ön kontrol yenileniyor; ücretli üretim başlamadan yeni storyboard hazırlanıyor.',
            )
            raise
        if runway_attempts > 0 and not terminal_pre_media_error:
            bounded_error = FinalVisualQualityError(
                f'Post-Runway pipeline failed after {runway_attempts} bounded submissions: {type(exc).__name__}'
            )
            mark_failure(task_id, bounded_error)
            raise bounded_error from exc
        mark_failure(task_id, exc)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
