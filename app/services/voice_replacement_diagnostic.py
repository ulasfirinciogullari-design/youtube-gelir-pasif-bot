"""Preserve one existing paid MP3 before fitting; never approve or reuse it."""
from __future__ import annotations

import hashlib
import re
from uuid import UUID

from app.services import storage


MAX_RAW_VOICE_BYTES = 14 * 1024 * 1024
_ETAG = re.compile(r'^"[A-Za-z0-9_-]{1,128}"$')


class VoiceReplacementDiagnosticError(RuntimeError):
    """Fixed diagnostic error; the caller must not issue another TTS request."""


def persist_raw_voice_replacement(task_id: str, audio_bytes: bytes) -> dict:
    """Create-only private storage of original bytes, without invented timings.

    This is not an audio_candidate_checkpoint, a QA pass, or permission to run
    any recovery. The caller must stop if persistence cannot be confirmed.
    """
    try:
        if (not isinstance(task_id, str) or str(UUID(task_id)) != task_id
                or type(audio_bytes) is not bytes or not 1024 <= len(audio_bytes) <= MAX_RAW_VOICE_BYTES
                or not (audio_bytes.startswith(b'ID3')
                        or audio_bytes[0] == 0xFF and audio_bytes[1] & 0xE0 == 0xE0)):
            raise ValueError('Invalid raw voice input')
        checksum = hashlib.sha256(audio_bytes).hexdigest()
        key = f'audio_replacement_diagnostics/{task_id}/{checksum}.mp3'
        client = storage._client()
        try:
            response = client.put_object(
                Bucket=storage.settings.bucket, Key=key, Body=audio_bytes,
                ContentLength=len(audio_bytes), ContentType='audio/mpeg',
                CacheControl='private, no-store', Metadata={'sha256': checksum}, IfNoneMatch='*',
            )
        except Exception as exc:
            error = getattr(exc, 'response', {})
            if (not isinstance(error, dict) or not isinstance(error.get('Error'), dict)
                    or str(error['Error'].get('Code')) not in {'412', 'PreconditionFailed'}):
                raise ValueError('Raw voice storage unavailable') from None
            response = client.head_object(Bucket=storage.settings.bucket, Key=key)
            if (type(response.get('ContentLength')) is not int
                    or response['ContentLength'] != len(audio_bytes)
                    or response.get('ContentType') != 'audio/mpeg'
                    or not isinstance(response.get('Metadata'), dict)
                    or response['Metadata'].get('sha256') != checksum):
                raise ValueError('Raw voice storage conflict')
        if (not isinstance(response, dict) or response.get('ResponseMetadata', {}).get('HTTPStatusCode') != 200
                or not isinstance(response.get('ETag'), str) or not _ETAG.fullmatch(response['ETag'])):
            raise ValueError('Raw voice storage receipt invalid')
        return {'version': 1, 'status': 'unapproved_raw_voice', 'task_id': task_id,
                'key': key, 'sha256': checksum, 'size': len(audio_bytes),
                'qa_approved': False, 'reusable': False, 'publish_eligible': False, 'requires_full_qa': True}
    except Exception:
        raise VoiceReplacementDiagnosticError('voice_replacement_raw_audio_unavailable') from None
