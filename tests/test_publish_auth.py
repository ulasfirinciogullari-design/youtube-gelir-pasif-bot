import publish_to_youtube


def test_the_uploader_refreshes_without_pinning_scopes(monkeypatch):
    # A refresh that names a scope the key lacks fails with invalid_scope: the key Ulaş issued on 2026-10-17 has
    # youtube instead of youtube.readonly, and every Short failed at sign-in until the scopes were dropped.
    built = {}
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "id")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("YOUTUBE_REFRESH_TOKEN", "refresh")
    monkeypatch.setattr(publish_to_youtube, "build", lambda api, version, credentials: built.update(creds=credentials))
    publish_to_youtube.get_authenticated_service()
    assert built["creds"].refresh_token == "refresh"
    assert not built["creds"].scopes
