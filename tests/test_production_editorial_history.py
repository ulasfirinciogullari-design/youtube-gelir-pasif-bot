import json
from copy import deepcopy

import fakeredis
import pytest

from app.services import production_editorial_history as history, production_series_promotion as promotion

CHANNEL = 'UC5v9AvNtD3PTLgo6m1jROOA'


def archive(client, number, topics, previous=None):
    row = {'channel_id': CHANNEL, 'epoch': number - 1,
        'profile': {'production_topics': topics, 'description_footer': 'PRIVATE FOOTER'},
        'state': {'last_task_id': 'PRIVATE TASK'}, 'credential_sha256': 'PRIVATE HASH',
        'previous_epoch': previous}
    key = promotion.ARCHIVE_PREFIX + CHANNEL + ':' + str(number - 1) + ':test'
    receipt_key = promotion.RECEIPT_PREFIX + CHANNEL + ':' + str(number)
    client.set(key, json.dumps(row))
    receipt = {'channel_id': CHANNEL, 'epoch': number, 'archive_key': key, 'archive_sha256': promotion._digest(row)}
    client.set(receipt_key, json.dumps(receipt))
    epoch = {'epoch': number, 'receipt_key': receipt_key}
    client.set(promotion.EPOCH_PREFIX + CHANNEL, json.dumps(epoch))
    return epoch, key


def test_only_public_prior_topics_reach_guidance_without_writing_or_leaking_profile_fields():
    client = fakeredis.FakeRedis(decode_responses=True)
    one, _ = archive(client, 1, ['Original episode https://www.ibm.com/history/upc'])
    archive(client, 2, ['Second episode https://www.bep.gov/currency/serial-numbers'], one)
    before = {key: client.get(key) for key in client.keys()}
    result = history.recent_topics(client, CHANNEL)
    assert len(result) == 2 and result[0].startswith('Second')
    assert 'PRIVATE' not in json.dumps(result)
    assert {key: client.get(key) for key in client.keys()} == before


@pytest.mark.parametrize('change', ['hash', 'private_url', 'credential', 'epoch_loop', 'other_channel'])
def test_invalid_or_secret_history_is_not_model_input(change):
    client = fakeredis.FakeRedis(decode_responses=True)
    epoch, key = archive(client, 1, ['Ordinary public topic https://www.ibm.com/history/upc'])
    row = json.loads(client.get(key))
    if change == 'hash': row['profile']['production_topics'] = ['changed']
    if change == 'private_url': row['profile']['production_topics'] = ['Secret https://127.0.0.1/private']
    if change == 'credential': row['profile']['production_topics'] = ['api_key=secret-material']
    if change == 'epoch_loop': row['previous_epoch'] = deepcopy(epoch)
    if change == 'other_channel': row['channel_id'] = 'UCgvESYtYbn2w9R2ExBOF_cw'
    client.set(key, json.dumps(row))
    if change != 'hash':
        receipt = json.loads(client.get(epoch['receipt_key']));receipt['archive_sha256'] = promotion._digest(row)
        client.set(epoch['receipt_key'], json.dumps(receipt))
    assert history.recent_topics(client, CHANNEL) == []
