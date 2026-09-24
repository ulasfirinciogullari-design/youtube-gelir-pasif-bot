"""Gradual early drop-off remains visible without inventing a winning format."""
from copy import deepcopy
import pytest

from app.services import youtube_analytics as analytics, audience_strategy as strategy

CHANNEL = 'UCgvESYtYbn2w9R2ExBOF_cw'


@pytest.fixture
def report(monkeypatch):
    # No adjacent sample falls by 0.10, but ten consecutive samples lose .40.
    points = [[i / 100, 1.2 if i <= 15 else max(.4, 1.2 - (i - 15) * .04)] for i in range(1, 101)]
    row = {'title': 'Observed video', 'content_type': 'SHORTS', 'views': 1200, 'engagedViews': 600,
        'averageViewDuration': 16, 'averageViewPercentage': 55, 'retention': points}
    value = {'channel_id': CHANNEL, 'status': 'fresh', 'videos': {'one': row}}
    monkeypatch.setattr(analytics, 'dashboard', lambda: {'channels': [value]})
    return value


def test_gradual_drop_in_single_video_is_descriptive_without_cross_video_ranking(report):
    assert analytics.editorial_guidance(CHANNEL) is None
    advice = analytics.pacing_guidance(CHANNEL)
    assert advice['sample_size'] == 1 and 'higher_retention_examples' not in advice
    drop = advice['observed_drop_examples'][0]
    assert drop['watch_ratio_drop'] == .4 and drop['to_fraction'] - drop['from_fraction'] == pytest.approx(.1)
    assert 'not a ranking' in advice['instruction']
    assert report['videos']['one']['retention'][0][1] == 1.2


def test_writer_receives_measured_window_without_extra_model_request(report, monkeypatch):
    monkeypatch.setattr(strategy, 'read_settings', lambda channel: strategy.DEFAULT)
    prompt = strategy.writer_rule(CHANNEL, .5)
    assert 'watch_ratio_drop' in prompt and 'descriptive_same_video' in prompt
    assert 'preserve the requested duration' in prompt


@pytest.mark.parametrize('damage', ['stale', 'unavailable', 'low_sample', 'different_format', 'no_curve', 'sparse', 'flat'])
def test_missing_or_incomparable_data_never_generates_pacing_advice(report, damage):
    row = report['videos']['one']
    if damage in {'stale', 'unavailable'}: report['status'] = damage
    if damage == 'low_sample': row['engagedViews'] = 99
    if damage == 'different_format': row['content_type'] = 'VIDEO_ON_DEMAND'
    if damage == 'no_curve': row.pop('retention')
    if damage == 'sparse': row['retention'] = [[.01, 1.2], [.5, .5], [1, .1]]
    if damage == 'flat': row['retention'] = [[i / 100, .9] for i in range(1, 101)]
    assert analytics.pacing_guidance(CHANNEL) is None


def test_three_same_format_examples_keep_rank_and_use_windowed_drop(report):
    report['videos'].update(two=deepcopy(report['videos']['one']), three=deepcopy(report['videos']['one']))
    advice = analytics.editorial_guidance(CHANNEL)
    assert len(advice['higher_retention_examples']) == 2
    assert len(advice['observed_drop_examples']) == 3
    assert all(row['watch_ratio_drop'] == .4 for row in advice['observed_drop_examples'])
