"""Bounded reselection from saved stock after an owner-queued visual rejection.

No voice, video generation, stock search, or publication request is made here.
Every candidate is rendered with its actual crop and timing before the ordinary
independent critic sees it. A returned selection still needs full worker QA.
"""
from copy import deepcopy
from pathlib import Path
import re

from app.services import content_plan as plan


def _require(value):
    plan._require(value, 'plan_stock_repair_unverified')


def validate_media(raw, scene_count, package_hash):
    _require(type(raw) is dict and set(raw) == {'version', 'recovery_only', 'source_task_id',
        'package_sha256', 'scenes'} and type(raw['version']) is int and raw['version'] == 7
        and raw['recovery_only'] is True and raw['scenes'] == {} and scene_count == 6
        and type(package_hash) is str and re.fullmatch('[0-9a-f]{64}', package_hash)
        and raw['package_sha256'] == package_hash)
    plan._id(raw['source_task_id'])
    # The existing recovery-only execution path then forbids every paid create.
    # The worker separately requires and verifies the exact private plan record.
    return {**deepcopy(raw), 'version': 3}


def _passes(row, threshold):
    return (type(row) is dict and type(row.get('score')) is int and threshold <= row['score'] <= 100
        and all(row.get(k) is True for k in ('evidence_gate_passed', 'identity_gate_passed', 'editorial_gate_passed')))


def select(package, voice, saved, work, spec):
    from app.services import storage, render, visual_qc
    from app.services.content_plan_recovery import _stored
    from app.services.production_included_router import _LAST_OBSERVED
    from app import tasks
    _require(len(package['scenes']) == len(voice['scene_durations']) == 6)
    threshold = spec['quality_threshold']; _require(type(threshold) is int and 86 <= threshold <= 100)
    objects = storage._client(); candidates = [[] for _ in range(6)]; credits = []
    for phase in ('budget_rescue', 'before_generation', 'initial'):
        record = saved.get(phase)
        if not record:
            continue
        credits.extend(deepcopy(record['credits']))
        for index, rows in enumerate(record['pools']):
            for row in rows:
                if len(candidates[index]) < 6 and row not in candidates[index]:
                    candidates[index].append(deepcopy(row))
    _require(all(candidates))
    local = {}; normalized = {}; observations = []; accepted = {}
    def source(row):
        digest = row['sha256']
        if digest not in local:
            local[digest] = _stored(objects, row['key'], digest, row['size'],
                work / ('saved-stock-' + digest + '.mp4'), 128 * 1024 * 1024)
        return {**row['spec'], 'path': str(local[digest]), 'curated_pinned': True,
                'preserve_start_fraction': True, 'forbid_loop': True}
    initial = [[source(rows[0])] for rows in candidates]
    duration = render.media_duration(voice['path'])
    timeline = render._scene_timeline(package['scenes'], initial, voice['scene_durations'], duration,
                                      [row[0] for row in initial])
    counts = render._timeline_frame_counts(timeline, duration)
    _require(len(timeline) == 6 and [row[3] for row in timeline] == list(range(6)))
    for round in range(3):
        chosen_rows = []; review_inputs = []
        for index, rows in enumerate(candidates):
            if index in accepted:
                choices = [accepted[index]]
            elif round < 2:
                choices = rows[round * 3:(round + 1) * 3] or rows[:3]
            else:
                choices = [{**deepcopy(rows[0]), 'spec': {**rows[0]['spec'], 'start_fraction': fraction}}
                           for fraction in (.06, .5, .94)]
            prepared = []; valid = []
            for row in choices:
                identity = plan._sha({'row': row, 'frames': counts[index]})
                if identity not in normalized:
                    target = work / ('saved-cut-' + identity + '.mp4')
                    try:
                        render.normalize_clip(source(row), target, counts[index] / render.FPS,
                                              index, timeline[index][2], '1080x1920')
                    except RuntimeError:
                        continue  # An invalid candidate can never be selected.
                    normalized[identity] = {**source(row), 'path': str(target), 'start_fraction': 0.0}
                prepared.append(normalized[identity]); valid.append(row)
            _require(prepared)
            chosen_rows.append(valid); review_inputs.append(prepared)
        result = visual_qc.review_scene_visuals(package['scenes'], review_inputs,
            work / f'saved-stock-review-{round}', max_scenes=6, _missing_review_attempts=0,
            topic=spec['topic'], story_scenes=package['scenes'], content_style=spec['content_style'],
            evidence_sources=package['sources'])
        observations.append({'round': round, 'review': result, 'provider_evidence': deepcopy(_LAST_OBSERVED.get())})
        reports = result.get('reviews') or []
        _require(type(reports) is list and len({v['scene_index'] for v in reports}) == len(reports))
        next_accepted = {}; selected_ids = set()
        for review in reports:
            index = review['scene_index']; candidate = review.get('best_candidate_index')
            if not (type(index) is int and 0 <= index < 6 and type(candidate) is int
                    and 0 <= candidate < len(chosen_rows[index]) and _passes(review, threshold)
                    and index not in result.get('missing_review_indices', [])
                    and index not in result.get('unreviewable_scene_indices', [])):
                continue
            row = chosen_rows[index][candidate]
            identity = row['spec']['pexels_id']
            if identity in selected_ids:
                continue
            selected_ids.add(identity); next_accepted[index] = deepcopy(row)
        accepted = next_accepted
        if len(accepted) == 6:
            stocks = {str(index): row for index, row in accepted.items()}
            tasks._require_unique_selected_stock([[source(accepted[i])] for i in range(6)])
            unique_credits = {plan._sha(v): v for v in credits}
            return stocks, list(unique_credits.values()), {'version': 1, 'exact_cut_frames': counts,
                'reviewed_selection_sha256': plan._sha(stocks), 'observations': observations}
    raise RuntimeError('Saved stock candidates did not pass independent exact-cut review')
