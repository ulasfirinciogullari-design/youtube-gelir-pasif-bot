"""One reviewed native Claude image request; no route, funding or QA approval.

Reviewed 2026-09-09:
https://abacus.ai/help/developer-platform/route-llm/anthropic-messages
https://routellm-apis.abacus.ai/
https://platform.claude.com/docs/en/models/overview
https://platform.claude.com/docs/en/build-with-claude/vision

Abacus forwards native Messages unchanged. Haiku 4.5 has a 200K context
window and costs $1/$5 per million input/output tokens. Reserve that entire
input window, including image processing and framing; byte or patch estimates
are not a guaranteed token ceiling. Output is reserved separately in full.
"""
import base64
from datetime import datetime, timezone
import json
import re
import subprocess

from app.services.production_spend import SpendBlocked, SpendQuote


ABACUS_VISUAL_MODEL = 'claude-haiku-4-5-20251001'
ABACUS_VISUAL_PRICE_REVISION = 'abacus-vision-2026-09-09-v1'
ABACUS_VISUAL_INPUT_TOKENS = 200_000
ABACUS_VISUAL_MAX_OUTPUT_TOKENS = 8192
ABACUS_VISUAL_MAX_IMAGES = 60
ABACUS_VISUAL_MAX_IMAGE_BYTES = 180 * 1024
ABACUS_VISUAL_MAX_IMAGE_WIDTH = 640
ABACUS_VISUAL_MAX_IMAGE_HEIGHT = 2000
ABACUS_VISUAL_MAX_DECODED_BYTES = 12 * 1024 * 1024
ABACUS_VISUAL_MAX_METADATA_BYTES = 100_000
# Per-frame labels, per-scene context and complete-story instructions are text
# blocks too. Do not drop that evidence to fit the independent 60-image cap.
_MAX_BLOCKS = 4 * ABACUS_VISUAL_MAX_IMAGES + 1
_MAX_BASE64_BYTES = 4 * ((ABACUS_VISUAL_MAX_IMAGE_BYTES + 2) // 3)
_MAX_WIRE_BYTES = ABACUS_VISUAL_MAX_METADATA_BYTES + ABACUS_VISUAL_MAX_IMAGES * _MAX_BASE64_BYTES
_VALID_FROM = datetime(2026, 9, 9, tzinfo=timezone.utc)
_VALID_UNTIL = datetime(2026, 10, 1, tzinfo=timezone.utc)
_BODY_FIELDS = {'model', 'system', 'messages', 'max_tokens', 'thinking', 'stream', 'service_tier'}


def _require(condition, code='spend_request_not_priced'):
    if not condition:
        raise SpendBlocked(code)


def _text(value):
    _require(type(value) is str and bool(value.strip())
             and len(value.encode('utf-8')) <= ABACUS_VISUAL_MAX_METADATA_BYTES)
    return value


def _encoded(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False).encode('ascii')


def _fresh(now=None):
    moment = datetime.now(timezone.utc) if now is None else now
    _require(isinstance(moment, datetime) and moment.tzinfo is not None
             and _VALID_FROM <= moment < _VALID_UNTIL, 'spend_price_review_expired')


def _jpeg_dimensions(raw):
    """Bound one complete JPEG before decode, including later scan markers.

    A second concatenated image must not reach the decoder with dimensions
    hidden behind the first frame's small SOF marker. Stuffed entropy bytes
    and restart markers are distinct from segment and end-of-image markers.
    """
    _require(raw.startswith(b'\xff\xd8') and raw.endswith(b'\xff\xd9'))
    offset, dimensions, scan, seen_scan = 2, None, False, False
    while offset < len(raw):
        if scan:
            offset = raw.find(b'\xff', offset)
            _require(offset >= 0)
        _require(raw[offset] == 255)
        while offset < len(raw) and raw[offset] == 255:
            offset += 1
        _require(offset < len(raw))
        marker = raw[offset]
        offset += 1
        if scan and marker in {0, *range(208, 216)}:
            continue
        if marker == 217:
            _require(seen_scan and dimensions is not None and offset == len(raw))
            return dimensions
        _require(marker not in {0, 1, 216, *range(208, 216)} and offset + 2 <= len(raw))
        scan = False
        length = int.from_bytes(raw[offset:offset + 2], 'big')
        _require(length >= 2 and offset + length <= len(raw))
        segment = raw[offset + 2:offset + length]
        if marker in {192, 194}:  # Ordinary baseline or progressive, 8-bit JPEG.
            _require(dimensions is None and len(segment) >= 6 and segment[0] == 8
                     and segment[5] in {1, 3} and len(segment) == 6 + 3 * segment[5])
            height, width = int.from_bytes(segment[1:3], 'big'), int.from_bytes(segment[3:5], 'big')
            _require(1 <= width <= ABACUS_VISUAL_MAX_IMAGE_WIDTH
                     and 1 <= height <= ABACUS_VISUAL_MAX_IMAGE_HEIGHT)
            dimensions = width, height
        elif marker in set(range(192, 208)) - {196, 200, 204}:
            _require(False)  # Other SOF modes are not this reviewed frame route.
        if marker == 218:  # SOS; strict full decode below checks the entropy data.
            _require(dimensions is not None)
            scan = seen_scan = True
        offset += length
    _require(False)


def _decode_jpeg(raw):
    """Use the existing decoder, stdin only, one thread, no output media file.

    Header recognition alone accepts damaged/truncated image data. The fixed
    MJPEG demuxer plus strict decode must produce exactly one bounded RGB frame.
    This checks bytes, not visual quality, and cannot access a media URL.
    """
    width, height = _jpeg_dimensions(raw)
    result = subprocess.run([
        'ffmpeg', '-v', 'error', '-nostdin', '-xerror', '-err_detect', 'explode',
        '-threads', '1', '-protocol_whitelist', 'pipe', '-f', 'mjpeg', '-i', 'pipe:0',
        '-map', '0:v:0', '-an', '-sn', '-dn', '-pix_fmt', 'rgb24', '-threads', '1',
        '-f', 'framehash', '-hash', 'sha256', 'pipe:1',
    ], input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=True)
    _require(len(result.stdout) <= 16 * 1024)
    lines = result.stdout.decode('ascii').splitlines()
    dimension_rows = [line for line in lines if line.startswith('#dimensions ')]
    _require(dimension_rows == [f'#dimensions 0: {width}x{height}'])
    frames = [line for line in lines if line and not line.startswith('#')]
    _require(len(frames) == 1)
    fields = [field.strip() for field in frames[0].split(',')]
    _require(len(fields) == 6 and fields[0] == '0' and fields[4] == str(width * height * 3)
             and re.fullmatch(r'[0-9a-f]{64}', fields[5]) is not None)


def inspect_abacus_visual_request(body):
    """Return detached PRIVATE input plus explicit token/asset bounds.

    Preserve every system/text/schema byte and every interleaved image block.
    No URL, truncation, image transformation, tools, cache or provider call.
    """
    try:
        _require(type(body) is dict and set(body) == _BODY_FIELDS
                 and body['model'] == ABACUS_VISUAL_MODEL and type(body['model']) is str
                 and body['stream'] is False and type(body['service_tier']) is str
                 and body['service_tier'] == 'standard_only'
                 and type(body['thinking']) is dict and body['thinking'] == {'type': 'disabled'}
                 and type(body['thinking']['type']) is str)
        output = body['max_tokens']
        _require(type(output) is int and 1 <= output <= ABACUS_VISUAL_MAX_OUTPUT_TOKENS)
        system = _text(body['system'])
        messages = body['messages']
        _require(type(messages) is list and len(messages) == 1 and type(messages[0]) is dict
                 and set(messages[0]) == {'role', 'content'} and type(messages[0]['role']) is str
                 and messages[0]['role'] == 'user')
        content = messages[0]['content']
        _require(type(content) is list and 2 <= len(content) <= _MAX_BLOCKS)
        private_blocks, metadata_blocks, images, total, text_count, verified = [], [], 0, 0, 0, set()
        for block in tuple(content):
            _require(type(block) is dict and type(block.get('type')) is str)
            if block.get('type') == 'text':
                _require(set(block) == {'type', 'text'})
                detached = {'type': 'text', 'text': _text(block['text'])}
                private_blocks.append(detached)
                metadata_blocks.append(detached)
                text_count += 1
                continue
            _require(set(block) == {'type', 'source'} and block['type'] == 'image')
            source = block['source']
            _require(type(source) is dict and set(source) == {'type', 'media_type', 'data'}
                     and type(source['type']) is str and source['type'] == 'base64'
                     and type(source['media_type']) is str and source['media_type'] == 'image/jpeg')
            encoded = source['data']
            _require(type(encoded) is str and 0 < len(encoded) <= _MAX_BASE64_BYTES)
            raw = base64.b64decode(encoded, validate=True)
            _require(0 < len(raw) <= ABACUS_VISUAL_MAX_IMAGE_BYTES
                     and base64.b64encode(raw).decode('ascii') == encoded)
            images += 1
            total += len(raw)
            _require(images <= ABACUS_VISUAL_MAX_IMAGES and total <= ABACUS_VISUAL_MAX_DECODED_BYTES)
            verified.add(raw)
            private_blocks.append({'type': 'image', 'source': {
                'type': 'base64', 'media_type': 'image/jpeg', 'data': encoded}})
            metadata_blocks.append({'type': 'image', 'source': {
                'type': 'base64', 'media_type': 'image/jpeg', 'data': ''}})
        _require(images > 0 and text_count > 0)
        detached = {'model': ABACUS_VISUAL_MODEL, 'system': system,
                    'messages': [{'role': 'user', 'content': private_blocks}], 'max_tokens': output,
                    'thinking': {'type': 'disabled'}, 'stream': False, 'service_tier': 'standard_only'}
        metadata = {**detached, 'messages': [{'role': 'user', 'content': metadata_blocks}]}
        _require(len(_encoded(metadata)) <= ABACUS_VISUAL_MAX_METADATA_BYTES
                 and len(_encoded(detached)) <= _MAX_WIRE_BYTES)
        for raw in verified:
            _decode_jpeg(raw)
        return {'body': detached, 'input_tokens_upper_bound': ABACUS_VISUAL_INPUT_TOKENS,
                'max_tokens': output, 'image_count': images, 'decoded_image_bytes': total}
    except SpendBlocked:
        raise
    except Exception:
        raise SpendBlocked('spend_request_not_priced') from None


def quote_abacus_visual_request(body, headers, *, now=None):
    """List-price maximum only; current account coverage is admitted elsewhere."""
    try:
        _fresh(now)
        _require(type(headers) is dict and all(type(name) is str for name in headers))
        names = [name.lower() for name in headers]
        _require(len(set(names)) == len(names) and set(names) == {
            'x-api-key', 'content-type', 'anthropic-version'})
        normalized = {name.lower(): value for name, value in headers.items()}
        key = normalized['x-api-key']
        _require(type(key) is str and 1 <= len(key) <= 4096
                 and all(32 < ord(char) < 127 for char in key)
                 and normalized['content-type'] == 'application/json'
                 and normalized['anthropic-version'] == '2023-06-01')
        inspected = inspect_abacus_visual_request(body)
        # At $1/$5 per million, one input/output token is exactly 1/5 microUSD.
        maximum = inspected['input_tokens_upper_bound'] + 5 * inspected['max_tokens']
        return SpendQuote('abacus', ABACUS_VISUAL_MODEL, maximum, ABACUS_VISUAL_PRICE_REVISION)
    except SpendBlocked:
        raise
    except Exception:
        raise SpendBlocked('spend_request_not_priced') from None
