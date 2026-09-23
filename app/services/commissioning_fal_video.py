"""Fal queue receipts inside the existing, explicitly commissioned scene cap.

Reuses the production scene journal and authority, including its episode call
limit. Never initializes funding, clears earlier intents or replays a POST.
"""
import httpx

from app.config import settings
from app.services import commissioning_video as video
from app.services.fal_video import generate_fal_video
from app.services.fal_video_catalog import ORIGIN, build_request, quote_request, select_model


class _Journal:
    def __init__(self, scope, descriptor, secret):
        self.scope, self.descriptor, self.secret = scope, descriptor, secret
        self.key, self.identity, self.prior = video._reserve(scope, descriptor, pin_scene=True)

    def _restore(self, observed):
        from app.services.youtube_auth import _decrypt_json
        raw = _decrypt_json(observed['encrypted_response'])['response'].encode()
        video._require(video._sha(raw) == observed['response_sha256'])
        return httpx.Response(observed['http_status'], content=raw)

    def submit(self, sender, url, **kwargs):
        video._require(url == self.descriptor['route']
            and video._sha(video._raw(kwargs['json']).encode()) == self.descriptor['request_sha256']
            and kwargs['headers']['Authorization'] == 'Key ' + self.secret)
        if self.prior is not None:
            return self._restore(self.prior['create'])
        response = sender(url, **kwargs)
        self._observe('create', response)
        return response

    def _observe(self, field, response):
        raw = response.content
        video._observe(self.scope, self.key, self.identity, field,
                       raw, response.status_code, self.secret)

    def result_response(self):
        saved = self.prior.get('result') if self.prior is not None else None
        return self._restore(saved) if saved is not None else None

    def observe_result(self, response):
        self._observe('result', response)


def generate(scope, prompt, seconds, aspect_ratio):
    video._require(video.enabled_for_task()
        and seconds == scope['generation_seconds'] and aspect_ratio == scope['aspect_ratio'])
    secret = str(getattr(settings, 'fal_key', '') or '').strip()
    video._require(1 <= len(secret) <= 8192 and all(32 < ord(c) < 127 for c in secret),
                   'commissioning_fal_key_missing')
    model = select_model(seconds, getattr(settings, 'studio_fal_video_model', 'auto'))
    body = build_request(model, prompt.strip(), seconds, aspect_ratio)
    quote = quote_request(model, body)
    descriptor = {name: scope[name] for name in ('package_sha256', 'scene_index', 'narration_millis',
                                                'generation_seconds', 'aspect_ratio')}
    descriptor.update({'route': ORIGIN + '/' + model,
        'request_sha256': video._sha(video._raw(body).encode()),
        'credential_sha256': video._sha(('fal\0' + secret).encode()), 'model': model,
        'duration_seconds': int(body['duration'].removesuffix('s')),
        'max_list_cost_micro_usd': quote.maximum_micro})
    # The prior authority remains authoritative for stock-only recovery work.
    from app.services.content_plan_retained_completion import stock_only_scope
    if stock_only_scope(scope):
        raise video.CommissionedVideoUnavailable('commissioned_video_quota_stock_rescue')
    journal = _Journal(scope, descriptor, secret)
    return generate_fal_video(prompt, seconds, aspect_ratio=aspect_ratio,
                              model=model, _journal=journal)
