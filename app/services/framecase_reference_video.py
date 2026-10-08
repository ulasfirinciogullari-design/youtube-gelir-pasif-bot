"""One private character-motion trial, with the existing native paid journal.

This is art-direction development, never an automatic episode replacement.
The frozen private trial permits one 8-second request, maximum list price
$0.64 (1080p with sound). No retry POST, safety rewrite or provider fallback.
Pricing/schema reviewed 2026-09-24:
https://fal.ai/models/fal-ai/veo3.1/lite/image-to-video
https://fal.ai/models/fal-ai/veo3.1/lite/image-to-video/api
"""
import base64
from datetime import datetime, timezone
import hashlib
import json
import time
from urllib.parse import urlparse

import httpx

from app.services import commissioning_video as video, production_spend_runtime as runtime
from app.services import fal_video as fal, studio_state as jobs
from app.services.commissioning_fal_video import _Journal
from app.services.framecase_cadence import CHANNEL_ID

MODEL = 'fal-ai/veo3.1/lite/image-to-video'
PRICE_REVISION = 'fal-veo-lite-reference-2026-09-24'
MAXIMUM_MICRO = 640_000


def body_for(image_bytes, prompt):
    video._require(type(image_bytes) is bytes and 1024 <= len(image_bytes) <= 8_000_000
                   and image_bytes.startswith(b'\x89PNG\r\n\x1a\n'))
    video._require(type(prompt) is str and 40 <= len(prompt.encode('utf-16-le')) // 2 <= 1000)
    return {'prompt': prompt, 'image_url': 'data:image/png;base64,' + base64.b64encode(image_bytes).decode(),
            'duration': '8s', 'resolution': '1080p', 'aspect_ratio': '9:16',
            'generate_audio': True, 'auto_fix': False}


def contract_for(image_bytes, prompt):
    body_for(image_bytes, prompt)
    return {'version': 1, 'image_sha256': hashlib.sha256(image_bytes).hexdigest(),
            'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
            'duration_seconds': 8, 'max_list_cost_micro_usd': MAXIMUM_MICRO,
            'price_revision': PRICE_REVISION}


def queue_url(value, request, suffix):
    video._require(type(value) is str)
    try:
        parsed = urlparse(value)
        valid_port = parsed.port in (None, 443)
    except ValueError:
        raise fal.FalVideoProtocolError('Invalid reference queue URL') from None
    allowed = {f'/{name}/requests/{request}{suffix}' for name in (MODEL, 'fal-ai/veo3.1')}
    video._require(parsed.scheme == 'https' and parsed.hostname == 'queue.fal.run'
        and valid_port and parsed.username is None and parsed.password is None
        and parsed.path in allowed and not parsed.params and not parsed.query and not parsed.fragment,
        'framecase_reference_queue_unverified')
    return value


def generate(image_bytes, prompt):
    task = runtime._TASK_ID.get(); source = jobs.get_job(task); spec = source.get('spec') or {}
    contract = contract_for(image_bytes, prompt)
    video._require(spec.get('production_channel_id') == CHANNEL_ID
        and spec.get('publish_after_render') is False and spec.get('production_scheduled') is False
        and spec.get('framecase_reference_trial') == contract and source.get('parent_id') is None
        and spec.get('quality_feedback') == 'iyi çıkmadı kalitesi'
        and video.enabled_for_task(), 'framecase_reference_trial_not_authorized')
    now = datetime.now(timezone.utc)
    video._require(datetime(2026, 9, 24, tzinfo=timezone.utc) <= now < datetime(2026, 10, 1, tzinfo=timezone.utc),
                   'spend_price_review_expired')
    foundation = runtime.configured_ledger(); context = runtime.resolve_context(foundation.client, task)
    scope = {'foundation': foundation, 'context': context, 'package_sha256': video._sha(video._raw(contract).encode()),
             'scene_index': 0, 'narration_millis': 8000, 'generation_seconds': 8, 'aspect_ratio': '9:16'}
    body = body_for(image_bytes, prompt); secret = str(runtime.settings.fal_key or '').strip()
    video._require(1 <= len(secret) <= 8192 and all(32 < ord(c) < 127 for c in secret))
    route = fal._FAL_QUEUE_ORIGIN + '/' + MODEL
    descriptor = {name: scope[name] for name in ('package_sha256', 'scene_index', 'narration_millis',
                                               'generation_seconds', 'aspect_ratio')}
    descriptor.update(route=route, request_sha256=video._sha(video._raw(body).encode()),
        credential_sha256=video._sha(('fal\0' + secret).encode()), model=MODEL, duration_seconds=8,
        max_list_cost_micro_usd=MAXIMUM_MICRO, reference_trial=contract)
    # A changed prompt/image cannot consume the remaining generic episode
    # slots. This trial's authority permits exactly one frozen request.
    existing = json.loads(foundation.client.get(video.PREFIX + context['lineage_id']) or '{}')
    identity = video._sha(video._raw(descriptor).encode())
    video._require(not existing.get('requests') or set(existing['requests']) == {identity},
                   'framecase_reference_trial_capacity')
    journal = _Journal(scope, descriptor, secret)
    headers = {'Authorization': 'Key ' + secret, 'Content-Type': 'application/json',
               'X-Fal-Request-Timeout': '570'}
    with httpx.Client(timeout=httpx.Timeout(30, connect=10), follow_redirects=False, trust_env=False) as client:
        created = journal.submit(client.post, route, headers=headers, json=body)
        if not 200 <= created.status_code < 300:
            fal._raise_submit_rejection(created)
        payload = created.json(); request = payload.get('request_id')
        video._require(type(request) is str and fal._FAL_REQUEST_ID_PATTERN.fullmatch(request))
        status_url = queue_url(payload.get('status_url') or route + '/requests/' + request + '/status', request, '/status')
        result_url = queue_url(payload.get('response_url') or route + '/requests/' + request, request, '')
        response = journal.result_response()
        deadline = time.monotonic() + 600
        while response is None and time.monotonic() < deadline:
            status = client.get(status_url, headers=headers)
            if not 200 <= status.status_code < 300:
                fal._raise_accepted_request_error(status, request)
            state = status.json().get('status')
            if state == 'COMPLETED':
                response = client.get(result_url, headers=headers)
                journal.observe_result(response)
                break
            video._require(state in ('IN_QUEUE', 'IN_PROGRESS'), 'framecase_reference_queue_unverified')
            time.sleep(5)
        video._require(response is not None, 'framecase_reference_outcome_unverified')
        if not 200 <= response.status_code < 300:
            fal._raise_accepted_request_error(response, request)
        error = fal._completed_error(response.json(), request)
        if error is not None:
            raise error
        # Reuse the strict media URL/container parser, with its already-known
        # Veo provider label; the original I2V route stays in the native receipt.
        result = fal._parse_video_result(response.json(), request, model='fal-ai/veo3.1/lite')
        result.update(reference_trial=contract, public=False)
        return result
