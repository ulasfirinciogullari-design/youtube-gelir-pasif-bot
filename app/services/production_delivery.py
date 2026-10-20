"""One authored long-form package, three reusable Shorts; no provider calls.

Planning is not a quality approval. The three cuts remain private candidates
until their actual portrait framing and audio have passed publication review.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re


CONTRACT = {'version': 1, 'long_minutes': 8, 'derived_shorts': 3}


class DeliveryPlanError(ValueError):
    pass


def _require(value, message='delivery_plan_invalid'):
    if not value:
        raise DeliveryPlanError(message)


def digest(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        allow_nan=False,
    ).encode('utf-8')).hexdigest()


def delivery_requested(options: dict, duration_minutes: float) -> bool:
    contract = options.get('production_delivery')
    if contract is None:
        return False
    _require(type(contract) is dict and set(contract) == set(CONTRACT)
             and all(type(contract[k]) is int and contract[k] == v for k, v in CONTRACT.items())
             and type(duration_minutes) in (int, float) and duration_minutes == 8
             and options.get('mode') == 'production' and options.get('format') == 'landscape',
             'delivery_contract_invalid')
    return True


def shorts_schema():
    return {
        'type': 'array', 'minItems': 3, 'maxItems': 3,
        'items': {
            'type': 'object', 'additionalProperties': False,
            'properties': {
                'title': {'type': 'string'},
                'description': {'type': 'string'},
                'scene_indices': {
                    'type': 'array', 'minItems': 2, 'maxItems': 8,
                    'items': {'type': 'integer', 'minimum': 0},
                },
            },
            'required': ['title', 'description', 'scene_indices'],
        },
    }


def writer_rule(options: dict, duration_minutes: float, *, select_cuts: bool = True) -> str:
    if not delivery_requested(options, duration_minutes):
        return ''
    rule = (
        'LONG-FORM DELIVERY FAMILY: the eight-minute documentary must also '
        'contain exactly THREE different, self-contained 25-40-second mini-stories. '
        'Integrate them naturally into the main story, not as repeated summaries. '
        'Each starts with its own specific curiosity or surprising action, delivers '
        'the source-supported answer, and ends in a complete sentence. No dangling '
        'pronouns, missing setup, withheld answer, generic subscription screen or '
        'reference to a previous episode. Use contiguous whole scenes, never '
        'mid-sentence cuts; the three scene ranges must not overlap. Aim for '
        '50-75 natural spoken words per mini-story; do not speed up the voice. '
        'Compose moving visuals with the essential subject inside the central '
        'portrait-safe area, without sacrificing the landscape edit. No slide/card '
        'filler. '
    )
    if not select_cuts:
        return rule + 'The final director will select the exact cut ranges; retain the normal research JSON schema.'
    return rule + (
        'Return derived_shorts, an array of exactly three objects containing '
        'title, description and scene_indices (zero-based, contiguous, in order). '
        'Titles and descriptions describe that mini-story alone. Reuse narration '
        'already present in scenes; do not author a second voice track. This '
        'selection is an edit plan, NOT proof that the final cut passed review.'
    )


def bind_delivery_plan(package: dict) -> dict:
    """Bind the authored three cuts to the exact final scene narration."""
    scenes, cuts = package.get('scenes'), package.get('derived_shorts')
    _require(type(scenes) is list and 12 <= len(scenes) <= 70)
    _require(all(type(s) is dict and type(s.get('narration')) is str
                 and bool(s['narration'].strip()) for s in scenes))
    _require(type(cuts) is list and len(cuts) == 3, 'delivery_three_shorts_required')
    rows, used, titles = [], set(), set()
    for number, cut in enumerate(cuts, 1):
        _require(type(cut) is dict and set(cut) == {'title', 'description', 'scene_indices'})
        for field, limit in (('title', 100), ('description', 3000)):
            _require(type(cut[field]) is str and 0 < len(cut[field].strip()) <= limit)
            _require(not any(ord(c) < 32 and c not in '\n\r\t' for c in cut[field]))
        title = cut['title'].strip()
        _require(title.casefold() not in titles, 'delivery_duplicate_short')
        titles.add(title.casefold())
        indices = cut['scene_indices']
        _require(type(indices) is list and 2 <= len(indices) <= 8
                 and all(type(i) is int and 0 <= i < len(scenes) for i in indices))
        _require(indices == list(range(indices[0], indices[-1] + 1))
                 and not used.intersection(indices), 'delivery_short_ranges_invalid')
        used.update(indices)
        narration = ' '.join(scenes[i]['narration'].strip() for i in indices)
        _require(35 <= len(narration.split()) <= 100, 'delivery_short_spoken_length_invalid')
        _require(bool(re.search(r'[.!?…][\"\u201d\u2019\']?$', narration)),
                 'delivery_short_sentence_incomplete')
        rows.append({
            'number': number, 'title': title, 'description': cut['description'].strip(),
            'scene_indices': list(indices), 'narration': narration,
            'narration_sha256': digest([scenes[i]['narration'] for i in indices]),
        })
    return {
        **CONTRACT,
        'scene_narration_sha256': digest([s['narration'] for s in scenes]),
        'shorts': rows,
    }


def rendered_delivery_manifest(task_id: str, package: dict, rendered: dict, *, master_sha256: str) -> dict:
    """Use actual frame boundaries, not estimated script or SRT timestamps."""
    _require(type(task_id) is str and re.fullmatch(r'[0-9a-f-]{36}', task_id))
    _require(type(master_sha256) is str and re.fullmatch(r'[0-9a-f]{64}', master_sha256))
    plan = bind_delivery_plan(package)
    _require(package.get('delivery_plan') == plan, 'delivery_narration_changed')
    windows = rendered.get('scene_windows')
    _require(rendered.get('resolution') == '1920x1080'
             and rendered.get('fps') == 30 and type(rendered.get('fps')) is int
             and rendered.get('scene_synced') is True)
    _require(type(windows) is list and len(windows) == len(package['scenes']))
    cursor = 0
    for index, window in enumerate(windows):
        _require(type(window) is dict and set(window) == {'scene_index', 'start_frame', 'end_frame'}
                 and type(window['scene_index']) is int and window['scene_index'] == index
                 and type(window['start_frame']) is int and window['start_frame'] == cursor
                 and type(window['end_frame']) is int and window['end_frame'] > cursor,
                 'delivery_frame_map_invalid')
        cursor = window['end_frame']
    _require(type(rendered.get('frame_count')) is int and cursor == rendered['frame_count'])
    cuts = []
    for short in plan['shorts']:
        first, last = short['scene_indices'][0], short['scene_indices'][-1]
        start, end = windows[first]['start_frame'], windows[last]['end_frame']
        _require(20 * 30 <= end - start <= 50 * 30, 'delivery_short_duration_invalid')
        cuts.append({**deepcopy(short), 'start_frame': start, 'end_frame': end,
                     'duration_seconds': (end - start) / 30,
                     'status': 'awaiting_portrait_render_and_review'})
    manifest = {
        'version': 1, 'source_task_id': task_id,
        'master_key': f'videos/{task_id}/final.mp4', 'master_sha256': master_sha256,
        'source_frame_count': rendered['frame_count'], 'fps': 30,
        'scene_narration_sha256': plan['scene_narration_sha256'], 'shorts': cuts,
        'new_voice_generations': 0, 'new_video_generations': 0,
        'qa_approved': False, 'publish_eligible': False,
    }
    return {**manifest, 'manifest_sha256': digest(manifest)}


def file_sha256(path) -> str:
    hasher = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(block)
    return hasher.hexdigest()


def persist_delivery_manifest(task_id, package, rendered, options, duration_minutes, work):
    """A derivative/export failure never purchases a second long-form master."""
    if not delivery_requested(options, duration_minutes):
        return {}
    from pathlib import Path
    from app.services.storage import upload_file

    try:
        manifest = rendered_delivery_manifest(
            task_id, package, rendered, master_sha256=file_sha256(rendered['path']),
        )
        key = f'videos/{task_id}/delivery/{manifest["manifest_sha256"]}.json'
        path = Path(work) / 'delivery.json'
        path.write_text(json.dumps(manifest, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        upload_file(path, key, 'application/json')
    except DeliveryPlanError as exc:
        return {'delivery_status': 'blocked', 'delivery_error': str(exc)}
    except Exception:
        return {'delivery_status': 'export_unavailable', 'delivery_error': 'delivery_export_unavailable'}
    return {
        'delivery_status': 'awaiting_portrait_render_and_review',
        'delivery_manifest_key': key, 'delivery_manifest_sha256': manifest['manifest_sha256'],
        'delivery_master_sha256': manifest['master_sha256'], 'delivery_shorts_count': 3,
    }
