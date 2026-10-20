"""Native image pricing and real bounded JPEG decoding, without provider IO."""
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import subprocess
from unittest.mock import Mock

import pytest

from app.services import abacus_visual_spend_quotes as visual, production_spend_quotes as catalog
from app.services.production_spend import SpendBlocked


NOW = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
ENDPOINT = 'https://routellm.abacus.ai/v1/messages'
HEADERS = {'x-api-key': 'private-fixture-key', 'Content-Type': 'application/json',
           'anthropic-version': '2023-06-01'}


@pytest.fixture(scope='module')
def jpeg():
    cache = {}
    def make(width=64, height=64):
        if (width, height) not in cache:
            result = subprocess.run([
                'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'color=c=blue:size={width}x{height}:rate=1',
                '-frames:v', '1', '-pix_fmt', 'yuvj444p', '-threads', '1', '-f', 'image2pipe', 'pipe:1',
            ], check=True, timeout=10, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            cache[width, height] = result.stdout
        return cache[width, height]
    return make


def image(raw):
    return {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg',
                                      'data': base64.b64encode(raw).decode('ascii')}}


def body(raw):
    return {'model': visual.ABACUS_VISUAL_MODEL, 'system': 'Return the complete JSON schema: {"type":"object"}.',
        'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': 'Özgün bütün kanıtlar.'}, image(raw),
                                                {'type': 'text', 'text': 'Frame 2 of the same scene.'}, image(raw)]}],
        'max_tokens': 8192, 'thinking': {'type': 'disabled'}, 'stream': False, 'service_tier': 'standard_only'}


def quote(request, headers=None, now=NOW):
    return visual.quote_abacus_visual_request(request, HEADERS if headers is None else headers, now=now)


def padded_jpeg(raw, size):
    """Legal JPEG comments fill a byte boundary without changing pixels."""
    pieces, remaining = [], size - len(raw)
    assert remaining >= 4
    while remaining:
        chunk = min(remaining, 65537)
        if 0 < remaining - chunk < 4:
            chunk -= 4
        assert chunk >= 4
        pieces.append(b'\xff\xfe' + (chunk - 2).to_bytes(2, 'big') + b'x' * (chunk - 4))
        remaining -= chunk
    return raw[:2] + b''.join(pieces) + raw[2:]


@pytest.mark.parametrize('output', [1, 1024, 8192])
def test_full_context_reservation_and_actual_output_ceiling(jpeg, output):
    request = body(jpeg())
    request['max_tokens'] = output
    result = quote(request)
    assert result.provider == 'abacus' and result.model == 'claude-haiku-4-5-20251001'
    assert result.maximum_micro == 200_000 + 5 * output
    assert result.price_revision == 'abacus-vision-2026-09-09-v1'
    assert result.price_revision != catalog._REVISION
    assert result.maximum_micro >= 200_005


def test_inspector_preserves_every_interleaved_block_and_schema_in_detached_body(jpeg):
    request, original = body(jpeg()), body(jpeg())
    inspected = visual.inspect_abacus_visual_request(request)
    assert inspected == {'body': original, 'input_tokens_upper_bound': 200_000, 'max_tokens': 8192,
                         'image_count': 2, 'decoded_image_bytes': 2 * len(jpeg())}
    assert request == original
    request['system'] = 'lost schema'
    request['messages'][0]['content'][0]['text'] = 'changed prompt'
    request['messages'][0]['content'][1]['source']['data'] = 'changed data'
    request['thinking']['type'] = 'enabled'
    assert inspected['body'] == original
    inspected['body']['messages'][0]['content'].append({'type': 'text', 'text': 'private copy'})
    assert len(original['messages'][0]['content']) == len(request['messages'][0]['content']) == 4


def test_image_count_does_not_reduce_the_full_context_quote(jpeg):
    request = body(jpeg())
    request['messages'][0]['content'] = [{'type': 'text', 'text': 'All sixty complete frames.'}] + [image(jpeg()) for _ in range(60)]
    inspected = visual.inspect_abacus_visual_request(request)
    assert inspected['image_count'] == 60 and len(inspected['body']['messages'][0]['content']) == 61
    assert quote(request).maximum_micro == quote(body(jpeg())).maximum_micro == 240_960


def test_duplicate_images_decode_once_but_are_all_counted_and_preserved(jpeg, monkeypatch):
    decode = Mock(wraps=visual._decode_jpeg)
    monkeypatch.setattr(visual, '_decode_jpeg', decode)
    request = body(jpeg())
    assert visual.inspect_abacus_visual_request(request)['decoded_image_bytes'] == 2 * len(jpeg())
    decode.assert_called_once_with(jpeg())
    # The proof is request-local; a later request still verifies actual bytes.
    visual.inspect_abacus_visual_request(request)
    assert decode.call_count == 2


@pytest.mark.parametrize('width,height', [(640, 2000), (640, 64), (64, 2000)])
def test_real_jpeg_dimension_boundaries_are_decoded(jpeg, width, height):
    assert quote(body(jpeg(width, height))).maximum_micro == 240_960


def test_exact_per_image_byte_boundary_is_valid_and_next_byte_is_rejected(jpeg):
    raw = padded_jpeg(jpeg(), visual.ABACUS_VISUAL_MAX_IMAGE_BYTES)
    request = body(raw)
    assert visual.inspect_abacus_visual_request(request)['decoded_image_bytes'] == 2 * len(raw)
    with pytest.raises(SpendBlocked, match='^spend_request_not_priced$'):
        quote(body(padded_jpeg(jpeg(), visual.ABACUS_VISUAL_MAX_IMAGE_BYTES + 1)))


@pytest.mark.parametrize('change', [
    {'model': 'claude-haiku-4-5'}, {'model': 'claude-sonnet-4-6'}, {'model': 'route-llm'},
    {'thinking': {'type': 'enabled', 'budget_tokens': 1024}}, {'thinking': {'type': 'disabled', 'budget_tokens': 0}},
    {'thinking': None}, {'stream': True}, {'stream': 0}, {'service_tier': 'auto'},
    {'max_tokens': 0}, {'max_tokens': 8193}, {'max_tokens': True}, {'max_tokens': 8192.0},
    {'max_tokens': '8192'}, {'inference_geo': 'global'}, {'inference_geo': 'us'},
    {'tools': []}, {'tool_choice': {'type': 'none'}}, {'cache_control': {'type': 'ephemeral'}},
    {'metadata': {'user_id': 'unreviewed'}}, {'output_config': {'format': {'type': 'json_schema'}}},
    {'system': [{'type': 'text', 'text': 'hidden cache', 'cache_control': {'type': 'ephemeral'}}]},
    {'temperature': 0}, {'top_p': 1}, {'stop_sequences': []}, {'speed': 'fast'},
])
def test_unknown_model_pricing_or_billable_native_options_are_blocked(jpeg, change):
    request = body(jpeg()); request.update(change)
    with pytest.raises(SpendBlocked, match='^spend_request_not_priced$'): quote(request)


@pytest.mark.parametrize('field', sorted(visual._BODY_FIELDS))
def test_required_native_fields_cannot_default_to_an_unreviewed_mode(jpeg, field):
    request = body(jpeg()); request.pop(field)
    with pytest.raises(SpendBlocked): quote(request)


@pytest.mark.parametrize('damage', ['assistant', 'history', 'messages_dict', 'extra_message_field',
    'content_string', 'no_images', 'no_text', 'empty', '61_images', 'too_many_blocks',
    'image_url', 'source_url', 'file_id', 'document', 'png_mime', 'cache_image', 'cache_text',
    'image_detail', 'source_extra', 'text_extra', 'nested_text', 'blank_text', 'empty_system',
    'unpaired_surrogate', 'metadata_limit'])
def test_complete_native_content_shape_is_required_without_truncation(jpeg, damage):
    request = body(jpeg())
    parts = request['messages'][0]['content']
    if damage == 'assistant': request['messages'][0]['role'] = 'assistant'
    elif damage == 'history': request['messages'].append(deepcopy(request['messages'][0]))
    elif damage == 'messages_dict': request['messages'] = request['messages'][0]
    elif damage == 'extra_message_field': request['messages'][0]['name'] = 'other'
    elif damage == 'content_string': request['messages'][0]['content'] = 'text-only'
    elif damage == 'no_images': request['messages'][0]['content'] = [parts[0], parts[2]]
    elif damage == 'no_text': request['messages'][0]['content'] = [parts[1], parts[3]]
    elif damage == 'empty': request['messages'][0]['content'] = []
    elif damage == '61_images': request['messages'][0]['content'] = [parts[0]] + [parts[1]] * 61
    elif damage == 'too_many_blocks': request['messages'][0]['content'] = [parts[0]] * 241 + [parts[1]]
    elif damage == 'image_url': parts[1] = {'type': 'image_url', 'image_url': {'url': 'https://private.invalid/SECRET'}}
    elif damage == 'source_url': parts[1]['source'] = {'type': 'url', 'url': 'https://private.invalid/SECRET'}
    elif damage == 'file_id': parts[1]['source'] = {'type': 'file', 'file_id': 'file_private'}
    elif damage == 'document': parts[1]['type'] = 'document'
    elif damage == 'png_mime': parts[1]['source']['media_type'] = 'image/png'
    elif damage == 'cache_image': parts[1]['cache_control'] = {'type': 'ephemeral'}
    elif damage == 'cache_text': parts[0]['cache_control'] = {'type': 'ephemeral'}
    elif damage == 'image_detail': parts[1]['detail'] = 'low'
    elif damage == 'source_extra': parts[1]['source']['extra'] = 'unpriced'
    elif damage == 'text_extra': parts[0]['citations'] = []
    elif damage == 'nested_text': parts[0]['text'] = {'text': 'hidden'}
    elif damage == 'blank_text': parts[0]['text'] = ' \n\t'
    elif damage == 'empty_system': request['system'] = ''
    elif damage == 'unpaired_surrogate': parts[0]['text'] = '\ud800'
    else: request['system'] = 'A' * 99_999
    original = deepcopy(request)
    with pytest.raises(SpendBlocked, match='^spend_request_not_priced$'): quote(request)
    assert request == original


@pytest.mark.parametrize('damage', ['not_base64', 'data_url', 'whitespace', 'no_padding', 'png_bytes',
    'truncated', 'header_only', 'concatenated', 'empty', 'noncanonical_padding', 'wide', 'tall'])
def test_actual_jpeg_bytes_must_decode_to_exactly_one_bounded_frame(jpeg, damage):
    request = body(jpeg())
    encoded = request['messages'][0]['content'][1]['source']['data']
    if damage == 'not_base64': encoded = '@@SECRETinvalid@@'
    elif damage == 'data_url': encoded = 'data:image/jpeg;base64,' + encoded
    elif damage == 'whitespace': encoded += '\n'
    elif damage == 'no_padding': encoded = encoded[:-1]
    elif damage == 'empty': encoded = ''
    elif damage == 'noncanonical_padding': encoded += '='
    else:
        raw = {'png_bytes': b'\x89PNG\r\n\x1a\nnotajpeg', 'truncated': jpeg()[:-2],
               'header_only': jpeg()[:50] + b'\xff\xd9', 'concatenated': jpeg() + jpeg(),
               'wide': jpeg(642, 64), 'tall': jpeg(64, 2002)}[damage]
        encoded = base64.b64encode(raw).decode('ascii')
    request['messages'][0]['content'][1]['source']['data'] = encoded
    with pytest.raises(SpendBlocked, match='^spend_request_not_priced$'): quote(request)


def test_complete_metadata_is_counted_before_image_decoder(jpeg, monkeypatch):
    request = body(jpeg())
    request['system'] = 'Ü' * 20_000  # ASCII JSON escaping exceeds metadata cap.
    decoder = Mock(side_effect=AssertionError('Decoder must not run'))
    monkeypatch.setattr(visual, '_decode_jpeg', decoder)
    with pytest.raises(SpendBlocked): quote(request)
    decoder.assert_not_called()


def test_exact_metadata_limit_preserves_complete_text_and_next_byte_blocks(jpeg):
    request = body(jpeg())
    metadata = deepcopy(request)
    for block in metadata['messages'][0]['content']:
        if block['type'] == 'image': block['source']['data'] = ''
    amount = visual.ABACUS_VISUAL_MAX_METADATA_BYTES - len(visual._encoded(metadata))
    request['system'] += 'A' * amount
    assert visual.inspect_abacus_visual_request(request)['body'] == request
    request['system'] += 'A'
    with pytest.raises(SpendBlocked): quote(request)


def test_aggregate_limit_counts_duplicate_images_separately(jpeg, monkeypatch):
    raw = jpeg()
    monkeypatch.setattr(visual, 'ABACUS_VISUAL_MAX_DECODED_BYTES', 2 * len(raw) - 1)
    with pytest.raises(SpendBlocked): quote(body(raw))


def test_valid_dimensions_do_not_make_corrupted_entropy_a_valid_decoded_jpeg(jpeg):
    raw = jpeg()
    marker = raw.index(b'\xff\xda')
    entropy = marker + 2 + int.from_bytes(raw[marker + 2:marker + 4], 'big')
    damaged = raw[:entropy] + b'\x00' + b'\xff\xd9'
    assert visual._jpeg_dimensions(damaged) == (64, 64)
    with pytest.raises(SpendBlocked): quote(body(damaged))


def test_second_concatenated_frame_never_reaches_decoder(jpeg, monkeypatch):
    request = body(jpeg() + jpeg(642, 64))
    decoder = Mock(side_effect=AssertionError('No decoder on multiple images'))
    monkeypatch.setattr(visual.subprocess, 'run', decoder)
    with pytest.raises(SpendBlocked): quote(request)
    decoder.assert_not_called()


@pytest.mark.parametrize('headers', [
    {}, {**HEADERS, 'Authorization': 'Bearer another-key'}, {**HEADERS, 'X-Api-Key': 'duplicate'},
    {**HEADERS, 'anthropic-beta': 'context-1m'}, {**HEADERS, 'x-api-key': ''},
    {**HEADERS, 'x-api-key': 'bad\nSECRET'}, {**HEADERS, 'x-api-key': 'şifre'},
    {**HEADERS, 'Content-Type': 'application/octet-stream'}, {**HEADERS, 'anthropic-version': 'future'},
    {**HEADERS, 'x-organization-id': 'other-account'}, {**HEADERS, 'extra_headers': {}},
])
def test_extra_or_conflicting_headers_are_never_priced(jpeg, headers):
    with pytest.raises(SpendBlocked, match='^spend_request_not_priced$'): quote(body(jpeg()), headers)


@pytest.mark.parametrize('now', [datetime(2026, 9, 8, 23, 59, 59, tzinfo=timezone.utc),
    datetime(2026, 10, 1, tzinfo=timezone.utc), datetime(2027, 1, 1, tzinfo=timezone.utc),
    datetime(2026, 9, 9), '2026-09-09'])
def test_separate_visual_revision_expires_fail_closed(jpeg, now):
    with pytest.raises(SpendBlocked, match='^spend_price_review_expired$'): quote(body(jpeg()), now=now)


def test_visual_review_window_includes_its_start_and_excludes_october(jpeg):
    for moment in (visual._VALID_FROM, visual._VALID_UNTIL - timedelta(microseconds=1)):
        assert quote(body(jpeg()), now=moment).price_revision == visual.ABACUS_VISUAL_PRICE_REVISION


@pytest.mark.parametrize('failure', [FileNotFoundError('SECRET executable'),
    subprocess.TimeoutExpired('SECRET command', 5), subprocess.CalledProcessError(1, 'SECRET command')])
def test_decoder_unavailability_or_failure_cannot_produce_price_or_expose_details(jpeg, monkeypatch, failure):
    raw = jpeg()
    monkeypatch.setattr(visual.subprocess, 'run', Mock(side_effect=failure))
    with pytest.raises(SpendBlocked, match='^spend_request_not_priced$') as error: quote(body(raw))
    assert 'SECRET' not in str(error.value)


def test_fixed_route_delegates_only_native_visual_shape_and_leaves_text_revision_unchanged(jpeg, monkeypatch):
    monkeypatch.setattr(visual, '_fresh', lambda now=None: None)
    monkeypatch.setattr(catalog, '_fresh', lambda: None)
    request = body(jpeg())
    provider, operation, result = catalog.quote_http_request(ENDPOINT, {'headers': HEADERS, 'json': request})
    assert (provider, operation, result.price_revision) == ('abacus', '/v1/messages', visual.ABACUS_VISUAL_PRICE_REVISION)
    text = deepcopy(request); text['messages'][0]['content'] = 'The unchanged original text path.'
    result = catalog.quote_http_request(ENDPOINT, {'headers': HEADERS, 'json': text})[2]
    assert result.price_revision == catalog._REVISION == 'official-2026-09-08-v3'
    assert result.maximum_micro == len(json.dumps(text, ensure_ascii=True, allow_nan=False).encode()) + 4096 + 5 * 8192


@pytest.mark.parametrize('url', ['https://routellm.abacus.ai/v1/chat/completions',
    'https://another.abacus.ai/v1/messages', 'http://routellm.abacus.ai/v1/messages',
    ENDPOINT + '?mode=fast', ENDPOINT + '#extra', 'https://secret@routellm.abacus.ai/v1/messages'])
def test_unreviewed_endpoint_cannot_delegate_to_visual_quote(jpeg, url):
    with pytest.raises(SpendBlocked): catalog.quote_http_request(url, {'headers': HEADERS, 'json': body(jpeg())})


def test_decoder_command_is_fixed_stdin_only_bounded_and_has_no_media_file(jpeg, monkeypatch):
    raw = jpeg()
    runner = Mock(wraps=visual.subprocess.run)
    monkeypatch.setattr(visual.subprocess, 'run', runner)
    visual.inspect_abacus_visual_request(body(raw))
    runner.assert_called_once()
    command = runner.call_args.args[0]
    assert command[command.index('-f') + 1] == 'mjpeg'
    assert command[command.index('-protocol_whitelist') + 1] == 'pipe'
    assert command[command.index('-i') + 1] == 'pipe:0' and command[-1] == 'pipe:1'
    assert runner.call_args.kwargs['input'] == raw and runner.call_args.kwargs['timeout'] == 5
    assert runner.call_args.kwargs['stderr'] is subprocess.DEVNULL
