"""Store existing STT evidence for diagnosis, never as a quality approval."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import tempfile
from uuid import UUID

from app.services import audio_checkpoint
from app.services.storage import upload_file


MAX_AUDIO_EVIDENCE_BYTES = 512 * 1024
MAX_AUDIO_EVIDENCE_ITEMS = 20000
_PROVIDERS = frozenset({'openai', 'elevenlabs', 'gemini'})
_MODEL = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
_LANGUAGE = re.compile(r'^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$')
_PRIVATE_TEXT = re.compile(
    r'[a-z][a-z0-9+.-]*://|\bwww\.|\bBearer\s+\S+'
    r'|\b(?:sk-[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,})'
    r'|\b(?:api[_ -]?key|authorization|access[_ -]?token|refresh[_ -]?token|'
    r'client[_ -]?secret|password)\s*[:=]',
    re.IGNORECASE,
)


class AudioProviderEvidenceError(RuntimeError):
    """A fixed, provider-detail-free diagnostic persistence failure."""


class _EvidenceWhitelist:
    def __init__(self):
        self.remaining_bytes = MAX_AUDIO_EVIDENCE_BYTES
        self.remaining_items = MAX_AUDIO_EVIDENCE_ITEMS

    def _consume(self, size: int = 16) -> None:
        self.remaining_items -= 1
        self.remaining_bytes -= size
        if self.remaining_items < 0 or self.remaining_bytes < 0:
            raise ValueError('Evidence exceeds its diagnostic cap')

    def scalar(self, value: object, *, text_limit: int = 256) -> object:
        # Preserve wrong-but-safe scalar types, negative/zero/overlapping
        # times, empty text and absent fields. Validators must see those
        # failures; this persistence layer must not reinterpret them.
        self._consume()
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            if not math.isfinite(value) or abs(value) > 10**15:
                raise ValueError('Evidence numeric scalar is unavailable')
            return value
        if isinstance(value, str):
            if len(value) > text_limit or _PRIVATE_TEXT.search(value):
                raise ValueError('Evidence text is not safe')
            self._consume(len(value.encode('utf-8')))
            return value
        raise ValueError('Evidence contains an unsupported value')

    def fields(self, item: dict, names: tuple[str, ...], *, text_limit: int = 256) -> dict:
        return {
            name: self.scalar(item[name], text_limit=text_limit)
            for name in names if name in item
        }

    def items(self, value: object) -> list:
        if not isinstance(value, list) or len(value) > self.remaining_items:
            raise ValueError('Evidence collection is unavailable')
        self._consume(len(value) * 16)
        return value

    def offset(self, value: object) -> object:
        if isinstance(value, dict):
            return self.fields(value, ('seconds', 'nanos'))
        return self.scalar(value)

    def payload(self, provider: str, payload: object) -> dict:
        if not isinstance(payload, dict):
            raise ValueError('Evidence payload is unavailable')
        if provider != 'gemini':
            result = self.fields(payload, ('text', 'transcript'), text_limit=MAX_AUDIO_EVIDENCE_BYTES)
            result.update(self.fields(payload, ('language', 'language_code')))
            if 'words' in payload:
                words = payload['words']
                if words is None:
                    result['words'] = None
                else:
                    result['words'] = [
                        self.fields(word, ('word', 'text', 'start', 'end', 'type'))
                        for word in self.items(words) if isinstance(word, dict)
                    ]
            return result

        result = self.fields(payload, ('status',))
        if 'steps' not in payload:
            return result
        result['steps'] = []
        for step in self.items(payload['steps']):
            if not isinstance(step, dict) or step.get('type') != 'model_output':
                continue
            output = {'type': 'model_output'}
            if 'content' in step:
                output['content'] = []
                for content in self.items(step['content']):
                    if not isinstance(content, dict) or content.get('type') != 'text':
                        continue
                    text = {'type': 'text', **self.fields(content, ('text',), text_limit=MAX_AUDIO_EVIDENCE_BYTES)}
                    if 'annotations' in content:
                        annotations = content['annotations']
                        if annotations is None:
                            text['annotations'] = None
                        else:
                            text['annotations'] = []
                            for annotation in self.items(annotations):
                                if not isinstance(annotation, dict) or annotation.get('type') != 'word_info':
                                    continue
                                word = {'type': 'word_info', **self.fields(annotation, ('text',))}
                                for name in ('start_offset', 'end_offset'):
                                    if name in annotation:
                                        word[name] = self.offset(annotation[name])
                                text['annotations'].append(word)
                    output['content'].append(text)
            result['steps'].append(output)
        return result


def _audio_fingerprint(task_id: str, audio_path: str | Path) -> tuple[str, int]:
    source = audio_checkpoint._local_candidate_path(task_id, {'path': audio_path})
    before = source.stat()
    checksum = hashlib.sha256()
    total = 0
    with source.open('rb') as stream:
        while chunk := stream.read(64 * 1024):
            total += len(chunk)
            if total > audio_checkpoint.MAX_AUDIO_CANDIDATE_BYTES:
                raise ValueError('Audio exceeds evidence size cap')
            checksum.update(chunk)
    after = source.stat()
    if (
        total != before.st_size
        or (before.st_size, before.st_mtime_ns, before.st_ino, before.st_dev)
        != (after.st_size, after.st_mtime_ns, after.st_ino, after.st_dev)
    ):
        raise ValueError('Audio changed during evidence binding')
    return checksum.hexdigest(), total


def persist_audio_provider_evidence(
    task_id: str,
    audio_path: str | Path,
    *,
    provider: str,
    model: str,
    language: str,
    payload: dict,
) -> dict:
    """Store only whitelisted JSON; never upload audio or call a provider.

    Call before provider response/timing validators. The returned pointer
    proves neither source truth nor transcription/prosody correctness. The
    caller owns best-effort handling and must still run every normal QA gate.
    """
    try:
        if (
            str(UUID(task_id)) != task_id or provider not in _PROVIDERS
            or not isinstance(model, str) or not _MODEL.fullmatch(model)
            or _PRIVATE_TEXT.search(model)
            or not isinstance(language, str) or not _LANGUAGE.fullmatch(language)
        ):
            raise ValueError('Invalid evidence identity')
        safe_payload = _EvidenceWhitelist().payload(provider, payload)
        audio_sha256, audio_size = _audio_fingerprint(task_id, audio_path)
        evidence = {
            'version': 1,
            'status': 'unvalidated_provider_evidence',
            'diagnostic_only': True,
            'qa_approved': False,
            'requires_full_qa': True,
            'source_task_id': task_id,
            'provider': provider,
            'model': model,
            'language': language,
            'audio_sha256': audio_sha256,
            'audio_size': audio_size,
            'payload': safe_payload,
        }
        encoded = json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
        if len(encoded) > MAX_AUDIO_EVIDENCE_BYTES:
            raise ValueError('Evidence exceeds storage size cap')
        evidence_sha256 = hashlib.sha256(encoded).hexdigest()
        key = f'audio_evidence/{task_id}/{audio_sha256}/{provider}/{evidence_sha256}.json'
        with tempfile.TemporaryDirectory(prefix='audio_evidence_', dir=audio_checkpoint._AUDIO_TEMP_ROOT) as temporary:
            path = Path(temporary) / 'evidence.json'
            path.write_bytes(encoded)
            upload_file(path, key, 'application/json')
        return {
            'version': 1,
            'status': 'unvalidated_provider_evidence',
            'diagnostic_only': True,
            'qa_approved': False,
            'requires_full_qa': True,
            'provider': provider,
            'model': model,
            'language': language,
            'key': key,
            'evidence_sha256': evidence_sha256,
            'audio_sha256': audio_sha256,
            'audio_size': audio_size,
            'size': len(encoded),
        }
    except Exception:
        raise AudioProviderEvidenceError('Audio provider evidence unavailable') from None
