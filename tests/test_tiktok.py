"""TikTok drafts: the token stays encrypted on the public channel-data branch, drafts respect TikTok's five-a-day
cap, and nothing about TikTok can fail a YouTube upload."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import urllib.parse

import pytest

from app.services import tiktok

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
ENV = {"TIKTOK_CLIENT_KEY": "key123", "TIKTOK_CLIENT_SECRET": "secret456"}
META = {"topic_id": "mk-019", "title": "Köpekler neden kafasını yana eğer? 🐶",
        "tags": ["köpek", "Köpekler", "hayvan bilgisi", "bilgi"]}


def _token(now=NOW, access_hours=24, refresh_days=365):
    return {"access_token": "acc", "access_expires_at": (now + timedelta(hours=access_hours)).isoformat(),
            "refresh_token": "ref", "refresh_expires_at": (now + timedelta(days=refresh_days)).isoformat(),
            "open_id": "oid", "scope": tiktok.SCOPES}


def test_the_token_is_stored_encrypted_and_only_the_secret_opens_it(tmp_path):
    tiktok.save_token(tmp_path, _token(), "secret456")
    raw = (tmp_path / tiktok.TOKEN_FILE).read_bytes()
    assert b"acc" not in raw and b"ref" not in raw
    assert tiktok.load_token(tmp_path, "secret456")["refresh_token"] == "ref"
    assert tiktok.load_token(tmp_path, "another") is None


def test_an_expired_access_token_is_refreshed_and_saved_again(tmp_path):
    tiktok.save_token(tmp_path, _token(access_hours=-1), "secret456")
    calls = []

    def oauth(fields):
        calls.append(fields)
        return {"access_token": "new", "expires_in": 86400, "refresh_token": "ref2", "refresh_expires_in": 31536000}

    assert tiktok.fresh_access(tmp_path, "key123", "secret456", now=NOW, oauth=oauth) == "new"
    assert calls == [{"client_key": "key123", "client_secret": "secret456", "grant_type": "refresh_token",
                      "refresh_token": "ref"}]
    assert tiktok.load_token(tmp_path, "secret456")["refresh_token"] == "ref2"
    # A valid one is used as it is.
    assert tiktok.fresh_access(tmp_path, "key123", "secret456", now=NOW, oauth=None) == "new"
    tiktok.save_token(tmp_path, _token(access_hours=-1, refresh_days=-1), "secret456")
    with pytest.raises(tiktok.TikTokError):
        tiktok.fresh_access(tmp_path, "key123", "secret456", now=NOW, oauth=oauth)


def test_a_short_goes_up_in_one_chunk_and_a_big_one_in_ten_megabyte_chunks():
    assert tiktok.chunks(18_000_000) == [(0, 17_999_999)]
    mb = 1024 * 1024
    parts = tiktok.chunks(95 * mb)
    assert len(parts) == 9 and parts[0] == (0, 10 * mb - 1) and parts[-1] == (80 * mb, 95 * mb - 1)
    assert all(b + 1 == c for (_, b), (c, _) in zip(parts, parts[1:]))


def test_the_upload_announces_the_size_and_sends_every_byte(tmp_path, monkeypatch):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 1000)
    calls, sent = [], []
    monkeypatch.setattr(tiktok, "_api", lambda path, access, payload=None, method="POST":
                        calls.append((path, access, payload)) or {"upload_url": "https://up", "publish_id": "p1"})
    assert tiktok.upload_draft(video, "acc", put=lambda url, body, a, b, size: sent.append((url, len(body), a, b,
                                                                                             size))) == "p1"
    assert calls == [("/post/publish/inbox/video/init/", "acc", {"source_info": {
        "source": "FILE_UPLOAD", "video_size": 1000, "chunk_size": 1000, "total_chunk_count": 1}})]
    assert sent == [("https://up", 1000, 0, 999, 1000)]


def test_the_caption_is_the_title_with_a_few_hashtags():
    assert tiktok.caption(META) == "Köpekler neden kafasını yana eğer? 🐶 #köpek #köpekler #hayvanbilgisi #keşfet #bilgi"
    assert tiktok.caption({"title": "Işık", "tags": ["IŞIK", "İnsan Vücudu"]}) == "Işık #ışık #insanvücudu #keşfet #bilgi"


def test_drafts_keep_tiktoks_daily_cap_and_spacing():
    def sent(hours_ago, status="SEND_TO_USER_INBOX"):
        return {"sent_at": (NOW - timedelta(hours=hours_ago)).isoformat(), "status": status}

    assert tiktok.skip_reason([], NOW) is None
    assert "saat önce" in tiktok.skip_reason([sent(2)], NOW)
    assert tiktok.skip_reason([sent(5)], NOW) is None
    assert "TikTok sınırı" in tiktok.skip_reason([sent(h) for h in (4.5, 9, 13.5, 18, 22.5)], NOW)
    assert tiktok.skip_reason([sent(h) for h in (4.5, 9, 13.5, 18, 25)], NOW) is None
    # A failed send uses neither the cap nor the spacing.
    assert tiktok.skip_reason([sent(1, "error: HTTP 500"), sent(2, "FAILED (file_format_check_failed)")], NOW) is None


def _result(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    return {"video": video, "meta": META}


def test_a_due_short_is_sent_as_a_draft_and_recorded(tmp_path):
    (tmp_path / tiktok.TOKEN_FILE).write_bytes(b"x")
    states = iter(["PROCESSING_UPLOAD", "SEND_TO_USER_INBOX"])
    entry = tiktok.share(_result(tmp_path), "yt1", tmp_path, env=ENV, now=NOW,
                         access_for=lambda d, k, s, now: "acc", upload=lambda video, access: "p1",
                         status=lambda pid, access: next(states), sleep=lambda s: None)
    assert entry["status"] == "SEND_TO_USER_INBOX" and entry["publish_id"] == "p1"
    saved = json.loads((tmp_path / tiktok.DRAFTS_FILE).read_text(encoding="utf-8"))
    assert saved == [entry] and saved[0]["youtube_id"] == "yt1" and saved[0]["caption"].startswith("Köpekler")
    # The next Short an hour later waits for the spacing.
    assert tiktok.share(_result(tmp_path), "yt2", tmp_path, env=ENV, now=NOW + timedelta(hours=1),
                        access_for=None, upload=None, status=None) is None


def test_tiktok_never_fails_the_run(tmp_path, capsys):
    assert tiktok.share(_result(tmp_path), "yt1", tmp_path, env={}) is None
    assert tiktok.share(_result(tmp_path), "yt1", tmp_path, env=ENV) is None  # not logged in yet
    assert "giriş yapılmadı" in capsys.readouterr().out
    (tmp_path / tiktok.TOKEN_FILE).write_bytes(b"x")

    def broken(video, access):
        raise tiktok.TikTokError("spam_risk_too_many_pending_share: limit")

    entry = tiktok.share(_result(tmp_path), "yt1", tmp_path, env=ENV, now=NOW,
                         access_for=lambda d, k, s, now: "acc", upload=broken, status=None, sleep=lambda s: None)
    assert entry["status"].startswith("error: spam_risk_too_many_pending_share")
    assert "gönderilemedi" in capsys.readouterr().out


def test_login_writes_the_consent_link_then_stores_the_token(tmp_path):
    assert tiktok.login(tmp_path, "", env={}) == 1
    assert tiktok.login(tmp_path, "", env=ENV) == 0
    url = json.loads((tmp_path / tiktok.LOGIN_FILE).read_text())["url"]
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert url.startswith(tiktok.AUTH_URL)
    assert query["client_key"] == ["key123"] and query["scope"] == [tiktok.SCOPES]
    assert query["redirect_uri"] == [tiktok.REDIRECT_URI] and query["response_type"] == ["code"]
    assert "secret456" not in url

    state = query["state"][0]
    codes = []

    def exchange(k, s, code, now):
        codes.append(code)
        return _token(now)

    # Only a code that came back from our own link is exchanged.
    forged = tiktok.sign_state("someone else's secret")
    for pasted in ("c0de", f"{forged} c0de", f"{state.split('.')[0]}.0000 c0de", f"{state} "):
        assert tiktok.login(tmp_path, pasted, env=ENV, exchange=exchange, name_of=None) == 1
    assert codes == []
    assert tiktok.login(tmp_path, f" {state} c0de*!1 ", env=ENV, exchange=exchange,
                        name_of=lambda access: "Zirvenin Kanunu") == 0
    assert codes == ["c0de*!1"]
    assert tiktok.load_token(tmp_path, "secret456")["access_token"] == "acc"
    assert json.loads((tmp_path / tiktok.ACCOUNT_FILE).read_text())["display_name"] == "Zirvenin Kanunu"
    assert not (tmp_path / tiktok.LOGIN_FILE).exists()


def test_the_workflow_hands_tiktok_its_keys_and_a_login_task():
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/viral_shorts_autopilot.yml").read_text()
    assert "TIKTOK_CLIENT_SECRET: ${{ secrets.TIKTOK_CLIENT_SECRET }}" in workflow
    assert "- 'tiktok'" in workflow
    assert 'python -m app.services.tiktok login --code "$KOD" --data channel-data' in workflow


def test_a_login_without_the_upload_permission_is_refused(tmp_path, capsys):
    state = tiktok.sign_state(ENV["TIKTOK_CLIENT_SECRET"])
    token = {**_token(), "scope": "user.info.basic"}
    assert tiktok.login(tmp_path, f"{state} c0de", env=ENV, exchange=lambda k, s, code, now: token,
                        name_of=lambda access: "x") == 1
    assert "video yükleme izni verilmedi" in capsys.readouterr().out
    assert not (tmp_path / tiktok.TOKEN_FILE).exists() and not (tmp_path / tiktok.ACCOUNT_FILE).exists()


def test_comedy_shorts_are_offered_to_tiktok_too():
    source = (Path(__file__).resolve().parents[1] / "comedy_factory.py").read_text()
    assert "tiktok.share(result, video_id, story.DATA_DIR)" in source
