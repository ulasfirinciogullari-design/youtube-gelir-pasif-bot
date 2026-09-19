"""Exact corrected retained final → private files and immutable episode-five plan."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import retained_publication_plan as publication
from app.services import retained_delivery_dispatch as delivery
from app.services import retained_production_admission as admission
from test_retained_delivery_dispatch import (
    admitted, corrected_visual, eligible, rejected, qualified, captured, source,
    planning_case, real_media, prepared, frozen_three, completed_probe, wire,
    forbid_live_transport,
)
from test_production_connection_continuity import case as original_case, _json, _dump
from test_retained_review_credential_successor import Intercept


@pytest.fixture
def case():
    # Configure numbering before any source/review commitment is produced.
    box = original_case.__wrapped__()
    box.profile.update(series_id='paranin-arka-yuzu-s1', series_name='Paranın Arka Yüzü',
                       series_total=8, require_thumbnail=False)
    box.client.set(admission.continuity._PROFILE + admission.continuity.CHANNEL_ID, _json(box.profile))
    scope = admission.continuity.CHANNEL_ID + ':' + box.profile['series_id']
    box.client.set(publication.automation.SERIES_COUNTER_PREFIX + scope, '4')
    return box


@pytest.fixture
def executing(admitted, monkeypatch):
    box = admitted
    delivery.dispatch_retained_child(box.permit, lambda **kw: SimpleNamespace(id=kw['task_id']))
    box.execution = delivery.acquire_retained_execution(box.client, box.child, box.manifest_sha)
    forbidden = Mock(side_effect=AssertionError('No generic number allocation or publish callback'))
    monkeypatch.setattr(publication.automation, 'reserve_series_number', forbidden)
    box.forbidden_publication = forbidden
    return box


def prepare(box):
    from test_retained_cut_evidence import BUCKET
    try:
        return publication.prepare_retained_publication(box.execution, box.s3, bucket=BUCKET, workdir=box.render_root)
    except publication.RetainedPublicationPlanError as error:
        locations = []
        current = error
        while current is not None:
            trace = current.__traceback__
            while trace is not None:
                locations.append((trace.tb_frame.f_code.co_name, trace.tb_lineno))
                trace = trace.tb_next
            current = current.__context__
        pytest.fail(f'Actual private plan failed at {locations}', pytrace=False)


def test_original_episode_five_metadata_files_and_quality_commitments(executing, subtests):
    box = executing
    before = _dump(box.client)
    value = prepare(box)
    record = value.record
    plan = record['plan']
    assert plan['source_task_id'] == box.child and plan['title'].endswith('(5/8)')
    assert plan['series'] == {'id': 'paranin-arka-yuzu-s1', 'name': 'Paranın Arka Yüzü', 'number': 5, 'total': 8}
    assert plan['default_language'] == 'tr' and plan['release_mode'] == 'public'
    assert plan['contains_synthetic_media'] is True and plan['require_thumbnail'] is False
    assert plan['quality_snapshot']['admission_manifest_sha256'] == box.manifest_sha
    assert plan['quality_snapshot']['final_aac_independently_listened'] is False
    assert record['upload_started'] is record['publication_complete'] is False
    state = publication._checked(value)
    for name, row in state['files'].items():
        assert publication.render._file(row['path'], row['identity']) == row
        assert row['identity'] == {k: record['artifacts'][name][k] for k in ('sha256', 'size')}
    keys = record['series_keys']
    after = _dump(box.client)
    assert set(after) - set(before) == {publication.PLAN_KEY, keys[1]}
    assert all(after[k] == v for k, v in before.items() if k != keys[0])
    assert box.client.mget(keys[:2]) == ['5', '5']
    assert box.client.exists(*keys[2:]) == 0
    assert all(box.client.pttl(k) == -1 for k in (publication.PLAN_KEY, *keys[:2]))
    assert publication.verify_retained_publication(value) == record
    with pytest.raises(publication.RetainedPublicationPlanError):
        publication.prepare_retained_publication(box.execution, None, bucket='unread', workdir=box.render_root)
    assert _dump(box.client) == after
    for key in (publication.PLAN_KEY, keys[0], keys[1], admission.continuity._AUTH_EPOCH):
        with subtests.test(changed=key):
            box.client.set(key, 'changed')
            with pytest.raises(publication.RetainedPublicationPlanError): publication.verify_retained_publication(value)
            box.client.delete(key)
            box.client.restore(key, 0, after[key])
    with subtests.test(expiry=True):
        box.client.expire(publication.PLAN_KEY, 3600)
        with pytest.raises(publication.RetainedPublicationPlanError): publication.verify_retained_publication(value)
        box.client.persist(publication.PLAN_KEY)
    assert publication.verify_retained_publication(value) == record
    record['plan']['contains_synthetic_media'] = False
    assert value.record['plan']['contains_synthetic_media'] is True
    path = Path(state['files']['video']['path'])
    path.write_bytes(path.read_bytes() + b'changed')
    with pytest.raises(publication.RetainedPublicationPlanError): publication.verify_retained_publication(value)
    assert len(box.wire.calls) == box.sends and box.s3.objects == box.objects
    box.forbidden_publication.assert_not_called()


def test_lost_plan_ack_preserves_number_and_never_prepares_again(executing):
    box = executing
    from test_retained_cut_evidence import BUCKET
    running = delivery._execution(box.execution)
    def lost(commands, reply):
        if commands == ('SET', 'SET', 'SET'):
            raise RuntimeError('PRIVATE plan acknowledgement')
        return reply
    running['client'] = Intercept(box.client, after=lost)
    with pytest.raises(publication.RetainedPublicationPlanError) as error:
        publication.prepare_retained_publication(box.execution, box.s3, bucket=BUCKET, workdir=box.render_root)
    assert 'PRIVATE' not in str(error.value)
    record = json.loads(box.client.get(publication.PLAN_KEY))
    assert box.client.mget(record['series_keys'][:2]) == ['5', '5']
    before = _dump(box.client)
    with pytest.raises(publication.RetainedPublicationPlanError):
        publication.prepare_retained_publication(box.execution, None, bucket='unread', workdir=box.render_root)
    assert _dump(box.client) == before and len(box.wire.calls) == box.sends
    box.forbidden_publication.assert_not_called()


def test_unissued_execution_or_plan_never_reads_files_or_backend(tmp_path):
    for value in (None, {}, object.__new__(delivery.RetainedDeliveryExecution)):
        with pytest.raises(publication.RetainedPublicationPlanError):
            publication.prepare_retained_publication(value, None, bucket='unread', workdir=tmp_path)
    for value in (None, {}, object.__new__(publication.PreparedRetainedPublication)):
        with pytest.raises(publication.RetainedPublicationPlanError): publication.verify_retained_publication(value)
