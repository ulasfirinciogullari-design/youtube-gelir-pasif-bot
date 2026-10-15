from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4

import fakeredis
import pytest

from app.services import channel_commissioning as grant, production_continuation as continuation
from app.services import framecase_cadence as cadence, content_plan as plan, studio_state as jobs
from app.services import framecase_pipeline as pipeline, framecase_schedule as schedule
from app.services.production_spend import SpendBlocked
from app.services.youtube_auth import CHANNEL_PREFIX

CHANNEL = cadence.CHANNEL_ID
OLD = 'UC5v9AvNtD3PTLgo6m1jROOA'
NOW = datetime(2026, 9, 23, 20, 59, tzinfo=timezone.utc).timestamp()
NEXT = NOW + 120


def source(channel=CHANNEL, kind='shorts'):
    return {'task_id': str(uuid4()), 'kind': 'render', 'parent_id': None,
            'spec': {'production_channel_id': channel, 'format': kind}}


@pytest.fixture
def client():
    return fakeredis.FakeRedis(decode_responses=True)


def activate(client):
    client.set(CHANNEL_PREFIX + CHANNEL, json.dumps({'id': CHANNEL, 'connection_id': 'framecase-connection'}))
    value = {'version': 1, 'channel_id': CHANNEL, 'connection_id': 'framecase-connection',
        'authorized_at': '2026-09-23T00:00:00+00:00', 'owner_evidence_sha256': 'a' * 64}
    grant.initialize(client, value)
    return value


def test_additional_channel_preserves_original_receipts_and_revocation(client):
    legacy = {'version': 1, 'kind': 'continuous_commissioning', 'allowed_channels': [OLD],
        'authorized_at': '2026-09-22T00:00:00+00:00', 'owner_evidence_sha256': 'f' * 64}
    continuation.initialize(client, legacy)
    old = {k: client.get(k) for k in (continuation.AUTHORIZATION_KEY, continuation.ANCHOR_KEY, continuation.ACTIVE_KEY)}
    value = activate(client)
    assert {k: client.get(k) for k in old} == old
    assert grant.initialize(client, value) is False
    with client.pipeline() as p:
        assert continuation.authority(p, OLD) == old[continuation.ANCHOR_KEY]
        proof = continuation.authority(p, CHANNEL)
        assert proof and proof != old[continuation.ANCHOR_KEY]
    client.delete(grant.PREFIX + CHANNEL + ':active')
    with client.pipeline() as p:
        assert continuation.authority(p, CHANNEL) is None
        assert continuation.authority(p, CHANNEL, active=False) == proof
    assert {k: client.get(k) for k in old} == old


@pytest.mark.parametrize('damage', ['connection', 'anchor', 'ttl'])
def test_channel_grant_fails_closed_on_changed_owner_binding(client, damage):
    activate(client)
    if damage == 'connection':
        client.set(CHANNEL_PREFIX + CHANNEL, json.dumps({'id': CHANNEL, 'connection_id': 'different-connection'}))
    elif damage == 'anchor': client.set(grant.PREFIX + CHANNEL + ':anchor', 'wrong')
    else: client.expire(grant.PREFIX + CHANNEL, 100)
    with client.pipeline() as p, pytest.raises(ValueError): continuation.authority(p, CHANNEL)


def test_daily_cap_counts_unfinished_uploads_across_istanbul_midnight(client):
    rows = [source() for _ in range(10)]
    for row in rows: assert cadence.publication_slot(row, client=client, now=NOW)
    assert not cadence.publication_slot(source(), client=client, now=NOW)
    assert not cadence.publication_slot(source(), client=client, now=NEXT)
    assert cadence.publication_slot(rows[0], client=client, now=NEXT)
    cadence.publication_completed(rows[0], client=client, now=NEXT)
    assert not cadence.publication_slot(source(), client=client, now=NEXT)
    snap = cadence.snapshot(CHANNEL, client=client, now=NEXT)
    assert snap['date'] == '2026-09-24' and snap['counts']['published']['shorts'] == 1
    assert snap['counts']['pending']['shorts'] == 9
    assert cadence.publication_slot(source(kind='landscape'), client=client, now=NEXT)
    assert not cadence.publication_slot(source(kind='landscape'), client=client, now=NEXT)


def test_cadence_does_not_limit_the_other_owner_channels(client):
    for _ in range(12): assert cadence.publication_slot(source(OLD), client=client, now=NOW)
    assert list(client.scan_iter()) == []


def test_racing_publishers_cannot_overbook(client):
    from redis.exceptions import WatchError
    def reserve(_):
        try: return cadence.publication_slot(source(), client=client, now=NOW)
        except WatchError: return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted = list(pool.map(reserve, range(24)))
    assert sum(accepted) <= 10
    assert len(client.hgetall(cadence.keys(CHANNEL, now=NOW)[2])) == sum(accepted)


def test_render_reservation_and_publication_share_remaining_day_capacity(client):
    rows = [source() for _ in range(10)]
    for row in rows:
        assert cadence.publication_slot(row, client=client, now=NOW)
        cadence.publication_completed(row, client=client, now=NOW)
    with client.pipeline() as p:
        assert cadence.production_slot(p, CHANNEL, 'shorts', str(uuid4()), now=NOW) is False
        assert cadence.production_slot(p, CHANNEL, 'shorts', str(uuid4()), now=NEXT)


def test_fiction_route_requires_immutable_approved_channel_dispatch(client, monkeypatch):
    monkeypatch.setattr(plan, '_client', lambda: client)
    with pytest.raises(SpendBlocked):
        pipeline.authorize(str(uuid4()), 'Topic', .5, 'en', OLD, {
            'production_channel_id': OLD, 'framecase_animation': True})
    assert not list(client.scan_iter())


def test_original_script_cannot_be_rewritten_or_claim_documentary_status():
    story = json.loads(pipeline.ASSET.read_text())
    episode = story['episodes'][0]
    narration = episode['narration'].split()
    chunks = [narration[:16], narration[16:32], narration[32:47], narration[47:]]
    src = {'episode': episode, 'locked_narration': True}
    candidate = {'title': episode['title'], 'description': 'An original fictional animated mystery.',
        'scenes': [{'narration': ' '.join(chunk), 'ai_prompt': 'Painterly animated Mira examines the same brass watch with a deliberate moving hand.'} for chunk in chunks]}
    assert pipeline.validate_package(deepcopy(candidate), src, longform=False)['narration'] == episode['narration']
    candidate['scenes'][0]['narration'] = candidate['scenes'][0]['narration'].replace('Bellwick', 'London')
    with pytest.raises(SpendBlocked, match='authored_narration'): pipeline.validate_package(candidate, src, longform=False)


def test_animation_never_approves_high_score_with_failed_physical_or_identity_gate():
    from app.tasks import _MANUAL_QA_CLEAR_VISUAL_FIELDS
    row = {'scene_index': 0, 'score': 98, 'best_candidate_index': 0,
        **{k: True for k in ('evidence_gate_passed', 'editorial_gate_passed', 'identity_gate_passed',
                             'subject_visible', 'spoken_action_visible')},
        **{k: False for k in ('unexplained_reset', *_MANUAL_QA_CLEAR_VISUAL_FIELDS)}}
    _, rejected = pipeline.visual_gate({'reviews': [row], 'missing_review_indices': []}, 1)
    assert rejected == []
    row['identity_gate_passed'] = False
    assert pipeline.visual_gate({'reviews': [row]}, 1)[1] == [0]


def test_story_positive_observations_require_a_new_independent_review(monkeypatch):
    from unittest.mock import Mock
    from app.services import production_included_router as router
    ambiguous = {**{k: True for k in pipeline.CHECKS},
                 'findings': ['The episode faithfully presents original fictional animation.']}
    accepted = {**{k: True for k in pipeline.CHECKS}, 'findings': []}
    critic = Mock(side_effect=[deepcopy(ambiguous), deepcopy(accepted)])
    monkeypatch.setattr(router, 'generate_text_json', critic)
    candidate = {'narration': 'The tower rang midnight twice.'}
    result = pipeline.review_package(candidate, {'locked_narration': True}, longform=False)
    assert result == {**accepted, 'clarification_history': [ambiguous]}
    assert candidate == {'narration': 'The tower rang midnight twice.'}
    assert critic.call_count == 2
    assert all(call.kwargs['purpose'] == 'story_review' for call in critic.call_args_list)
    assert 'previous_report' in critic.call_args_list[1].args[0]
    assert 'The tower rang midnight twice.' in critic.call_args_list[1].args[0]


@pytest.mark.parametrize('second_kind', ['ambiguous', 'rejected', 'unknown'])
def test_story_clarification_is_bounded_and_cannot_overrule_rejection(monkeypatch, second_kind):
    from unittest.mock import Mock
    from app.services import production_included_router as router
    ambiguous = {**{k: True for k in pipeline.CHECKS}, 'findings': ['An observation.']}
    rejected = {**ambiguous, 'filmable_consistent_shots': False,
                'findings': ['The clock moves in the wrong direction.']}
    second = {'ambiguous': ambiguous, 'rejected': rejected,
              'unknown': SpendBlocked('commissioning_reasoning_previous_outcome_unknown')}[second_kind]
    critic = Mock(side_effect=[deepcopy(ambiguous), second])
    monkeypatch.setattr(router, 'generate_text_json', critic)
    with pytest.raises(SpendBlocked):
        pipeline.review_package({'narration': 'Locked.'}, {}, longform=False)
    assert critic.call_count == 2
    assert ambiguous['findings'] == ['An observation.']


def test_real_story_rejection_is_not_repeated_to_obtain_approval(monkeypatch):
    from unittest.mock import Mock
    from app.services import production_included_router as router
    rejected = {**{k: True for k in pipeline.CHECKS}, 'coherent_causal_story': False,
                'findings': ['The reveal contradicts the first episode.']}
    critic = Mock(return_value=rejected)
    monkeypatch.setattr(router, 'generate_text_json', critic)
    with pytest.raises(SpendBlocked, match='story_review_rejected'):
        pipeline.review_package({}, {}, longform=False)
    assert critic.call_count == 1


def test_omitted_fiction_notice_is_added_before_critic_without_changing_story(monkeypatch):
    from unittest.mock import Mock
    from app.services import production_included_router as router
    from app.services import framecase_editorial_revision as revision
    monkeypatch.setattr(revision, 'retained_source', lambda _: None)
    story = json.loads(pipeline.ASSET.read_text()); episode = story['episodes'][1]
    source = {'episode': episode, 'arc': story['episodes'], 'locked_narration': True}
    words = episode['narration'].split()
    chunks = [words[:16], words[16:32], words[32:47], words[47:]]
    draft = {'title': episode['title'], 'description': 'An original animated mystery in Bellwick.',
             'scenes': [{'narration': ' '.join(part), 'ai_prompt':
                 'Painterly 2D animation of the clock moving backward while rain falls continuously.',
                 'motion_prompt': 'Mira turns her watch toward the light and studies its hands, then looks up.'}
                 for part in chunks]}
    original = deepcopy(draft)
    monkeypatch.setattr(pipeline, 'story_input', lambda _: source)
    writer = Mock(return_value=draft); monkeypatch.setattr(router, 'generate_text_json', writer)
    def review(candidate, observed_source, *, longform):
        assert candidate['description'].startswith('Original fictional animation by Framecase Stories.')
        assert candidate['title'] == "The Bell Rang Twice | The Clock That Lied"
        assert candidate['narration'] == episode['narration']
        assert candidate['scenes'] == [dict(s, visual_queries=[], transition='cut', pace='balanced')
                                       for s in original['scenes']]
        return {**{k: True for k in pipeline.CHECKS}, 'findings': []}
    monkeypatch.setattr(pipeline, 'review_package', review)
    result = pipeline.prepare_package({'item': {'format': 'animation'}})
    assert result['fiction_review']['findings'] == []
    assert draft == original and writer.call_count == 1


def test_fiction_metadata_keeps_limits_and_does_not_change_noncanonical_titles():
    source = {'episode': {'number': 2, 'title': 'Canonical 2/5'}, 'arc': [{}] * 5}
    value = {'description': 'a' * 4000, 'title': 'The suspect scored 2/5'}
    result = pipeline.draft_public_metadata(value, source)
    assert len(result['description']) == 4000 and result['description'].startswith('Original fictional')
    assert result['title'] == value['title']
    already = {'description': 'Original fiction.', 'title': 'Different'}
    assert pipeline.draft_public_metadata(already, source) == already


def test_successor_is_ordered_five_shorts_then_one_long():
    story = json.loads(pipeline.ASSET.read_text())
    rows = schedule.items(story)
    assert [r['format'] for r in rows] == ['animation'] * 5 + ['long']
    assert rows[0]['depends_on'] == []
    assert all(rows[i]['depends_on'] == [rows[i-1]['id']] for i in range(1, 6))
    assert rows[-1]['series']['total'] == 1


def test_recovery_delivery_is_once_and_never_replays_uncertain_voice(client, monkeypatch):
    from app.services import framecase_recovery as recovery
    from app.production_tasks import continue_framecase_episode
    from unittest.mock import Mock
    monkeypatch.setattr(plan, '_client', lambda: client)
    enqueue = Mock(side_effect=TimeoutError('broker ACK lost'))
    monkeypatch.setattr(continue_framecase_episode, 'apply_async', enqueue)
    row = source(); row.update(state='FAILURE', framecase_failure_code='framecase_RuntimeError')
    row['spec']['framecase_animation'] = True
    client.set(jobs.JOB_PREFIX + row['task_id'], plan._raw(row))
    assert recovery.schedule(row) == 'continuation_dispatch_uncertain'
    assert recovery.schedule(row) == 'continuation_preparing_or_uncertain'
    assert enqueue.call_count == 1
    held = deepcopy(row); held['task_id'] = str(uuid4()); held['framecase_failure_code'] = 'framecase_voice_outcome_unverified'
    client.set(jobs.JOB_PREFIX + held['task_id'], plan._raw(held))
    assert recovery.schedule(held) == 'held_for_verification'
    assert enqueue.call_count == 1


@pytest.mark.parametrize('ready', [False, True])
@pytest.mark.parametrize('code', ['credit_cross_mode_request_conflict',
                                'credit_dispatch_legacy_outcome_unverified'])
def test_native_hold_recovery_requires_positive_unclaimed_preparation(client, monkeypatch, ready, code):
    from app.services import framecase_recovery as recovery, production_credit_dispatch as dispatch
    from app.production_tasks import continue_framecase_episode
    from unittest.mock import Mock
    monkeypatch.setattr(plan, '_client', lambda: client)
    verify = Mock(return_value=ready)
    monkeypatch.setattr(dispatch, 'ready_for_root', verify)
    enqueue = Mock()
    monkeypatch.setattr(continue_framecase_episode, 'apply_async', enqueue)
    row = source(); row.update(state='FAILURE', framecase_failure_code=code, framecase_resume_attempt=3)
    row['spec']['framecase_animation'] = True
    client.set(jobs.JOB_PREFIX + row['task_id'], plan._raw(row))
    assert recovery.schedule(row) == ('continuation_queued' if ready else 'held_for_verification')
    verify.assert_called_once_with(row['task_id'])
    assert enqueue.call_count == int(ready)
    assert json.loads(client.get(jobs.JOB_PREFIX + row['task_id'])) == row
    if ready:
        assert enqueue.call_args.kwargs['args'] == (row['task_id'], 4)
        limited = deepcopy(row); limited['framecase_resume_attempt'] = 6
        client.set(jobs.JOB_PREFIX + row['task_id'], plan._raw(limited))
        assert recovery.schedule(limited) == 'continuation_limit_reached'
        assert enqueue.call_count == 1


@pytest.mark.parametrize('changed_build', [False, True])
def test_verified_presend_new_build_skips_old_backoff_without_new_retry_allowance(client, monkeypatch, changed_build):
    from app.services import framecase_recovery as recovery, production_credit_dispatch as dispatch
    from app.production_tasks import continue_framecase_episode
    from unittest.mock import Mock
    monkeypatch.setattr(plan, '_client', lambda: client)
    monkeypatch.setattr(dispatch, 'ready_for_root', lambda root: True)
    monkeypatch.setenv('RAILWAY_GIT_COMMIT_SHA', 'new' if changed_build else 'old')
    enqueue = Mock(); monkeypatch.setattr(continue_framecase_episode, 'apply_async', enqueue)
    row = source(); row['spec']['framecase_animation'] = True
    row.update(state='FAILURE', framecase_failure_code='credit_cross_mode_request_conflict',
               framecase_failed_build='old', framecase_retry_at=recovery.time.time()+1800,
               framecase_resume_attempt=3)
    client.set(jobs.JOB_PREFIX + row['task_id'], plan._raw(row))
    assert recovery.schedule(row) == ('continuation_queued' if changed_build else 'retry_wait')
    row['framecase_resume_attempt'] = 6
    client.set(jobs.JOB_PREFIX + row['task_id'], plan._raw(row))
    assert recovery.schedule(row) == 'continuation_limit_reached'
    assert enqueue.call_count == int(changed_build)


def test_actual_final_scene_windows_include_end_hold_without_losing_frames(tmp_path):
    import subprocess
    path = tmp_path / 'master.mp4'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=64x96:rate=30',
        '-frames:v', '60', '-c:v', 'libx264', '-threads', '1', str(path)], check=True, capture_output=True)
    output = pipeline.exact_master_scenes({'path': str(path), 'frame_count': 60, 'scene_windows': [
        {'scene_index': 0, 'start_frame': 0, 'end_frame': 27},
        {'scene_index': 1, 'start_frame': 27, 'end_frame': 60}]}, tmp_path, 2)
    from app.services.render import video_frame_count
    assert [video_frame_count(row[0]['path']) for row in output] == [27, 33]
    with pytest.raises(SpendBlocked):
        pipeline.exact_master_scenes({'path': str(path), 'frame_count': 60, 'scene_windows': [
            {'scene_index': 0, 'start_frame': 1, 'end_frame': 60}]}, tmp_path, 1)


def test_fractional_master_review_preserves_all_four_decodable_windows(tmp_path):
    import subprocess
    from app.services.render import video_frame_count, media_duration
    path = tmp_path / 'master.mp4'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
        'testsrc2=size=64x96:rate=30:duration=22', '-f', 'lavfi', '-i',
        'sine=frequency=440:duration=21.76', '-frames:v', '653', '-c:v', 'libx264',
        '-threads', '1', '-c:a', 'aac', str(path)], check=True, capture_output=True)
    before = path.read_bytes()
    boundaries = (0, 188, 318, 510, 653)
    windows = [{'scene_index': i, 'start_frame': a, 'end_frame': b}
               for i, (a, b) in enumerate(zip(boundaries, boundaries[1:]))]
    output = pipeline.exact_master_scenes({'path': str(path), 'frame_count': 653,
        'scene_windows': windows}, tmp_path, 4)
    for row, window in zip(output, windows):
        count = window['end_frame'] - window['start_frame']
        assert video_frame_count(row[0]['path']) == count
        assert media_duration(row[0]['path']) == pytest.approx(count / 30, abs=.001)
    assert path.read_bytes() == before


def test_natural_complete_animation_has_no_forced_silent_padding():
    from app.tasks import _short_preview_voice_duration_qc, _strict_short_preview_render_qc
    story = json.loads(pipeline.ASSET.read_text())
    package = {'narration': story['episodes'][0]['narration']}
    voice = {'duration_after_fit': 21.216}
    target = pipeline.short_edit_target(voice, package)
    assert target == 21.766666666666666
    assert _short_preview_voice_duration_qc(voice, target)['pass'] is True
    assert _strict_short_preview_render_qc({'frame_count': 653,
        'ending_silence_seconds': .69}, target, 21.216)['pass'] is True
    assert not _strict_short_preview_render_qc({'frame_count': 900,
        'ending_silence_seconds': 8.9}, target, 21.216)['pass']
    for seconds in (19, 40, 0, float('nan'), True, '21.216'):
        with pytest.raises(SpendBlocked):
            pipeline.short_edit_target({'duration_after_fit': seconds}, package)
    with pytest.raises(SpendBlocked):
        pipeline.short_edit_target({'duration_after_fit': 20}, {'narration': 'word ' * 70})


@pytest.mark.parametrize('code', ['framecase_audio_timing_rejected', 'framecase_FinalAudioQualityError',
                                  'framecase_timed_edit_infeasible'])
def test_known_immutable_audio_failure_waits_for_actual_code_correction(client, monkeypatch, code):
    from app.services import framecase_recovery as recovery
    from app.production_tasks import continue_framecase_episode
    from unittest.mock import Mock
    monkeypatch.setattr(plan, '_client', lambda: client)
    enqueue = Mock(); monkeypatch.setattr(continue_framecase_episode, 'apply_async', enqueue)
    monkeypatch.setenv('RAILWAY_GIT_COMMIT_SHA', 'old-build')
    row = source(); row.update(state='FAILURE', framecase_failure_code=code,
        framecase_failed_build='old-build', framecase_resume_attempt=3,
        framecase_retry_at=recovery.time.time()+1800)
    row['spec']['framecase_animation'] = True
    client.set(jobs.JOB_PREFIX + row['task_id'], plan._raw(row))
    assert recovery.schedule(row) == 'waiting_for_pipeline_correction'
    enqueue.assert_not_called()
    monkeypatch.setenv('RAILWAY_GIT_COMMIT_SHA', 'fixed-build')
    assert recovery.schedule({**row, 'publication_hold': {'reason': 'owner'}}) == 'held_by_owner'
    enqueue.assert_not_called()
    assert recovery.schedule(row) == 'continuation_queued'
    assert enqueue.call_args.kwargs['args'] == (row['task_id'], 4)
    assert client.get(jobs.JOB_PREFIX + row['task_id']) == plan._raw(row)
    assert recovery.schedule(row) == 'continuation_preparing_or_uncertain'
    assert enqueue.call_count == 1


@pytest.mark.parametrize('previous_attempt', [3, 6])
def test_exact_cut_failure_resumes_original_media_only_after_renderer_correction(client, monkeypatch, previous_attempt):
    from app.services import framecase_recovery as recovery
    from app.production_tasks import continue_framecase_episode
    from unittest.mock import Mock
    monkeypatch.setattr(plan, '_client', lambda: client)
    enqueue = Mock(); monkeypatch.setattr(continue_framecase_episode, 'apply_async', enqueue)
    row = source(); row['spec']['framecase_animation'] = True
    row.update(state='FAILURE', framecase_failure_code='framecase_RuntimeError',
        framecase_failure_trace=[{'function': 'run'}, {'function': '_execute'},
            {'function': 'render_video'}, {'function': 'normalize_clip'}],
        framecase_failed_build='broken-renderer', framecase_resume_attempt=previous_attempt,
        framecase_retry_at=recovery.time.time() + 1800)
    raw = plan._raw(row); client.set(jobs.JOB_PREFIX + row['task_id'], raw)
    monkeypatch.setenv('RAILWAY_GIT_COMMIT_SHA', 'broken-renderer')
    assert recovery.schedule(row) == 'waiting_for_pipeline_correction'
    monkeypatch.setenv('RAILWAY_GIT_COMMIT_SHA', 'corrected-renderer')
    assert recovery.schedule({**row, 'publication_hold': {'reason': 'owner'}}) == 'held_by_owner'
    enqueue.assert_not_called()
    assert recovery.schedule(row) == 'continuation_queued'
    assert enqueue.call_args.kwargs['args'] == (row['task_id'], previous_attempt + 1)
    record = json.loads(client.get(recovery.PREFIX + enqueue.call_args.kwargs['task_id']))
    assert record['source_task_id'] == row['task_id']
    if previous_attempt == 6:
        assert record['correction'] == {'failure_code': 'framecase_RuntimeError',
            'failed_build': 'broken-renderer', 'corrected_build': 'corrected-renderer'}
        monkeypatch.setattr(jobs, 'get_job', lambda _: row)
        monkeypatch.setenv('RAILWAY_GIT_COMMIT_SHA', 'unrelated-build')
        with pytest.raises(plan.ContentPlanError, match='recovery_unverified'):
            recovery.run(SimpleNamespace(request=SimpleNamespace(id=record['operation'])), row['task_id'], 7)
        assert not client.exists(recovery.PREFIX + 'execution:' + record['operation'])
    assert client.get(jobs.JOB_PREFIX + row['task_id']) == raw
    assert enqueue.call_count == 1


@pytest.mark.parametrize('trace', [[], [{'function': 'normalize_clip'}],
    [{'function': 'render_video'}, {'function': 'download'}], 'invalid', [None, {}]])
def test_unrelated_runtime_failure_keeps_transient_backoff(client, monkeypatch, trace):
    from app.services import framecase_recovery as recovery
    from app.production_tasks import continue_framecase_episode
    from unittest.mock import Mock
    monkeypatch.setattr(plan, '_client', lambda: client)
    enqueue = Mock(); monkeypatch.setattr(continue_framecase_episode, 'apply_async', enqueue)
    monkeypatch.setenv('RAILWAY_GIT_COMMIT_SHA', 'different-build')
    row = source(); row['spec']['framecase_animation'] = True
    row.update(state='FAILURE', framecase_failure_code='framecase_RuntimeError',
        framecase_failure_trace=trace, framecase_failed_build='old-build',
        framecase_retry_at=recovery.time.time() + 1800)
    assert recovery.schedule(row) == 'retry_wait'
    enqueue.assert_not_called()


@pytest.mark.parametrize('failure_code', ['framecase_review_window_invalid', 'framecase_visual_quality_exhausted'])
def test_extra_continuations_require_a_new_build_and_keep_original_root(client, monkeypatch, failure_code):
    from app.services import framecase_recovery as recovery
    from app.production_tasks import continue_framecase_episode
    from unittest.mock import Mock
    monkeypatch.setattr(plan, '_client', lambda: client)
    enqueue = Mock(); monkeypatch.setattr(continue_framecase_episode, 'apply_async', enqueue)
    row = source(); row['spec']['framecase_animation'] = True
    row.update(state='FAILURE', framecase_resume_attempt=6,
        framecase_failure_code=failure_code, framecase_failed_build='old')
    client.set(jobs.JOB_PREFIX + row['task_id'], plan._raw(row))
    monkeypatch.setenv('RAILWAY_GIT_COMMIT_SHA', 'old')
    assert recovery.schedule(row) == 'waiting_for_pipeline_correction'
    monkeypatch.setenv('RAILWAY_GIT_COMMIT_SHA', 'corrected')
    transient = {**row, 'framecase_failure_code': 'framecase_RuntimeError'}
    assert recovery.schedule(transient) == 'continuation_limit_reached'
    enqueue.assert_not_called()
    assert recovery.schedule(row) == 'continuation_queued'
    assert enqueue.call_args.kwargs['args'] == (row['task_id'], 7)
    record = json.loads(client.get(recovery.PREFIX + enqueue.call_args.kwargs['task_id']))
    assert record['correction'] == {'failure_code': failure_code,
        'failed_build': 'old', 'corrected_build': 'corrected'}
    assert client.get(jobs.JOB_PREFIX + row['task_id']) == plan._raw(row)
    assert recovery.schedule({**row, 'framecase_resume_attempt': 12}) == 'continuation_limit_reached'
    # A queued correction cannot execute on an unrelated deployment or acquire
    # a fresh provider/daily budget by moving to another source.
    monkeypatch.setattr(jobs, 'get_job', lambda _: row)
    monkeypatch.setenv('RAILWAY_GIT_COMMIT_SHA', 'unrelated-build')
    with pytest.raises(plan.ContentPlanError, match='recovery_unverified'):
        recovery.run(SimpleNamespace(request=SimpleNamespace(id=record['operation'])), row['task_id'], 7)
    assert not client.exists(recovery.PREFIX + 'execution:' + record['operation'])


@pytest.mark.parametrize('nominal,measured,count', [(30, 21.216, 4), (30, 27.024, 4),
                                                  (180, 148.1, 30), (180, 190.0, 30)])
def test_animation_natural_timeline_preserves_audio_and_scene_cues(tmp_path, monkeypatch, nominal, measured, count):
    from app.services import voice
    from unittest.mock import Mock
    path = tmp_path / 'voice.mp3'; path.write_bytes(b'unchanged paid response')
    durations = [measured / count] * count
    monkeypatch.setattr(voice, '_media_duration', lambda _: measured)
    ffmpeg = Mock(side_effect=AssertionError('Natural narration must not be stretched'))
    monkeypatch.setattr(voice.subprocess, 'run', ffmpeg)
    result = voice._fit_duration(path, durations, nominal, natural_timeline=True)
    assert result == (durations, measured, measured, 1.0)
    assert path.read_bytes() == b'unchanged paid response'
    ffmpeg.assert_not_called()


def test_long_animation_has_bounded_complete_spoken_edit():
    package = {'narration': 'word ' * 420}
    assert pipeline.long_edit_target({'duration_after_fit': 150}, package) == 150.8
    for seconds in (119, 240, True, float('inf')):
        with pytest.raises(SpendBlocked):
            pipeline.long_edit_target({'duration_after_fit': seconds}, package)
    with pytest.raises(SpendBlocked):
        pipeline.long_edit_target({'duration_after_fit': 120}, package)
    with pytest.raises(SpendBlocked):
        pipeline.long_edit_target({'duration_after_fit': 180}, {'narration': 'word ' * 70})


def test_successor_repairs_real_editorial_findings_and_requires_fresh_review(monkeypatch):
    from app.services import production_included_router as router
    from unittest.mock import Mock
    story = json.loads(pipeline.ASSET.read_text())
    valid = {k: deepcopy(story[k]) for k in ('series_name', 'visual_bible', 'episodes')}
    valid['visual_bible'] = ('Adult Mira Vale wears a cream raincoat and has dark wavy shoulder-length hair. '
        'Painterly 2D navy, amber, ivory and teal; consistent brass watch, no generated text.')
    rejected = {k: True for k in pipeline.CHECKS}; rejected['coherent_causal_story'] = False
    accepted = {k: True for k in pipeline.CHECKS}
    writer = Mock(side_effect=[deepcopy(valid), rejected, deepcopy(valid), accepted])
    monkeypatch.setattr(router, 'generate_text_json', writer)
    candidate, review, history = schedule.reviewed_story(['Previous completed case'])
    assert review == accepted and candidate == valid
    assert len(history) == 2 and history[0]['findings'] == ['coherent_causal_story']
    assert 'coherent_causal_story' in writer.call_args_list[2].args[0]
    assert [v.kwargs['purpose'] for v in writer.call_args_list] == ['editorial', 'story_review'] * 2
    writer.reset_mock(side_effect=True)
    writer.side_effect = [deepcopy(valid), rejected] * 3
    with pytest.raises(plan.ContentPlanError, match='successor_story_rejected'):
        schedule.reviewed_story(['Previous completed case'])
    assert writer.call_count == 6
