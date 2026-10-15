"""Offline scene/family transactions; no provider, real Redis or live policy IO."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from unittest.mock import Mock

import fakeredis
import pytest

from app.services.production_spend import (
    LEDGER_KEY, OpeningReservation, SpendBlocked, SpendLedger, SpendPolicy, SpendQuote,
)
from app.services.production_scene_budget import (
    make_scene_plan, scene_fields, validate_scene_descriptor,
)


CHANNEL = 'channel_AAAAA'
ROOT = 'lineage_AAAAA'
CONNECTION = 'connection_AAAAA'
PACKAGE = 'a' * 64
REVISION = 'fixture-reviewed-v1'


def descriptor(*, model='gen4.5', seconds=5, **changes):
    return {
        'provider': 'runway', 'model': model, 'duration_seconds': seconds,
        'aspect_ratio': '9:16', 'resolution': '720p', 'audio': False,
        'sample_count': 1, 'price_revision': REVISION, **changes,
    }


def scene_row(index=0, *, maximum=600_000, total=1_200_000, requests=None):
    return {
        'scene_index': index, 'narration_millis': 4_500, 'generation_seconds': 5,
        'max_request_micro': maximum, 'total_micro': total,
        'allowed_requests': requests if requests is not None else [descriptor()],
    }


def plan_args(**changes):
    return {
        'channel_id': CHANNEL, 'lineage_id': ROOT, 'kind': 'shorts',
        'connection_id': CONNECTION, 'package_sha256': PACKAGE,
        'scenes': [scene_row(), scene_row(1)], **changes,
    }


def admission(index=0, *, request=None, **changes):
    return {
        'connection_id': CONNECTION, 'package_sha256': PACKAGE, 'scene_index': index,
        'descriptor': request if request is not None else descriptor(), **changes,
    }


def reserve(ledger, number=1, *, scene_index=0, **changes):
    args = {
        'request_key': f'request_{number:08}', 'channel_id': CHANNEL,
        'lineage_id': ROOT, 'kind': 'shorts',
        'quote': SpendQuote('runway', 'gen4.5', 600_000, REVISION),
        'scene': admission(scene_index), **changes,
    }
    return ledger.reserve(**args)


def usage(client):
    return json.loads(client.hget(LEDGER_KEY, scene_fields(ROOT)[1]))


@pytest.fixture
def case():
    client = fakeredis.FakeRedis(decode_responses=True)
    clock = [datetime(2026, 9, 9, tzinfo=timezone.utc)]
    # Deliberately generous family/global ceilings: scene checks must decide.
    policy = SpendPolicy(*([10_000_000] * 6))
    ledger = SpendLedger(client, policy, clock=lambda: clock[0])
    ledger.initialize()
    return client, ledger, clock


def test_explicit_freeze_does_not_spend_and_equal_reentry_never_resets(case):
    client, ledger, _ = case
    assert ledger.initialize_scene_plan(**plan_args()) is True
    assert ledger.snapshot()['period']['used_micro'] == 0
    assert client.ttl(LEDGER_KEY) == -1
    receipt = reserve(ledger)
    before = client.dump(LEDGER_KEY)
    assert ledger.initialize_scene_plan(**plan_args()) is False
    assert client.dump(LEDGER_KEY) == before
    assert usage(client)['used_micro'] == {'0': 600_000, '1': 0}
    assert receipt['scene']['scene_index'] == 0
    assert receipt['scene']['plan_sha256'] == usage(client)['plan_sha256']
    assert 'prompt' not in json.dumps(client.hgetall(LEDGER_KEY))


def test_initializer_does_not_bootstrap_missing_ledger(case):
    client, ledger, _ = case
    client.delete(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^spend_not_initialized$'):
        ledger.initialize_scene_plan(**plan_args())
    assert not client.exists(LEDGER_KEY)


def test_missing_plan_blocks_without_any_counter_or_receipt_write(case):
    client, ledger, _ = case
    before = client.dump(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^spend_scene_plan_missing$'):
        reserve(ledger)
    assert client.dump(LEDGER_KEY) == before


def test_zero_parent_budget_keeps_its_original_block_reason_without_scene_plan(case):
    client, _, clock = case
    client.delete(LEDGER_KEY)
    ledger = SpendLedger(client, SpendPolicy(*([0] * 6)), clock=lambda: clock[0])
    ledger.initialize()
    before = client.dump(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^spend_month_limit$'):
        reserve(ledger)
    assert client.dump(LEDGER_KEY) == before


def test_initial_and_repair_requests_share_scene_total(case):
    client, ledger, _ = case
    ledger.initialize_scene_plan(**plan_args())
    sender = Mock()
    for request_id in (1, 2):
        sender(reserve(ledger, request_id))
    before = client.dump(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^spend_scene_total_limit$'):
        sender(reserve(ledger, 3))
    assert sender.call_count == 2
    assert client.dump(LEDGER_KEY) == before
    assert usage(client)['used_micro']['0'] == 1_200_000
    assert ledger.snapshot()['period']['used_micro'] == 1_200_000
    # Another originally frozen slot has its own allowance, never a fresh
    # task/phase budget for the exhausted slot.
    reserve(ledger, 4, scene_index=1)
    assert usage(client)['used_micro'] == {'0': 1_200_000, '1': 600_000}


def test_expensive_fallback_blocked_by_per_call_ceiling_with_family_room(case):
    client, ledger, _ = case
    rows = [scene_row(total=5_000_000, requests=[
        descriptor(), descriptor(model='seedance2_fast')])]
    ledger.initialize_scene_plan(**plan_args(scenes=rows))
    sender = Mock()
    sender(reserve(ledger))
    with pytest.raises(SpendBlocked, match='^spend_scene_request_limit$'):
        sender(reserve(ledger, 2,
                       quote=SpendQuote('runway', 'seedance2_fast', 1_450_000, REVISION),
                       scene=admission(request=descriptor(model='seedance2_fast'))))
    assert sender.call_count == 1
    assert ledger.snapshot()['remaining_micro'] == 9_400_000
    assert usage(client)['used_micro']['0'] == 600_000


@pytest.mark.parametrize('model,amount', [
    ('veo-3.1-fast-generate-preview', 600_000),
    ('veo-3.1-generate-preview', 2_400_000),
])
def test_veo_ladder_stays_within_original_cheaper_quote(case, model, amount):
    client, ledger, _ = case
    lite = descriptor(provider='gemini', model='veo-3.1-lite-generate-preview',
                      seconds=6, audio=True)
    upgrade = {**lite, 'model': model}
    ledger.initialize_scene_plan(**plan_args(scenes=[scene_row(
        maximum=300_000, total=5_000_000, requests=[lite, upgrade])]))
    reserve(ledger, quote=SpendQuote('gemini', lite['model'], 300_000, REVISION),
            scene=admission(request=lite))
    with pytest.raises(SpendBlocked, match='^spend_scene_request_limit$'):
        reserve(ledger, 2, quote=SpendQuote('gemini', model, amount, REVISION),
                scene=admission(request=upgrade))
    assert usage(client)['used_micro']['0'] == 300_000


@pytest.mark.parametrize('change', [
    {'duration_seconds': 6}, {'aspect_ratio': '16:9'},
    {'price_revision': 'unreviewed-v2'}, {'model': 'seedance2_fast'},
])
def test_unplanned_actual_request_shape_cannot_reinterpret_scene(case, change):
    client, ledger, _ = case
    ledger.initialize_scene_plan(**plan_args())
    before = client.dump(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^spend_scene_request_mismatch$'):
        reserve(ledger, scene=admission(request=descriptor(**change)))
    assert client.dump(LEDGER_KEY) == before


@pytest.mark.parametrize('quote', [
    SpendQuote('gemini', 'gen4.5', 1, REVISION),
    SpendQuote('runway', 'seedance2_fast', 1, REVISION),
    SpendQuote('runway', 'gen4.5', 1, 'different-v2'),
])
def test_admission_cannot_launder_a_different_quote(case, quote):
    client, ledger, _ = case
    ledger.initialize_scene_plan(**plan_args())
    before = client.dump(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^spend_scene_quote_mismatch$'):
        reserve(ledger, quote=quote)
    assert client.dump(LEDGER_KEY) == before


@pytest.mark.parametrize('change', [
    {'connection_id': 'connection_BBBBB'}, {'package_sha256': 'b' * 64},
    {'scene_index': 2},
])
def test_changed_scene_binding_cannot_mint_a_new_allowance(case, change):
    client, ledger, _ = case
    ledger.initialize_scene_plan(**plan_args())
    reserve(ledger)
    before = client.dump(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^spend_scene_binding_invalid$'):
        reserve(ledger, 2, scene=admission(**change))
    assert client.dump(LEDGER_KEY) == before


@pytest.mark.parametrize('change', [
    {'connection_id': 'connection_BBBBB'}, {'package_sha256': 'b' * 64},
    {'scenes': [scene_row(total=2_000_000), scene_row(1)]},
    {'scenes': [scene_row(), scene_row(1), scene_row(2)]},
])
def test_plan_cannot_be_changed_before_or_after_spending(case, change):
    client, ledger, _ = case
    ledger.initialize_scene_plan(**plan_args())
    for spent in (False, True):
        if spent:
            reserve(ledger)
        before = client.dump(LEDGER_KEY)
        with pytest.raises(SpendBlocked, match='^spend_scene_plan_mismatch$'):
            ledger.initialize_scene_plan(**plan_args(**change))
        assert client.dump(LEDGER_KEY) == before


def test_frozen_plan_cannot_be_omitted_on_a_video_request(case):
    client, ledger, _ = case
    ledger.initialize_scene_plan(**plan_args())
    before = client.dump(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^spend_scene_context_missing$'):
        reserve(ledger, scene=None)
    assert client.dump(LEDGER_KEY) == before
    # Generic/non-video foundation API remains compatible.
    reserve(ledger, quote=SpendQuote('abacus', 'claude-haiku-4-5', 10, REVISION), scene=None)
    assert usage(client)['used_micro'] == {'0': 0, '1': 0}


@pytest.mark.parametrize('quote', [
    SpendQuote('runway', 'gen4.5', 600_000, REVISION),
    SpendQuote('gemini', 'veo-3.1-lite-generate-preview', 300_000, REVISION),
    SpendQuote('gemini', 'gemini-omni-1.1-flash', 300_000, REVISION),
    SpendQuote('fal', 'historical-video-route', 300_000, REVISION),
])
def test_legacy_video_receipt_prevents_late_zero_usage_plan(case, quote):
    client, ledger, _ = case
    reserve(ledger, quote=quote, scene=None)
    before = client.dump(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^spend_scene_plan_late$'):
        ledger.initialize_scene_plan(**plan_args())
    assert client.dump(LEDGER_KEY) == before


def test_opening_video_import_and_month_rollover_cannot_gain_a_new_scene_pool(case):
    client, ledger, clock = case
    client.delete(LEDGER_KEY)
    ledger.initialize_reconciled(month='2026-09', reconciliation_sha256='c' * 64,
                                reservations=[OpeningReservation(
                                    day='2026-09-09', request_key='request_00000001',
                                    channel_id=CHANNEL, lineage_id=ROOT, kind='shorts',
                                    quote=SpendQuote('runway', 'gen4.5', 600_000, REVISION))])
    clock[0] = datetime(2026, 10, 1, tzinfo=timezone.utc)
    before = client.dump(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^spend_scene_plan_late$'):
        ledger.initialize_scene_plan(**plan_args())
    assert client.dump(LEDGER_KEY) == before


def test_prior_text_spend_does_not_prevent_first_video_plan(case):
    client, ledger, _ = case
    reserve(ledger, scene=None, quote=SpendQuote('abacus', 'claude-haiku-4-5', 100_000, REVISION))
    assert ledger.initialize_scene_plan(**plan_args()) is True
    reserve(ledger, 2)
    assert usage(client)['used_micro']['0'] == 600_000
    assert ledger.snapshot()['period']['used_micro'] == 700_000


def test_scene_total_survives_month_rollover(case):
    client, ledger, clock = case
    ledger.initialize_scene_plan(**plan_args())
    reserve(ledger)
    clock[0] = datetime(2026, 10, 1, tzinfo=timezone.utc)
    reserve(ledger, 2)
    with pytest.raises(SpendBlocked, match='^spend_scene_total_limit$'):
        reserve(ledger, 3)
    assert usage(client)['used_micro']['0'] == 1_200_000
    assert ledger.snapshot()['period']['used_micro'] == 600_000


def test_concurrent_scene_reservations_update_all_counters_atomically(case):
    client, ledger, _ = case
    ledger.initialize_scene_plan(**plan_args())
    def attempt(number):
        try:
            reserve(ledger, number)
            return True
        except SpendBlocked:
            return False
    with ThreadPoolExecutor(max_workers=8) as executor:
        accepted = list(executor.map(attempt, range(20)))
    assert sum(accepted) == 2
    assert usage(client)['used_micro']['0'] == 1_200_000
    assert ledger.snapshot()['period'] == {
        'month': '2026-09', 'used_micro': 1_200_000,
        'days': {'2026-09-09': 1_200_000}, 'channels': {CHANNEL: 1_200_000}}
    family = json.loads(client.hget(LEDGER_KEY, 'lineage:' + hashlib.sha256(ROOT.encode()).hexdigest()))
    assert family['used_micro'] == 1_200_000
    assert len(list(client.hscan_iter(LEDGER_KEY, match='request:*'))) == 2


def lose_replies(client, monkeypatch):
    original = client.pipeline
    def pipeline(*args, **kwargs):
        pipe = original(*args, **kwargs)
        execute = pipe.execute
        def lose_reply(*args, **kwargs):
            execute(*args, **kwargs)
            raise ConnectionError('private connection details')
        pipe.execute = lose_reply
        return pipe
    monkeypatch.setattr(client, 'pipeline', pipeline)
    return original


def test_lost_reservation_reply_keeps_all_debits_and_replay_fence(case, monkeypatch):
    client, ledger, _ = case
    ledger.initialize_scene_plan(**plan_args())
    original = lose_replies(client, monkeypatch)
    sender = Mock()
    with pytest.raises(SpendBlocked, match='^spend_store_unavailable$'):
        sender(reserve(ledger))
    monkeypatch.setattr(client, 'pipeline', original)
    assert sender.call_count == 0
    assert usage(client)['used_micro']['0'] == 600_000
    assert ledger.snapshot()['period']['used_micro'] == 600_000
    assert ledger.initialize_scene_plan(**plan_args()) is False
    with pytest.raises(SpendBlocked, match='^spend_request_already_reserved$'):
        sender(reserve(ledger))
    assert sender.call_count == 0


def test_lost_plan_reply_allows_equal_observation_without_new_money(case, monkeypatch):
    client, ledger, _ = case
    original = lose_replies(client, monkeypatch)
    with pytest.raises(SpendBlocked, match='^spend_store_unavailable$'):
        ledger.initialize_scene_plan(**plan_args())
    monkeypatch.setattr(client, 'pipeline', original)
    assert ledger.initialize_scene_plan(**plan_args()) is False
    assert ledger.snapshot()['period']['used_micro'] == 0
    assert usage(client)['used_micro'] == {'0': 0, '1': 0}


@pytest.mark.parametrize('field_index', [0, 1])
def test_missing_frozen_record_never_reinitializes_spent_scene(case, field_index):
    client, ledger, _ = case
    ledger.initialize_scene_plan(**plan_args())
    reserve(ledger)
    client.hdel(LEDGER_KEY, scene_fields(ROOT)[field_index])
    before = client.dump(LEDGER_KEY)
    with pytest.raises(SpendBlocked):
        reserve(ledger, 2)
    with pytest.raises(SpendBlocked):
        ledger.initialize_scene_plan(**plan_args())
    assert client.dump(LEDGER_KEY) == before


def test_deleted_plan_and_usage_are_not_recreated_over_existing_video(case):
    client, ledger, _ = case
    ledger.initialize_scene_plan(**plan_args())
    reserve(ledger)
    client.hdel(LEDGER_KEY, *scene_fields(ROOT))
    with pytest.raises(SpendBlocked, match='^spend_scene_plan_late$'):
        ledger.initialize_scene_plan(**plan_args())


def test_changed_persisted_plan_is_detected_by_usage_digest(case):
    client, ledger, _ = case
    ledger.initialize_scene_plan(**plan_args())
    plan_field, _ = scene_fields(ROOT)
    plan = json.loads(client.hget(LEDGER_KEY, plan_field))
    plan['scenes'][0]['total_micro'] = 9_000_000
    client.hset(LEDGER_KEY, plan_field, json.dumps(plan))
    before = client.dump(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^spend_scene_state_invalid$'):
        reserve(ledger)
    assert client.dump(LEDGER_KEY) == before


def test_concurrent_legacy_video_prevents_plan_commit(case, monkeypatch):
    client, ledger, _ = case
    original, inserted = client.pipeline, []
    def pipeline(*args, **kwargs):
        pipe = original(*args, **kwargs)
        hscan = pipe.hscan
        def scan_then_legacy_video(*args, **kwargs):
            result = hscan(*args, **kwargs)
            if not inserted:
                inserted.append(True)
                reserve(ledger, scene=None)
            return result
        pipe.hscan = scan_then_legacy_video
        return pipe
    monkeypatch.setattr(client, 'pipeline', pipeline)
    with pytest.raises(SpendBlocked, match='^spend_scene_plan_late$'):
        ledger.initialize_scene_plan(**plan_args())
    assert client.hget(LEDGER_KEY, scene_fields(ROOT)[0]) is None
    assert ledger.snapshot()['period']['used_micro'] == 600_000


def test_concurrent_plan_commit_prevents_unscoped_video_reservation(case, monkeypatch):
    client, ledger, _ = case
    original, inserted = client.pipeline, []
    def pipeline(*args, **kwargs):
        pipe = original(*args, **kwargs)
        hexists = pipe.hexists
        def read_then_freeze(key, field):
            result = hexists(key, field)
            if field == scene_fields(ROOT)[1] and not inserted:
                inserted.append(True)
                ledger.initialize_scene_plan(**plan_args())
            return result
        pipe.hexists = read_then_freeze
        return pipe
    monkeypatch.setattr(client, 'pipeline', pipeline)
    with pytest.raises(SpendBlocked, match='^spend_scene_context_missing$'):
        reserve(ledger, scene=None)
    assert usage(client)['used_micro']['0'] == 0
    assert ledger.snapshot()['period']['used_micro'] == 0


def test_historical_scan_is_bounded_and_never_assumes_empty_tail(case, monkeypatch):
    client, ledger, _ = case
    original, pages = client.pipeline, []
    def pipeline(*args, **kwargs):
        pipe = original(*args, **kwargs)
        def unending_scan(*args, **kwargs):
            pages.append(True)
            return 1, {}
        pipe.hscan = unending_scan
        return pipe
    monkeypatch.setattr(client, 'pipeline', pipeline)
    before = client.dump(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^spend_scene_history_limit$'):
        ledger.initialize_scene_plan(**plan_args())
    assert len(pages) == 128
    assert client.dump(LEDGER_KEY) == before


@pytest.mark.parametrize('field,value', [
    ('narration_millis', float('nan')), ('narration_millis', float('inf')),
    ('narration_millis', True), ('narration_millis', 0),
    ('narration_millis', 7_200_001),
    ('generation_seconds', True), ('generation_seconds', 11),
    ('scene_index', True), ('scene_index', -1),
    ('max_request_micro', True), ('max_request_micro', -1),
    ('total_micro', 599_999), ('allowed_requests', []),
])
def test_invalid_plan_values_never_touch_store(case, field, value):
    client, ledger, _ = case
    row = scene_row()
    row[field] = value
    before = client.dump(LEDGER_KEY)
    with pytest.raises(SpendBlocked):
        ledger.initialize_scene_plan(**plan_args(scenes=[row]))
    assert client.dump(LEDGER_KEY) == before


@pytest.mark.parametrize('changes', [
    {'provider': 'fal'}, {'model': 'gemini-omni-1.1-flash'},
    {'audio': True}, {'sample_count': 2}, {'sample_count': True},
    {'duration_seconds': 5.0}, {'resolution': '1080p'},
    {'prompt': 'private text must not be stored'},
])
def test_only_bounded_reviewed_ttv_shapes_are_admissible(changes):
    with pytest.raises(SpendBlocked):
        validate_scene_descriptor(descriptor(**changes))


def test_veo_plan_binds_actual_rounded_billed_duration():
    request = descriptor(provider='gemini', model='veo-3.1-lite-generate-preview',
                         seconds=6, audio=True)
    args = plan_args(scenes=[scene_row(requests=[request])])
    plan = make_scene_plan(**args)
    assert plan['scenes'][0]['generation_seconds'] == 5
    assert plan['scenes'][0]['allowed_requests'][0]['duration_seconds'] == 6
    args['scenes'][0]['allowed_requests'][0]['duration_seconds'] = 4
    with pytest.raises(SpendBlocked):
        make_scene_plan(**args)
    # Returned plan is detached from the mutable caller-owned inputs.
    assert plan['scenes'][0]['allowed_requests'][0]['duration_seconds'] == 6


def test_long_narration_binding_does_not_expand_billed_video_duration():
    row = scene_row(requests=[descriptor(seconds=10)])
    row.update(narration_millis=7_200_000, generation_seconds=10)
    plan = make_scene_plan(**plan_args(scenes=[row]))
    scene = plan['scenes'][0]
    assert scene['narration_millis'] == 7_200_000
    assert scene['generation_seconds'] == scene['allowed_requests'][0]['duration_seconds'] == 10


def test_duplicate_scene_or_route_cannot_create_ambiguous_authority():
    for rows in ([scene_row(), scene_row()],
                 [scene_row(requests=[descriptor(), descriptor()])]):
        with pytest.raises(SpendBlocked):
            make_scene_plan(**plan_args(scenes=rows))
