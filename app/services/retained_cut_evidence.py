"""Opt-in local cut derivation and private byte preservation, never approval.

The caller supplies source hashes from its existing authenticated source read.
This module checks the original media bytes and records the actual successful
normalization commands, probes and tool identities. It does not authenticate
source eligibility, reproduce historical cuts, sample reviewer JPEGs, send a
provider request, or grant render/QA/publish/claim authority. Stored objects have
no Redis admission anchor. Object ACL checks do not establish bucket/CDN policy.
"""
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import threading
from uuid import UUID
import weakref

from app.services import render, storage
from app.services.retained_audio_review_evidence import _source_acl_shape


MAX_CUT_BYTES = 100 * 1024 * 1024
MAX_MANIFEST_BYTES = 512 * 1024
_FLAGS = {'diagnostic_only': True, 'qa_approved': False, 'publish_eligible': False,
          'full_qa_complete': False, 'claim_authorized': False, 'resume_authorized': False,
          'sampled_wire_linkage_verified': False, 'historical_cut_identity_verified': False}
_SOURCE = {'source_task_id', 'source_state_sha256', 'source_spec_sha256',
           'source_journal_sha256', 'source_metadata_sha256'}
_ISSUED = weakref.WeakKeyDictionary()
_PERSISTED = weakref.WeakKeyDictionary()
_LOCK = threading.Lock()
_TIGRIS = {'https://t3.storageapi.dev', 'https://t3.storage.dev', 'https://fly.storage.tigris.dev'}
_ADMINS = {'Grantee': {'Type': 'Group', 'URI': 'https://groups.tigris.dev/org/admins'},
           'Permission': 'FULL_CONTROL'}


class RetainedCutEvidenceError(RuntimeError):
    """Fixed local error; no paths, commands, source text or backend messages."""


def _require(condition):
    if not condition:
        raise RetainedCutEvidenceError('retained_cut_evidence_unverified')


def _raw(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')
    _require(len(data) <= MAX_MANIFEST_BYTES)
    return data


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _digest(value):
    _require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None)


def _file(path, maximum=MAX_CUT_BYTES, *, content=False):
    path = Path(path)
    _require(path.is_absolute() and '..' not in path.parts and not path.is_symlink())
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        _require(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= maximum)
        digest, size, parts = hashlib.sha256(), 0, []
        while chunk := stream.read(min(65536, maximum - size + 1)):
            size += len(chunk)
            _require(size <= maximum)
            digest.update(chunk)
            if content:
                parts.append(chunk)
        after = os.fstat(stream.fileno())
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        _require(identity(before) == identity(after) == identity(path.stat(follow_symlinks=False))
                 and size == before.st_size)
    return {'sha256': digest.hexdigest(), 'size': size}, b''.join(parts) if content else None


def _build_identity():
    tools = {}
    for name in ('ffmpeg', 'ffprobe'):
        executable = shutil.which(name)
        _require(type(executable) is str)
        path = Path(executable).resolve(strict=True)
        before, _ = _file(path, 256 * 1024 * 1024)
        version = subprocess.check_output([str(path), '-version'], stderr=subprocess.DEVNULL, timeout=10)
        _require(type(version) is bytes and 0 < len(version) <= 64 * 1024)
        after, _ = _file(path, 256 * 1024 * 1024)
        _require(before == after)
        tools[name] = {'executable': before, 'version_sha256': _sha(version), 'version_size': len(version)}
    directory = Path(__file__).resolve().parent
    code = {name: _file(directory / name, 2 * 1024 * 1024)[0]['sha256'] for name in
            ('render.py', 'retained_cut_evidence.py', 'preserved_visual_recovery.py',
             'visual_qc.py', 'retained_sampled_input_linkage.py')}
    return {'tools': tools, 'source_sha256': code}


class PreparedRetainedCuts:
    """An owner-thread local diagnostic; its JSON is not a consumer permit."""
    __slots__ = ('__weakref__',)

    def __new__(cls, *args, **kwargs):
        raise TypeError('retained_cut_evidence_private')

    def __repr__(self):
        return '<PreparedRetainedCuts diagnostic-only>'

    @property
    def record(self):
        return json.loads(_checked(self)[0])

    @property
    def inputs(self):
        return deepcopy(_checked(self)[1])

    @property
    def frame_counts(self):
        return [row['target_frames'] for row in self.record['cuts']]


def _checked(value):
    _require(type(value) is PreparedRetainedCuts)
    with _LOCK:
        state = _ISSUED.get(value)
    _require(state is not None and state[3] == threading.get_ident())
    return state


def _persisted_receipt(artifact):
    _checked(artifact)
    with _LOCK:
        raw = _PERSISTED.get(artifact)
    _require(type(raw) is bytes)
    return json.loads(raw)


def _verify_files(record, files):
    for path, expected in files:
        _require(_file(path)[0] == expected)
    _require(_build_identity() == record['build'])


def verify_retained_cuts(artifact):
    """Read-only local continuity check; no historical sampling assertion."""
    try:
        encoded, _, files, _, _ = _checked(artifact)
        _verify_files(json.loads(encoded), files)
    except RetainedCutEvidenceError:
        raise
    except Exception:
        raise RetainedCutEvidenceError('retained_cut_evidence_unverified') from None


def prepare_retained_cuts(package, voice, paths, work, *, source_binding, raw_bindings):
    """Normalize exactly six existing originals locally; no client or provider.

    Source hashes must come from the caller's unchanged source validation. Raw
    bindings are the six manifest {sha256, size, provider} projections in order.
    The voice SHA/size is provided as source_binding['audio']; source hash fields
    are separately retained without claiming a new source admission.
    """
    try:
        _require(type(source_binding) is dict and set(source_binding) == _SOURCE | {'audio'})
        source = deepcopy(source_binding)
        _require(type(source['source_task_id']) is str
                 and str(UUID(source['source_task_id'])) == source['source_task_id'])
        for key in _SOURCE - {'source_task_id'}:
            _digest(source[key])
        _require(type(package) is dict and type(package.get('scenes')) is list
                 and len(package['scenes']) == 6 and type(voice) is dict
                 and type(paths) in (list, tuple) and len(paths) == 6
                 and type(raw_bindings) is list and len(raw_bindings) == 6)
        audio, _ = _file(Path(voice['path']), 14 * 1024 * 1024)
        _require(type(source['audio']) is dict and set(source['audio']) == {'sha256', 'size'}
                 and _raw(audio) == _raw(source['audio']))
        frozen_package = _raw(package)
        frozen_voice = _raw({key: value for key, value in voice.items() if key != 'path'})
        build = _build_identity()
        duration_probe = getattr(render.media_duration, '__wrapped__', render.media_duration)
        measured = float(duration_probe(voice['path']))
        _require(math.isfinite(measured) and 28.7 <= measured <= 30.08
                 and abs(measured - voice['duration_after_fit']) <= 0.12)
        pools, originals = [], []
        for index, (path, provider) in enumerate(paths):
            expected = raw_bindings[index]
            _require(type(expected) is dict and set(expected) == {'sha256', 'size', 'provider'}
                     and type(provider) is str and provider == expected['provider'])
            actual, _ = _file(path)
            _require(_raw(actual) == _raw({key: expected[key] for key in ('sha256', 'size')}))
            originals.append((Path(path), actual))
            pools.append([{'path': str(path), 'generated': True, 'source_type': 'generated',
                'generation_provider': provider, 'start_fraction': 0.0,
                'preserve_start_fraction': True, 'forbid_loop': True}])
        timeline = render._scene_timeline(package['scenes'], pools, voice['scene_durations'], measured, [])
        _require(len(timeline) == 6 and [row[3] for row in timeline] == list(range(6)))
        counts = render._timeline_frame_counts(timeline, measured)
        directory = Path(work) / 'preserved_exact_review'
        _require(directory.is_absolute() and not directory.is_symlink())
        directory.mkdir(mode=0o700, exist_ok=False)
        inputs, cuts, files = [], [], [*originals, (Path(voice['path']), audio)]
        for index, (spec, duration, transition, owner) in enumerate(timeline):
            target, recipes = directory / f'scene-{index:02d}.mp4', []
            render.normalize_clip(spec, target, counts[index] / render.FPS, index, transition,
                                  '1080x1920', _recipe_recorder=recipes.append)
            _require(len(recipes) == 1)
            os.chmod(target, 0o600)
            identity, _ = _file(target)
            actual_frames = render.video_frame_count(target)
            _require(type(actual_frames) is int and actual_frames == counts[index])
            recipe = recipes[0]
            for attempt in recipe['attempts']:
                argv = attempt['argv']
                attempt['executed_argv_sha256'] = _sha(_raw(argv))
                attempt['argv'] = ['$input' if item == spec['path'] else
                                   '$output' if item == str(target) else item for item in argv]
            cuts.append({'scene_index': owner, 'candidate_index': 0, 'raw': deepcopy(raw_bindings[index]),
                'cut': identity, 'timeline_duration': duration, 'transition': transition,
                'target_frames': counts[index], 'actual_frames': actual_frames,
                'recipe': recipe, 'recipe_sha256': _sha(_raw(recipe))})
            files.append((target, identity))
            inputs.append([{**spec, 'path': str(target)}])
        record = {'version': 1, 'kind': 'retained_cut_derivation', 'source': source,
            'package_sha256': _sha(frozen_package), 'voice_metadata_sha256': _sha(frozen_voice),
            'measured_voice_duration': measured, 'fps': render.FPS, 'output_resolution': '1080x1920',
            'total_frames': sum(counts), 'build': build, 'cuts': cuts, **_FLAGS}
        _require(_raw(package) == frozen_package
                 and _raw({key: value for key, value in voice.items() if key != 'path'}) == frozen_voice)
        _verify_files(record, files)
        encoded = _raw(record)
        sidecar = directory / f'derivation-{_sha(encoded)}.json'
        fd = os.open(sidecar, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            _require(stream.write(encoded) == len(encoded))
            stream.flush()
            os.fsync(stream.fileno())
        artifact = object.__new__(PreparedRetainedCuts)
        with _LOCK:
            _ISSUED[artifact] = (encoded, inputs, tuple(files), threading.get_ident(), False)
        return artifact
    except RetainedCutEvidenceError:
        raise
    except Exception:
        raise RetainedCutEvidenceError('retained_cut_evidence_unverified') from None


def _storage_guard(client, bucket):
    meta = getattr(client, 'meta', None)
    retries = getattr(getattr(meta, 'config', None), 'retries', None)
    _require(type(retries) is dict and (
        type(retries.get('total_max_attempts')) is int and retries['total_max_attempts'] == 1
        if 'total_max_attempts' in (retries or {}) else
        type(retries.get('max_attempts')) is int and retries['max_attempts'] == 0))
    _require(type(bucket) is str and re.fullmatch('[A-Za-z0-9][A-Za-z0-9._-]{2,62}', bucket)
             and bucket == storage.settings.bucket and type(getattr(meta, 'endpoint_url', None)) is str
             and meta.endpoint_url == storage.settings.endpoint and meta.endpoint_url.startswith('https://'))


def _put(client, bucket, key, data, content_type):
    _storage_guard(client, bucket)
    pointer = {'key': key, 'sha256': _sha(data), 'size': len(data), 'content_type': content_type}
    response = client.put_object(Bucket=bucket, Key=key, Body=data, ContentLength=len(data),
        ContentType=content_type, CacheControl='private, no-store', Metadata={'sha256': pointer['sha256']},
        ACL='private', IfNoneMatch='*')
    status = response.get('ResponseMetadata', {}).get('HTTPStatusCode')
    _require(type(status) is int and status == 200)
    _require(_read_private(client, bucket, pointer) == data)
    return pointer


def _read_private(client, bucket, pointer):
    _storage_guard(client, bucket)
    _require(type(pointer) is dict and set(pointer) == {'key', 'sha256', 'size', 'content_type'}
             and type(pointer['size']) is int and 0 < pointer['size'] <= MAX_CUT_BYTES)
    _digest(pointer['sha256'])
    response = client.get_object(Bucket=bucket, Key=pointer['key'])
    body = response.get('Body')
    try:
        status = response.get('ResponseMetadata', {}).get('HTTPStatusCode')
        _require(type(status) is int and status == 200
                 and type(response.get('ContentLength')) is int and response['ContentLength'] == pointer['size']
                 and response.get('ContentType') == pointer['content_type'])
        total, digest, chunks = 0, hashlib.sha256(), []
        while True:
            chunk = body.read(min(65536, pointer['size'] - total + 1))
            _require(type(chunk) is bytes)
            if not chunk:
                break
            total += len(chunk)
            _require(total <= pointer['size'])
            digest.update(chunk)
            chunks.append(chunk)
        _require(total == pointer['size'] and digest.hexdigest() == pointer['sha256'])
    finally:
        if body is not None:
            body.close()
    _storage_guard(client, bucket)
    acl = client.get_object_acl(Bucket=bucket, Key=pointer['key'])
    _source_acl_shape(acl, code='retained_cut_evidence_unverified')
    owner, grants = acl['Owner'], acl['Grants']
    _require(len(grants) in (1, 2) and sum(grant['Permission'] == 'FULL_CONTROL'
        and grant['Grantee'].get('Type') == 'CanonicalUser'
        and grant['Grantee'].get('ID') == owner['ID'] for grant in grants) == 1)
    if len(grants) == 2:
        _require(client.meta.endpoint_url in _TIGRIS and sum(grant == _ADMINS for grant in grants) == 1)
    return b''.join(chunks)


def persist_retained_cuts(artifact, client, *, bucket):
    """One create-only attempt, private readback before receipt; no Redis grant."""
    try:
        encoded, inputs, files, owner, attempted = _checked(artifact)
        _require(not attempted)
        with _LOCK:
            _ISSUED[artifact] = (encoded, inputs, files, owner, True)
        record = json.loads(encoded)
        _verify_files(record, files)
        _storage_guard(client, bucket)
        prefix = 'recovery/' + record['source']['source_task_id'] + '/retained_cuts/v1/'
        pointers = []
        for row, specs in zip(record['cuts'], inputs):
            identity, data = _file(specs[0]['path'], content=True)
            _require(identity == row['cut'])
            pointers.append(_put(client, bucket, prefix + f"scene-{row['scene_index']:02d}/"
                                 + identity['sha256'] + '.mp4', data, 'video/mp4'))
        _verify_files(record, files)
        manifest = {**record, 'objects': pointers}
        raw = _raw(manifest)
        pointer = _put(client, bucket, prefix + 'manifests/' + _sha(raw) + '.json', raw, 'application/json')
        _verify_files(record, files)
        receipt = {'version': 1, 'kind': 'retained_cut_storage_receipt', 'manifest': pointer,
                   'storage_readback_verified': True, 'admission_anchor_created': False, **_FLAGS}
        with _LOCK:
            _PERSISTED[artifact] = _raw(receipt)
        return receipt
    except RetainedCutEvidenceError:
        raise
    except Exception:
        raise RetainedCutEvidenceError('retained_cut_evidence_unverified') from None
