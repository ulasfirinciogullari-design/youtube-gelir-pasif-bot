from __future__ import annotations

import pytest

from app.services import google_cloud_test_users as service


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, *, content_status: int = 200):
        self.content_status = content_status
        self.patches: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, url: str):
        if 'cloudresourcemanager' in url:
            return FakeResponse(200, {'projectId': 'video-project'})
        if 'content-oauthconfig' in url:
            if self.content_status != 200:
                return FakeResponse(self.content_status)
            return FakeResponse(200, {
                'testUsers': [{
                    'emailAddresses': ['owner@example.com'],
                }],
            })
        return FakeResponse(self.content_status)

    def patch(self, url: str, json: dict):
        self.patches.append((url, json))
        return FakeResponse(200, json)


def test_normalize_test_user_email():
    assert service.normalize_test_user_email(' New.Channel+1@GMAIL.COM ') == 'new.channel+1@gmail.com'
    with pytest.raises(service.GoogleCloudTestUserError):
        service.normalize_test_user_email('not-an-email')


def test_state_marker_only_accepts_cloud_repair_states():
    assert service.is_cloud_test_user_state('gctu_' + ('a' * 43)) is True
    assert service.is_cloud_test_user_state('youtube_' + ('a' * 43)) is False
    assert service.is_cloud_test_user_state('') is False


def test_ensure_test_user_preserves_existing_users(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(service.httpx, 'Client', lambda **_kwargs: client)

    result = service._ensure_test_user(
        'temporary-access-token',
        'channel@gmail.com',
        '267549936202',
    )

    assert result == {
        'email': 'channel@gmail.com',
        'added': True,
        'project': 'video-project',
    }
    assert len(client.patches) == 1
    url, body = client.patches[0]
    assert url.endswith('?updateMask=testUsers')
    assert body == {
        'testUsers': [{
            'emailAddresses': ['channel@gmail.com', 'owner@example.com'],
        }],
    }


def test_ensure_test_user_reports_missing_cloud_permission(monkeypatch):
    client = FakeClient(content_status=403)
    monkeypatch.setattr(service.httpx, 'Client', lambda **_kwargs: client)

    with pytest.raises(
        service.GoogleCloudTestUserError,
        match='Cloud projesini yönetemiyor',
    ):
        service._ensure_test_user(
            'temporary-access-token',
            'channel@gmail.com',
            '267549936202',
        )
