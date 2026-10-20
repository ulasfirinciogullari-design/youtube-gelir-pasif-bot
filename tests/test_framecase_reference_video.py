from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from app.services import framecase_reference_video as reference
from app.services.production_spend import SpendBlocked

IMAGE = b'\x89PNG\r\n\x1a\n' + b'original-animation-art' * 60
PROMPT = 'The adult investigator looks from her watch to the empty picture frame, preserving the drawn identity.'
REQUEST = '123e4567-e89b-12d3-a456-426614174000'


def test_reference_contract_binds_picture_prompt_price_and_safety_defaults():
    contract = reference.contract_for(IMAGE, PROMPT)
    assert contract['max_list_cost_micro_usd'] == 640000
    assert contract != reference.contract_for(IMAGE + b'changed', PROMPT)
    assert contract != reference.contract_for(IMAGE, PROMPT + ' Different.')
    body = reference.body_for(IMAGE, PROMPT)
    assert body['generate_audio'] is True and body['auto_fix'] is False
    assert 'safety_tolerance' not in body and body['resolution'] == '1080p'
    assert body['duration'] == '8s'


@pytest.mark.parametrize('suffix', ['', '/status'])
def test_queue_urls_remain_on_the_exact_accepted_fal_job(suffix):
    for model in (reference.MODEL, 'fal-ai/veo3.1'):
        url = f'https://queue.fal.run/{model}/requests/{REQUEST}{suffix}'
        assert reference.queue_url(url, REQUEST, suffix) == url
        for altered in (url.replace('queue.fal.run', 'example.com'), url + '?token=x',
                        url.replace(REQUEST, 'another-job'), url.replace('https:', 'http:')):
            with pytest.raises(SpendBlocked): reference.queue_url(altered, REQUEST, suffix)


def test_reference_trial_rejects_publication_or_changed_contract_before_paid_work(monkeypatch):
    source = {'parent_id': None, 'spec': {'production_channel_id': reference.CHANNEL_ID,
        'publish_after_render': False, 'production_scheduled': False,
        'quality_feedback': 'iyi çıkmadı kalitesi',
        'framecase_reference_trial': reference.contract_for(IMAGE, PROMPT)}}
    monkeypatch.setattr(reference.video, 'enabled_for_task', lambda: True)
    foundation = Mock(); monkeypatch.setattr(reference.runtime, 'configured_ledger', foundation)
    for field, changed in [('publish_after_render', True), ('production_scheduled', True),
                           ('production_channel_id', 'other'), ('framecase_reference_trial', {})]:
        bad = deepcopy(source); bad['spec'][field] = changed
        monkeypatch.setattr(reference.jobs, 'get_job', lambda _, value=bad: value)
        with pytest.raises(SpendBlocked): reference.generate(IMAGE, PROMPT)
    foundation.assert_not_called()
