"""Read existing approved narration for a metadata-only upload description."""

from __future__ import annotations

import json
from uuid import UUID

from app.services import storage


MAX_METADATA_BYTES = 1024 * 1024
MAX_DESCRIPTION_CHARACTERS = 4000


def _unique_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate metadata field')
        result[key] = value
    return result


def _invalid_constant(_value: str):
    raise ValueError('Invalid metadata number')


def approved_narration_description(source_task_id: str, source_job: dict) -> str:
    """No generation, mutation or QA approval: use only the completed cut's text.

    Legacy render results retain the scene count, not narration. Their existing
    same-task final metadata contains the actual approved scene text. The
    publication plan may copy it without changing a recovery/story fingerprint.
    """
    body = None
    try:
        from app.services.youtube_automation import automated_quality_approved

        if not automated_quality_approved(source_job):
            raise ValueError('Final quality approval is required')
        if not isinstance(source_task_id, str) or str(UUID(source_task_id)) != source_task_id:
            raise ValueError('Invalid final task')
        result = source_job['result']
        metadata_key = f'videos/{source_task_id}/metadata.json'
        count = result.get('scenes')
        title = result.get('title')
        if (
            source_job.get('task_id') != source_task_id
            or result.get('task_id') != source_task_id
            or result.get('video_key') != f'videos/{source_task_id}/final.mp4'
            or result.get('metadata_key') != metadata_key
            or type(count) is not int or not 1 <= count <= 256
            or not isinstance(title, str) or not title.strip() or len(title) > 500
        ):
            raise ValueError('Invalid final metadata binding')
        response = storage._client().get_object(Bucket=storage.settings.bucket, Key=metadata_key)
        body = response.get('Body')
        size = response.get('ContentLength')
        if (
            type(size) is not int or not 1 <= size <= MAX_METADATA_BYTES
            or response.get('ContentType') != 'application/json'
            or response.get('ContentEncoding') not in (None, '')
            or (response.get('ResponseMetadata') or {}).get('HTTPStatusCode') != 200
            or not callable(getattr(body, 'read', None))
            or not callable(getattr(body, 'close', None))
        ):
            raise ValueError('Invalid final metadata response')
        chunks = []
        remaining = size
        while remaining:
            limit = min(64 * 1024, remaining)
            chunk = body.read(limit)
            if not isinstance(chunk, bytes) or not chunk or len(chunk) > limit:
                raise ValueError('Incomplete final metadata')
            chunks.append(chunk)
            remaining -= len(chunk)
        if body.read(1) != b'':
            raise ValueError('Final metadata exceeds its declared size')
        metadata = json.loads(
            b''.join(chunks).decode('utf-8'),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
        if (
            not isinstance(metadata, dict)
            or metadata.get('task_id') != source_task_id
            or metadata.get('title') != title
            or metadata.get('quality_disposition') != 'automated_qc_pass'
            or metadata.get('manual_qa_required') is not False
        ):
            raise ValueError('Final metadata does not match its approved result')
        scenes = metadata.get('scenes')
        if not isinstance(scenes, list) or len(scenes) != count:
            raise ValueError('Final scene count does not match')
        narrations = []
        for index, scene in enumerate(scenes):
            if (
                not isinstance(scene, dict)
                or type(scene.get('index')) is not int or scene['index'] != index
                or not isinstance(scene.get('narration'), str)
                or not scene['narration'].strip()
                or len(scene['narration']) > MAX_DESCRIPTION_CHARACTERS
                or any(ord(char) < 32 and char not in '\t\r\n' for char in scene['narration'])
            ):
                raise ValueError('Invalid final narration')
            narrations.append(scene['narration'].strip())
        description = ' '.join(narrations)
        if not 1 <= len(description) <= MAX_DESCRIPTION_CHARACTERS:
            raise ValueError('Final narration exceeds the description limit')
        return description
    except Exception:
        # Never surface storage endpoints, object keys, provider errors or text.
        raise ValueError('Approved final narration is unavailable for publishing') from None
    finally:
        if body is not None:
            try:
                body.close()
            except Exception:
                pass
