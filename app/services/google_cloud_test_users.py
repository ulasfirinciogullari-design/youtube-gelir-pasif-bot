from __future__ import annotations

from datetime import datetime, timezone
import re
import secrets
from typing import Any

from google_auth_oauthlib.flow import Flow
import httpx

from app.config import settings
from app.services.youtube_auth import (
    STATE_TTL_SECONDS,
    YouTubeAuthError,
    _binding_digest,
    _client_config,
    _decrypt_json,
    _encrypt_json,
    _redis,
    _validate_redirect_uri,
)


CLOUD_SCOPE = 'https://www.googleapis.com/auth/cloud-platform'
CLOUD_STATE_PREFIX = 'gctu_'
CLOUD_STATE_KEY_PREFIX = 'youtube_studio:oauth:cloud_test_user_state:v1:'

_STATE_PATTERN = re.compile(r'^gctu_[A-Za-z0-9_-]{40,128}$')
_EMAIL_PATTERN = re.compile(
    r'^[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]+@'
    r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?'
    r'(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$'
)


class GoogleCloudTestUserError(YouTubeAuthError):
    """Safe, user-displayable Google Cloud test-user update failure."""


def normalize_test_user_email(value: str) -> str:
    email = str(value or '').strip().casefold()
    if len(email) > 254 or not _EMAIL_PATTERN.fullmatch(email):
        raise GoogleCloudTestUserError('Geçerli bir Google hesap e-postası girilmedi')
    return email


def is_cloud_test_user_state(state: str) -> bool:
    return bool(_STATE_PATTERN.fullmatch(str(state or '')))


def _state_key(state: str, binding_digest: str) -> str:
    return f'{CLOUD_STATE_KEY_PREFIX}{state}:{binding_digest}'


def _project_number() -> str:
    match = re.match(r'^(\d+)-', str(settings.google_client_id or '').strip())
    if not match:
        raise GoogleCloudTestUserError('Google Cloud proje numarası OAuth istemcisinden okunamadı')
    return match.group(1)


def build_test_user_authorization_url(target_email: str, session_binding: str) -> str:
    target_email = normalize_test_user_email(target_email)
    redirect_uri = _validate_redirect_uri(settings.google_redirect_uri)
    binding_digest = _binding_digest(session_binding)
    state = CLOUD_STATE_PREFIX + secrets.token_urlsafe(32)
    flow = Flow.from_client_config(
        _client_config(),
        scopes=[CLOUD_SCOPE],
        state=state,
        autogenerate_code_verifier=True,
    )
    flow.redirect_uri = redirect_uri
    authorization_url, returned_state = flow.authorization_url(
        access_type='online',
        include_granted_scopes='false',
        prompt='consent select_account',
    )
    if returned_state != state or not flow.code_verifier:
        raise GoogleCloudTestUserError('Google yönetim izni başlatılamadı')
    payload = _encrypt_json({
        'version': 1,
        'redirect_uri': redirect_uri,
        'code_verifier': flow.code_verifier,
        'binding_digest': binding_digest,
        'target_email': target_email,
        'project_number': _project_number(),
        'created_at': datetime.now(timezone.utc).isoformat(),
    })
    try:
        stored = _redis().set(
            _state_key(state, binding_digest),
            payload,
            ex=STATE_TTL_SECONDS,
            nx=True,
        )
    except Exception as exc:
        raise GoogleCloudTestUserError('Google yönetim oturumu kaydedilemedi') from exc
    if not stored:
        raise GoogleCloudTestUserError('Google yönetim oturumu başlatılamadı')
    return authorization_url


def discard_test_user_authorization_state(state: str, session_binding: str) -> None:
    if not is_cloud_test_user_state(state):
        return
    try:
        binding_digest = _binding_digest(session_binding)
        _redis().delete(_state_key(state, binding_digest))
    except YouTubeAuthError:
        return
    except Exception:
        return


def _consume_state(state: str, session_binding: str) -> dict[str, Any]:
    if not is_cloud_test_user_state(state):
        raise GoogleCloudTestUserError('Google yönetim oturumu geçersiz veya süresi dolmuş')
    binding_digest = _binding_digest(session_binding)
    try:
        encrypted = _redis().getdel(_state_key(state, binding_digest))
    except Exception as exc:
        raise GoogleCloudTestUserError('Google yönetim oturumu okunamadı') from exc
    if not encrypted:
        raise GoogleCloudTestUserError('Google yönetim oturumu geçersiz veya süresi dolmuş')
    payload = _decrypt_json(str(encrypted))
    if payload.get('version') != 1:
        raise GoogleCloudTestUserError('Google yönetim oturumu geçersiz veya süresi dolmuş')
    if payload.get('redirect_uri') != _validate_redirect_uri(settings.google_redirect_uri):
        raise GoogleCloudTestUserError('Google yönetim oturumu geçersiz veya süresi dolmuş')
    if payload.get('binding_digest') != binding_digest:
        raise GoogleCloudTestUserError('Google yönetim tarayıcı doğrulaması başarısız')
    payload['target_email'] = normalize_test_user_email(str(payload.get('target_email') or ''))
    project_number = str(payload.get('project_number') or '')
    if not project_number.isdigit() or project_number != _project_number():
        raise GoogleCloudTestUserError('Google Cloud proje doğrulaması başarısız')
    verifier = str(payload.get('code_verifier') or '')
    if not 43 <= len(verifier) <= 128:
        raise GoogleCloudTestUserError('Google yönetim oturumu geçersiz veya süresi dolmuş')
    return payload


def _project_candidates(client: httpx.Client, project_number: str) -> list[str]:
    candidates: list[str] = []
    try:
        response = client.get(
            f'https://cloudresourcemanager.googleapis.com/v3/projects/{project_number}'
        )
        if response.status_code == 200:
            payload = response.json()
            project_id = str(payload.get('projectId') or '').strip()
            if project_id:
                candidates.append(project_id)
    except (httpx.HTTPError, ValueError, TypeError):
        pass
    if project_number not in candidates:
        candidates.append(project_number)
    return candidates


def _content_test_users(data: dict[str, Any]) -> tuple[list[dict[str, Any]], set[str]]:
    raw_groups = data.get('testUsers')
    groups = [dict(item) for item in raw_groups if isinstance(item, dict)] if isinstance(raw_groups, list) else []
    existing: set[str] = set()
    for group in groups:
        addresses = group.get('emailAddresses')
        if isinstance(addresses, list):
            existing.update(
                str(item).strip().casefold()
                for item in addresses
                if isinstance(item, str) and item.strip()
            )
    return groups, existing


def _try_content_api(
    client: httpx.Client,
    project: str,
    target_email: str,
) -> tuple[bool, bool, str | None]:
    url = f'https://content-oauthconfig.googleapis.com/v1/projects/{project}/oauthConfig'
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        return False, False, f'network:{type(exc).__name__}'
    if response.status_code != 200:
        return False, False, f'http:{response.status_code}'
    try:
        data = response.json()
    except ValueError:
        return False, False, 'invalid-json'
    if not isinstance(data, dict):
        return False, False, 'invalid-body'
    groups, existing = _content_test_users(data)
    if target_email in existing:
        return True, False, None
    if groups:
        addresses = groups[0].get('emailAddresses')
        clean_addresses = [
            str(item).strip().casefold()
            for item in addresses
            if isinstance(item, str) and item.strip()
        ] if isinstance(addresses, list) else []
        groups[0]['emailAddresses'] = sorted(set(clean_addresses) | {target_email})
    else:
        groups = [{'emailAddresses': [target_email]}]
    try:
        updated = client.patch(
            f'{url}?updateMask=testUsers',
            json={'testUsers': groups},
        )
    except httpx.HTTPError as exc:
        return False, False, f'network:{type(exc).__name__}'
    if updated.status_code in {200, 201}:
        return True, True, None
    return False, False, f'http:{updated.status_code}'


def _try_legacy_api(
    client: httpx.Client,
    project: str,
    target_email: str,
) -> tuple[bool, bool, str | None]:
    url = f'https://oauthconfig.googleapis.com/v1/projects/{project}/oauthConsentScreen'
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        return False, False, f'network:{type(exc).__name__}'
    if response.status_code != 200:
        return False, False, f'http:{response.status_code}'
    try:
        data = response.json()
    except ValueError:
        return False, False, 'invalid-json'
    if not isinstance(data, dict):
        return False, False, 'invalid-body'
    raw_users = data.get('testUsers')
    existing = {
        str(item).strip().casefold()
        for item in raw_users
        if isinstance(item, str) and item.strip()
    } if isinstance(raw_users, list) else set()
    if target_email in existing:
        return True, False, None
    try:
        updated = client.patch(
            f'{url}?updateMask=testUsers',
            json={'testUsers': sorted(existing | {target_email})},
        )
    except httpx.HTTPError as exc:
        return False, False, f'network:{type(exc).__name__}'
    if updated.status_code in {200, 201}:
        return True, True, None
    return False, False, f'http:{updated.status_code}'


def _ensure_test_user(access_token: str, target_email: str, project_number: str) -> dict[str, Any]:
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json',
        'Content-Type': 'application/json',
    }
    failures: list[str] = []
    try:
        with httpx.Client(headers=headers, timeout=20.0, follow_redirects=False) as client:
            candidates = _project_candidates(client, project_number)
            for project in candidates:
                for updater in (_try_content_api, _try_legacy_api):
                    ok, added, failure = updater(client, project, target_email)
                    if ok:
                        return {
                            'email': target_email,
                            'added': added,
                            'project': project,
                        }
                    if failure:
                        failures.append(failure)
    except httpx.HTTPError as exc:
        raise GoogleCloudTestUserError('Google Cloud bağlantısı kurulamadı') from exc

    statuses = {
        int(value.split(':', 1)[1])
        for value in failures
        if value.startswith('http:') and value.split(':', 1)[1].isdigit()
    }
    if 401 in statuses:
        raise GoogleCloudTestUserError('Google yönetim izni geçersiz veya süresi dolmuş')
    if 403 in statuses:
        raise GoogleCloudTestUserError(
            'Seçilen Google hesabı bu Cloud projesini yönetemiyor. Projeyi kurduğun ana hesabı seç.'
        )
    if 404 in statuses:
        raise GoogleCloudTestUserError('OAuth projesinin test kullanıcı ayarı bulunamadı')
    raise GoogleCloudTestUserError('Google test kullanıcısı otomatik eklenemedi')


def complete_test_user_authorization(
    code: str,
    state: str,
    session_binding: str,
) -> dict[str, Any]:
    code = str(code or '').strip()
    if not code or len(code) > 4096:
        raise GoogleCloudTestUserError('Google yönetim kodu eksik veya geçersiz')
    payload = _consume_state(state, session_binding)
    flow = Flow.from_client_config(
        _client_config(),
        scopes=[CLOUD_SCOPE],
        state=state,
    )
    flow.redirect_uri = str(payload['redirect_uri'])
    flow.code_verifier = str(payload['code_verifier'])
    try:
        flow.fetch_token(code=code)
    except Exception as exc:
        raise GoogleCloudTestUserError('Google yönetim izni doğrulanamadı') from exc
    credentials = flow.credentials
    access_token = str(credentials.token or '').strip()
    if not access_token:
        raise GoogleCloudTestUserError('Google yönetim erişim anahtarı alınamadı')
    granted_scopes = set(credentials.scopes or [])
    if granted_scopes and CLOUD_SCOPE not in granted_scopes:
        raise GoogleCloudTestUserError('Google Cloud yönetim izni verilmedi')
    return _ensure_test_user(
        access_token,
        str(payload['target_email']),
        str(payload['project_number']),
    )
