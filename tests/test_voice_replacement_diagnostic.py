"""Mocked private storage only; no TTS, downloads, jobs or media fitting."""
import ast
import hashlib
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest


TASK = '18400000-0000-4000-8000-000000000004'
AUDIO = b'ID3' + b'original-audio' * 100


@pytest.fixture
def case():
    path = Path(__file__).resolve().parents[1] / 'app/services/voice_replacement_diagnostic.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom) and (node.module or '').startswith('app.'))]
    receipt = {'ETag': '"originaletag123"', 'ResponseMetadata': {'HTTPStatusCode': 200}}
    client = Mock()
    client.put_object.return_value = receipt
    storage = SimpleNamespace(_client=Mock(return_value=client), settings=SimpleNamespace(bucket='private-fixture'))
    module = ModuleType('voice_replacement_diagnostic')
    module.storage = storage
    exec(compile(tree, str(path), 'exec'), module.__dict__)
    return SimpleNamespace(module=module, storage=storage, client=client, receipt=receipt)


def _run(c, task=TASK, audio=AUDIO):
    return c.module.persist_raw_voice_replacement(task, audio)


def test_exact_existing_bytes_are_private_immutable_and_never_a_qa_or_recovery_approval(case):
    c = case
    pointer = _run(c)
    checksum = hashlib.sha256(AUDIO).hexdigest()
    assert pointer == {
        'version': 1, 'status': 'unapproved_raw_voice', 'task_id': TASK,
        'key': f'audio_replacement_diagnostics/{TASK}/{checksum}.mp3',
        'sha256': checksum, 'size': len(AUDIO), 'qa_approved': False, 'reusable': False,
        'publish_eligible': False, 'requires_full_qa': True}
    args = c.client.put_object.call_args.kwargs
    assert args['Body'] is AUDIO and args['ContentLength'] == len(AUDIO)
    assert args['Key'] == pointer['key'] and args['IfNoneMatch'] == '*'
    assert args['ContentType'] == 'audio/mpeg' and args['CacheControl'] == 'private, no-store'
    assert args['Metadata'] == {'sha256': checksum} and 'ACL' not in args
    c.client.head_object.assert_not_called()
    assert not {'audio_candidate_checkpoint', 'scene_durations', 'voice_result', 'url', 'etag'} & pointer.keys()


@pytest.mark.parametrize('task,audio', [
    ('../source', AUDIO), (TASK.replace('18400000', 'abcdefab').upper(), AUDIO), (None, AUDIO), (True, AUDIO),
    (TASK, None), (TASK, bytearray(AUDIO)), (TASK, b'ID3short'), (TASK, b'notmp3' * 200),
    (TASK, b'ID3' + b'x' * (14 * 1024 * 1024)),
], ids=['traversal', 'uppercase-uuid', 'null-id', 'bool-id', 'null-audio',
        'mutable-audio', 'short-audio', 'wrong-header', 'oversize-audio'])
def test_invalid_input_never_opens_storage(case, task, audio):
    with pytest.raises(case.module.VoiceReplacementDiagnosticError): _run(case, task, audio)
    case.storage._client.assert_not_called()


def test_mpeg_frame_sync_and_exact_size_bound_are_accepted(case):
    pointer = _run(case, audio=b'\xff\xfb' + b'x' * 1022)
    assert pointer['size'] == 1024


class StorageFailure(Exception):
    def __init__(self, code='PreconditionFailed'):
        super().__init__('secret-storage-response-must-not-escape')
        self.response = {'Error': {'Code': code}, 'ResponseMetadata': {'HTTPStatusCode': 412}}


def _collision(c):
    c.client.put_object.side_effect = StorageFailure()
    c.client.head_object.return_value = {
        **c.receipt, 'ContentLength': len(AUDIO), 'ContentType': 'audio/mpeg',
        'Metadata': {'sha256': hashlib.sha256(AUDIO).hexdigest()}}


def test_same_hash_object_retry_checks_existing_metadata_without_overwriting(case):
    _collision(case)
    pointer = _run(case)
    assert pointer['sha256'] == hashlib.sha256(AUDIO).hexdigest()
    assert case.client.put_object.call_count == 1 and case.client.head_object.call_count == 1
    assert case.client.head_object.call_args.kwargs['Key'] == pointer['key']


@pytest.mark.parametrize('field,value', [('ContentLength', True), ('ContentLength', 999),
                                       ('ContentType', 'text/plain'), ('Metadata', {}),
                                       ('Metadata', {'sha256': 'b' * 64}), ('ETag', 'unquoted'),
                                       ('ResponseMetadata', {'HTTPStatusCode': 206})])
def test_existing_object_must_match_exact_hash_size_and_type(case, field, value):
    _collision(case)
    case.client.head_object.return_value[field] = value
    with pytest.raises(case.module.VoiceReplacementDiagnosticError): _run(case)
    assert case.client.put_object.call_count == 1


@pytest.mark.parametrize('receipt', [{}, None, {'ETag': 'bad'},
                                   {'ETag': '"okay"', 'ResponseMetadata': {'HTTPStatusCode': 500}},
                                   {'ETag': '"secret\nvalue"', 'ResponseMetadata': {'HTTPStatusCode': 200}}])
def test_unconfirmed_storage_receipt_fails_safely_without_second_put(case, receipt):
    case.client.put_object.return_value = receipt
    with pytest.raises(case.module.VoiceReplacementDiagnosticError, match='^voice_replacement_raw_audio_unavailable$'):
        _run(case)
    assert case.client.put_object.call_count == 1


@pytest.mark.parametrize('error', [TimeoutError('secret timeout'), StorageFailure('AccessDenied')])
def test_ambiguous_or_other_storage_error_never_retries_or_leaks(case, error):
    case.client.put_object.side_effect = error
    with pytest.raises(case.module.VoiceReplacementDiagnosticError, match='^voice_replacement_raw_audio_unavailable$'):
        _run(case)
    assert case.client.put_object.call_count == 1
    case.client.head_object.assert_not_called()
