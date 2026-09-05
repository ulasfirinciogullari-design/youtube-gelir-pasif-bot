"""Best-effort private diagnostics before paid-allocation failure and cleanup.

No provider, reviewer, renderer, or publication task is called. Resampled stock
frames illustrate the selected clips; they are not the original reviewer input
bytes, an approval, or media that any recovery path may reuse for rendering.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import math
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
from uuid import UUID

from app.services.storage import upload_file


MAX_SCENES = 12
MAX_FRAME_BYTES = 180 * 1024
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
MAX_CLIP_BYTES = 512 * 1024 * 1024
_WORK_ROOT = Path('/tmp/youtube_factory')
_FRAME_BUDGET_SECONDS = 35.0
# Same editorial sample positions as visual_qc.MOMENT_FRACTIONS. These are
# resampled from the selected clip, never inferred from remapped QC filenames.
_MOMENT_FRACTIONS = (0.18, 0.50, 0.82, 0.06, 0.94)
_PRIVATE_TEXT = re.compile(
    r'(?:https?|ftp|s3|file)://|www\.|\bdata:|\bBearer\s+\S+'
    r'|\b(?:sk-[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,})'
    r'|\b(?:api[_ -]?key|authorization|access[_ -]?token|refresh[_ -]?token|'
    r'client[_ -]?secret|password|X-Amz-[A-Za-z-]+)\s*[:=]'
    r'|\b[A-Za-z0-9_-]{40,}\b|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}'
    r'|(?:^|\s)(?:[A-Za-z]:[\\/]|/(?:tmp|var|home|etc|app|Users|run|proc)(?:/|\b))',
    re.IGNORECASE,
)
_BOOLEAN_GATES = (
    'evidence_gate_passed', 'editorial_gate_passed', 'identity_gate_passed',
    'subject_visible', 'spoken_action_visible', 'thermal_claim_applicable',
    'thermal_evidence_visible', 'physical_causality_applicable', 'target_contact_visible',
    'connection_action_applicable', 'moving_connector_visible', 'receiving_interface_visible',
    'connector_visibly_joins_target', 'connection_persists_after_release',
    'state_change_applicable', 'state_changed_after_action', 'final_state_persists',
    'unexplained_reset', 'location_continuity_applicable', 'location_continuity_matches',
    'recurring_identity_continuity_applicable', 'recurring_identity_continuity_matches',
    'prominent_readable_text_or_logo_visible', 'major_visual_artifact_visible',
    'effectively_static_or_frozen', 'substantially_repeats_adjacent_scene',
    'authored_identity_or_material_conflict_visible', 'manufactured_object_cues_visible',
    'manufactured_replica_required', 'open_air_cooling_temporal_required',
    'cooling_temporal_evidence_explained', 'cooling_temporal_moment_coverage_passed',
)
_ALLOCATION_NUMBERS = (
    'paid_create_cap', 'paid_create_used', 'paid_slots_remaining',
    'required_paid_scenes', 'quality_threshold',
)
_ALLOCATION_INDICES = (
    'selected_paid_scene_indices', 'overflow_scene_indices', 'failed_stock_scene_indices',
)


def _text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ''
    # Drop a suspicious field entirely; partial URL/token redaction can leak
    # the remainder of a credential. No arbitrary dictionaries are serialized.
    if len(value) > 10000 or _PRIVATE_TEXT.search(value):
        return '[redacted]'
    return ' '.join(re.sub(r'[\x00-\x1f\x7f]', ' ', value).split())[:limit]


def _queries(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value[:3] if (text := _text(item, 160))]


def _number(value: object, low: float, high: float) -> int | float | None:
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        return None
    return value


def _indices(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    return sorted({item for item in value[:256] if type(item) is int and 0 <= item < 256})[:MAX_SCENES]


def _review(review: dict) -> dict:
    result = {'reason': _text(review.get('reason'), 600),
              'retry_queries': _queries(review.get('retry_queries'))}
    for field in ('score', 'raw_score'):
        result[field] = _number(review.get(field), -1, 100)
    for field in ('best_candidate_index', 'best_moment_index'):
        value = review.get(field)
        result[field] = value if type(value) is int and 0 <= value < 256 else None
    result['best_start_fraction'] = _number(review.get('best_start_fraction'), 0, 1)
    result['evidence_moment_indices'] = [index for index in _indices(review.get('evidence_moment_indices')) if index < 5]
    result['gates'] = {field: review[field] for field in _BOOLEAN_GATES if type(review.get(field)) is bool}
    return result


def _work_path(task_id: str, work_dir: str | Path) -> Path:
    if not isinstance(work_dir, (str, Path)) or '://' in str(work_dir):
        raise ValueError('Invalid work directory')
    path = Path(work_dir)
    root = _WORK_ROOT.absolute()
    relative = path.relative_to(root)
    if (not path.is_absolute() or len(relative.parts) != 1 or '..' in path.parts
            or re.fullmatch(re.escape(task_id) + r'_attempt_[0-9]{1,2}', relative.name) is None):
        raise ValueError('Invalid task directory')
    resolved = path.resolve(strict=True)
    if resolved != root.resolve(strict=True) / relative or not resolved.is_dir():
        raise ValueError('Invalid task directory')
    return resolved


def _stock_path(spec: object, work: Path) -> Path | None:
    if (not isinstance(spec, dict) or spec.get('generated') is True
            or spec.get('source_type') != 'stock' or spec.get('stock_provider') != 'pexels'
            or spec.get('generation_provider')
            or (spec.get('forbid_loop') is True and spec.get('preserve_start_fraction') is True)):
        return None
    raw = spec.get('path')
    if not isinstance(raw, (str, Path)) or '://' in str(raw):
        return None
    path = Path(raw)
    if not path.is_absolute() or '..' in path.parts or path.suffix.lower() != '.mp4':
        return None
    try:
        relative = path.relative_to(work)
        resolved = path.resolve(strict=True)
        size = resolved.stat().st_size
        if (resolved != work / relative or not stat.S_ISREG(resolved.stat().st_mode)
                or not 12 <= size <= MAX_CLIP_BYTES):
            return None
        with resolved.open('rb') as incoming:
            header = incoming.read(12)
        if header[4:8] != b'ftyp':
            return None
        return resolved
    except (OSError, ValueError):
        return None


def _selected(specs: object, review: dict) -> tuple[int | None, object]:
    if not isinstance(specs, list) or not 1 <= len(specs) <= 24:
        return None, None
    candidates = []
    for index, spec in enumerate(specs):
        raw = spec.get('path') if isinstance(spec, dict) else spec
        if not isinstance(raw, (str, Path)):
            return None, None
        if str(raw):
            candidates.append((index, spec))
    if len(candidates) == 1:
        # _apply_visual_review already collapses the selection to index zero;
        # review.best_candidate_index may still describe the original pool.
        return candidates[0]
    index = review.get('best_candidate_index')
    if type(index) is int and 0 <= index < len(candidates):
        return candidates[index]
    return None, None


def _sample(review: dict) -> tuple[float, str]:
    moment = review.get('best_moment_index')
    if type(moment) is int and 0 <= moment < len(_MOMENT_FRACTIONS):
        return _MOMENT_FRACTIONS[moment], 'reviewed_moment_resample'
    fraction = _number(review.get('best_start_fraction'), 0, 1)
    if fraction is not None:
        return float(fraction), 'reviewed_fraction_resample'
    return 0.5, 'selected_clip_midpoint_resample'


def _extract_frame(path: Path, output: Path, fraction: float, timeout: float) -> bytes | None:
    """Same single-frame convention as visual_qc._frame, with no network protocols."""
    try:
        started = time.monotonic()
        duration = float(subprocess.check_output([
            'ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
            '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', str(path),
        ], text=True, stderr=subprocess.DEVNULL, timeout=min(3.0, timeout)).strip())
        if not math.isfinite(duration) or not 0 < duration <= 3600:
            return None
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            return None
        subprocess.run([
            'ffmpeg', '-y', '-v', 'error', '-protocol_whitelist', 'file,pipe',
            '-ss', f'{duration * fraction:.3f}', '-threads', '1', '-i', str(path),
            '-frames:v', '1', '-map_metadata', '-1', '-filter_threads', '1',
            '-vf', 'scale=640:640:force_original_aspect_ratio=decrease', '-q:v', '8', str(output),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=min(6.0, remaining))
        with output.open('rb') as incoming:
            frame = incoming.read(MAX_FRAME_BYTES + 1)
        if 128 <= len(frame) <= MAX_FRAME_BYTES and frame.startswith(b'\xff\xd8\xff') and frame.endswith(b'\xff\xd9'):
            return frame
    except Exception:
        pass
    return None


def _page(metadata: dict) -> bytes:
    cards = []
    for item in metadata['scenes']:
        review = item['review']
        frame = item.get('frame')
        picture = (f'<img alt="Seçili stok klipten tanı karesi" src="data:image/jpeg;base64,{frame["jpeg_base64"]}">'
                   if frame else '<p class="muted">Tanı karesi alınamadı.</p>')
        details = json.dumps({'review': review, 'selection': item['selection']}, ensure_ascii=False, indent=2)
        cards.append(
            f'<article><h2>Sahne {item["scene_index"] + 1} · Puan {html.escape(str(review["score"]))}</h2>'
            f'<p>{html.escape(item["narration"])}</p>{picture}'
            f'<p>{html.escape(review["reason"])}</p>'
            '<details><summary>Değerlendirme ve arama ayrıntıları</summary><pre>'
            f'{html.escape(details)}</pre></details></article>'
        )
    page = (
        '<!doctype html><html lang="tr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="referrer" content="no-referrer">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; '
        'style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
        '<title>Görsel seçim tanısı — özel kayıt</title><style>'
        'body{font:16px/1.5 system-ui;margin:24px auto;max-width:1080px;padding:0 16px;background:#f3f5f8;color:#17212d}'
        'main{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px}'
        'article{background:white;border:1px solid #dce2e8;border-radius:12px;padding:18px}'
        'h1{font-size:26px}h2{font-size:18px}img{width:100%;height:280px;object-fit:contain;background:#17212d}'
        'pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}.muted{color:#536475}'
        '</style></head><body><h1>Görsel seçim tanısı</h1>'
        '<p>Özel tanı kaydı. Kareler seçili stok kliplerden yeniden örneklenmiştir; özgün model girdisi değildir. '
        'Bu kayıt kalite onayı vermez ve videoda yeniden kullanılamaz.</p>'
        f'<p class="muted">{metadata["frame_count"]}/{len(metadata["scenes"])} sahne karesi korundu.</p>'
        '<main>' + ''.join(cards) + '</main></body></html>'
    )
    return page.encode('utf-8')


def persist_visual_allocation_checkpoint(
    task_id: str,
    scenes: list[dict],
    scene_visuals: list[list[dict]],
    reviews: list[dict] | dict[int, dict],
    work_dir: str | Path,
    *,
    quality_threshold: int = 86,
    allocation: dict | None = None,
) -> dict:
    """Persist at most twelve selected stock frames and sanitized diagnostic data.

    Returns a job-field patch containing only fixed status, counts, hashes and
    private object keys. Uses the existing project's Storage configuration;
    never requests a public ACL or creates/returns a signed viewing URL. Any
    failure returns a fixed unavailable marker and cannot affect QA decisions.
    """
    unavailable = {'visual_allocation_checkpoint': {'version': 1, 'status': 'unavailable'}}
    try:
        if (not isinstance(task_id, str) or str(UUID(task_id)) != task_id
                or not isinstance(scenes, list) or not 1 <= len(scenes) <= 256
                or not isinstance(scene_visuals, list) or len(scene_visuals) != len(scenes)
                or not isinstance(reviews, (dict, list)) or not 1 <= len(reviews) <= 256
                or type(quality_threshold) is not int or not 0 <= quality_threshold <= 100):
            return unavailable
        work = _work_path(task_id, work_dir)
        ordered = {}
        entries = reviews.items() if isinstance(reviews, dict) else ((None, value) for value in reviews)
        for key, review in entries:
            if not isinstance(review, dict):
                return unavailable
            index = review.get('scene_index')
            if (type(index) is not int or not 0 <= index < len(scenes) or index in ordered
                    or (key is not None and (type(key) is not int or key != index))):
                return unavailable
            ordered[index] = review
        selected_indices = sorted(ordered)[:MAX_SCENES]
        metadata = {
            'version': 1, 'status': 'diagnostic_only', 'qa_approved': False, 'reusable_for_render': False,
            'source_task_id': task_id, 'quality_threshold': quality_threshold,
            'reviewed_scene_count': len(ordered), 'omitted_scene_count': len(ordered) - len(selected_indices),
            'frame_count': 0, 'scenes': [], 'allocation': {},
        }
        if isinstance(allocation, dict):
            for field in _ALLOCATION_NUMBERS:
                number = _number(allocation.get(field), 0, 256)
                if number is not None:
                    metadata['allocation'][field] = number
            for field in _ALLOCATION_INDICES:
                if field in allocation:
                    metadata['allocation'][field] = _indices(allocation[field])
        deadline = time.monotonic() + _FRAME_BUDGET_SECONDS
        with tempfile.TemporaryDirectory(prefix='visual_allocation_', dir=work) as temporary:
            temporary = Path(temporary)
            for index in selected_indices:
                review = ordered[index]
                scene = scenes[index] if isinstance(scenes[index], dict) else {}
                selected_index, spec = _selected(scene_visuals[index], review)
                fraction, basis = _sample(review)
                item = {
                    'scene_index': index, 'narration': _text(scene.get('narration'), 1200),
                    'visual_queries': _queries(scene.get('visual_queries')), 'review': _review(review),
                    'selection': {'selected_spec_index': selected_index,
                                  'sample_fraction': fraction, 'sample_basis': basis,
                                  'frame_status': 'unavailable'},
                }
                path = _stock_path(spec, work)
                remaining = deadline - time.monotonic()
                frame = (_extract_frame(path, temporary / f'scene_{index:03d}.jpg', fraction, remaining)
                         if path is not None and remaining > 0 else None)
                if (isinstance(frame, bytes) and 128 <= len(frame) <= MAX_FRAME_BYTES
                        and frame.startswith(b'\xff\xd8\xff') and frame.endswith(b'\xff\xd9')):
                    item['frame'] = {'mime_type': 'image/jpeg', 'sha256': hashlib.sha256(frame).hexdigest(),
                                     'size': len(frame), 'jpeg_base64': base64.b64encode(frame).decode('ascii')}
                    item['selection']['frame_status'] = 'diagnostic_resample'
                    metadata['frame_count'] += 1
                metadata['scenes'].append(item)
            manifest = json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
            page = _page(metadata)
            if max(len(manifest), len(page)) > MAX_ARTIFACT_BYTES:
                return unavailable
            manifest_hash, page_hash = hashlib.sha256(manifest).hexdigest(), hashlib.sha256(page).hexdigest()
            prefix = f'diagnostics/visual_allocation/{task_id}'
            manifest_key = f'{prefix}/metadata-{manifest_hash}.json'
            page_key = f'{prefix}/review-{page_hash}.html'
            manifest_path, page_path = temporary / 'metadata.json', temporary / 'review.html'
            manifest_path.write_bytes(manifest)
            page_path.write_bytes(page)
            # Storage return values may contain URLs or provider internals;
            # discard them. No ACL, signing, or settings changes are made here.
            upload_file(manifest_path, manifest_key, 'application/json')
            upload_file(page_path, page_key, 'text/html; charset=utf-8')
        return {'visual_allocation_checkpoint': {
            'version': 1, 'status': 'diagnostic_only', 'qa_approved': False, 'reusable_for_render': False,
            'metadata_key': manifest_key, 'html_key': page_key,
            'metadata_sha256': manifest_hash, 'html_sha256': page_hash,
            'scene_count': len(metadata['scenes']), 'frame_count': metadata['frame_count'],
        }}
    except Exception:
        return unavailable
