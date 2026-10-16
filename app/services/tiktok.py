"""
Zirvenin Kanunu Shorts sent to the channel's TikTok account as drafts.

TikTok lets an app that has not passed its content audit post only private videos, so each public Short goes to the
TikTok inbox instead (Content Posting API, video.upload scope). Ulaş opens the notification and posts it from the app,
where he can also add a trending sound. TikTok takes at most five pending drafts per account in 24 hours, so a draft
goes out every few hours rather than with every Short.

The login is a one-off: `python -m app.services.tiktok login` writes the consent link to the channel-data branch, and
running it again with what the callback page shows after consent (the link's state, a space, the code) stores the
token. The state is signed with TIKTOK_CLIENT_SECRET, so only a code from our own link is accepted, and a login that
left out video.upload is refused. The branch is public, so the token is saved encrypted with a key derived from the
same secret, which only the repository secrets hold.

A Short published before a draft was due can still be sent: `python -m app.services.tiktok share --youtube-id ID`
takes the video from the publishing run's artifact, inside GitHub Actions (the artifact is kept 14 days).
"""

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com/v2"
# A page on the repository's GitHub Pages site (branch "site") that shows the code TikTok sends back.
REDIRECT_URI = "https://ulasfirinciogullari-design.github.io/youtube-gelir-pasif-bot/tiktok-callback.html"
SCOPES = "user.info.basic,video.upload"

TOKEN_FILE = "tiktok_token.enc"
DRAFTS_FILE = "tiktok.json"
LOGIN_FILE = "tiktok_login.json"
ACCOUNT_FILE = "tiktok_account.json"

MAX_DRAFTS_PER_DAY = 5  # TikTok's cap on pending shares per account in 24 hours
MIN_HOURS_BETWEEN = 4
# A file up to 64 MB goes up as one chunk; a bigger one in 10 MB chunks, the last one taking the remainder.
SINGLE_CHUNK_MAX = 64 * 1024 * 1024
CHUNK_SIZE = 10 * 1024 * 1024
STATUS_WAITS = (5, 10, 15, 30)  # seconds before each status check
DONE_STATUSES = ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE", "FAILED")
FIXED_HASHTAGS = ("#keşfet", "#bilgi")
GITHUB_API = "https://api.github.com/repos"
SHORTS_WORKFLOW = "viral_shorts_autopilot.yml"
ARTIFACT_PREFIX = "original-short-"
UPLOADS_FILE = "uploads.json"
# The run that uploaded a Short started at most this long before the upload.
MAX_RUN_LEAD = timedelta(hours=2)
MAX_TAG_HASHTAGS = 4


class TikTokError(RuntimeError):
    pass


def _cipher(secret: str):
    from cryptography.fernet import Fernet

    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"zirve-tiktok:" + secret.encode()).digest()))


def save_token(data_dir: Path, token: dict, secret: str) -> None:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / TOKEN_FILE).write_bytes(_cipher(secret).encrypt(json.dumps(token).encode()))


def load_token(data_dir: Path, secret: str) -> dict | None:
    from cryptography.fernet import InvalidToken

    path = Path(data_dir) / TOKEN_FILE
    if not path.exists():
        return None
    try:
        return json.loads(_cipher(secret).decrypt(path.read_bytes()))
    except (InvalidToken, ValueError):
        return None


def _send(req: urllib.request.Request, timeout: int = 60) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
        if not raw:
            raise TikTokError(f"HTTP {e.code}") from e
    try:
        return json.loads(raw or b"{}")
    except ValueError as e:
        raise TikTokError(f"TikTok yanıtı okunamadı: {raw[:200]!r}") from e


def _oauth(fields: dict) -> dict:
    req = urllib.request.Request(f"{API}/oauth/token/", data=urllib.parse.urlencode(fields).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    data = _send(req)
    if "access_token" not in data:
        raise TikTokError(f"{data.get('error', 'token alınamadı')}: {data.get('error_description', '')}".strip())
    return data


def _api(path: str, access: str, payload: dict | None = None, method: str = "POST") -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{API}{path}", data=body, method=method, headers={
        "Authorization": f"Bearer {access}", "Content-Type": "application/json; charset=UTF-8"})
    data = _send(req)
    error = data.get("error") or {}
    if error.get("code", "ok") != "ok":
        raise TikTokError(f"{error.get('code')}: {error.get('message', '')}".strip())
    return data.get("data") or {}


def sign_state(secret: str, nonce: str | None = None) -> str:
    nonce = nonce or secrets.token_urlsafe(12)
    return f"{nonce}.{hmac.new(secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()[:24]}"


def state_is_ours(secret: str, state: str) -> bool:
    nonce, _, _ = state.partition(".")
    return bool(nonce) and hmac.compare_digest(sign_state(secret, nonce), state)


def login_url(client_key: str, state: str) -> str:
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_key": client_key, "scope": SCOPES, "response_type": "code",
        "redirect_uri": REDIRECT_URI, "state": state})


def _stamp(data: dict, now: datetime) -> dict:
    """Keeps the token with absolute expiry times; the access token is treated as expired 5 minutes early."""
    return {
        "access_token": data["access_token"],
        "access_expires_at": (now + timedelta(seconds=int(data.get("expires_in", 0)) - 300)).isoformat(),
        "refresh_token": data["refresh_token"],
        "refresh_expires_at": (now + timedelta(seconds=int(data.get("refresh_expires_in", 0)))).isoformat(),
        "open_id": data.get("open_id", ""),
        "scope": data.get("scope", ""),
    }


def exchange_code(client_key: str, secret: str, code: str, now: datetime | None = None) -> dict:
    return _stamp(_oauth({"client_key": client_key, "client_secret": secret, "code": code.strip(),
                          "grant_type": "authorization_code", "redirect_uri": REDIRECT_URI}),
                  now or datetime.now(timezone.utc))


def fresh_access(data_dir: Path, client_key: str, secret: str, now: datetime | None = None, oauth=_oauth) -> str:
    """The stored access token, refreshed (and saved again) once it has expired."""
    now = now or datetime.now(timezone.utc)
    token = load_token(data_dir, secret)
    if token is None:
        raise TikTokError("TikTok'a giriş yapılmamış ya da token okunamadı")
    if datetime.fromisoformat(token["access_expires_at"]) > now:
        return token["access_token"]
    if datetime.fromisoformat(token["refresh_expires_at"]) <= now:
        raise TikTokError("TikTok girişinin süresi dolmuş, yeniden izin gerekiyor")
    token = _stamp(oauth({"client_key": client_key, "client_secret": secret, "grant_type": "refresh_token",
                          "refresh_token": token["refresh_token"]}), now)
    save_token(data_dir, token, secret)
    return token["access_token"]


def account_name(access: str) -> str:
    data = _api("/user/info/?fields=open_id,display_name", access, method="GET")
    return (data.get("user") or {}).get("display_name", "")


def chunks(size: int) -> list[tuple[int, int]]:
    """(first byte, last byte) of each upload chunk."""
    if size <= SINGLE_CHUNK_MAX:
        return [(0, size - 1)]
    count = size // CHUNK_SIZE
    return [(i * CHUNK_SIZE, size - 1 if i == count - 1 else (i + 1) * CHUNK_SIZE - 1) for i in range(count)]


def _put(url: str, body: bytes, first: int, last: int, size: int) -> None:
    req = urllib.request.Request(url, data=body, method="PUT", headers={
        "Content-Type": "video/mp4", "Content-Length": str(len(body)),
        "Content-Range": f"bytes {first}-{last}/{size}"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        raise TikTokError(f"Video parçası yüklenemedi: HTTP {e.code} {e.read()[:200]!r}") from e


def upload_draft(video: Path, access: str, put=_put) -> str:
    """Sends the video to the account's TikTok inbox; returns the publish id."""
    size = Path(video).stat().st_size
    parts = chunks(size)
    data = _api("/post/publish/inbox/video/init/", access, {"source_info": {
        "source": "FILE_UPLOAD", "video_size": size, "chunk_size": parts[0][1] + 1,
        "total_chunk_count": len(parts)}})
    with open(video, "rb") as f:
        for first, last in parts:
            f.seek(first)
            put(data["upload_url"], f.read(last - first + 1), first, last, size)
    return data["publish_id"]


def draft_status(publish_id: str, access: str) -> str:
    data = _api("/post/publish/status/fetch/", access, {"publish_id": publish_id})
    status = data.get("status", "")
    return f"{status} ({data['fail_reason']})" if status == "FAILED" and data.get("fail_reason") else status


def caption(meta: dict) -> str:
    """The text to paste when posting the draft: the YouTube title plus a few hashtags from its tags."""
    tags = []
    for tag in meta.get("tags") or []:
        word = re.sub(r"[^\w]", "", str(tag).replace("I", "ı").replace("İ", "i").lower())
        if word and f"#{word}" not in tags and f"#{word}" not in FIXED_HASHTAGS:
            tags.append(f"#{word}")
    return " ".join([meta.get("title", "").strip(), *tags[:MAX_TAG_HASHTAGS], *FIXED_HASHTAGS]).strip()


def load_drafts(data_dir: Path) -> list[dict]:
    path = Path(data_dir) / DRAFTS_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def skip_reason(drafts: list[dict], now: datetime, spacing: bool = True) -> str | None:
    """Why no draft should go out now, or None. Failed sends count toward neither limit. A Short sent by hand
    (spacing=False) only has to fit TikTok's daily cap."""
    sent = []
    for draft in drafts:
        if str(draft.get("status", "")).startswith(("FAILED", "error")):
            continue
        try:
            sent.append(datetime.fromisoformat(draft["sent_at"]))
        except (KeyError, TypeError, ValueError):
            continue
    today = [t for t in sent if now - t < timedelta(hours=24)]
    if len(today) >= MAX_DRAFTS_PER_DAY:
        return f"son 24 saatte {len(today)} taslak gönderildi (TikTok sınırı {MAX_DRAFTS_PER_DAY})"
    if spacing and sent and now - max(sent) < timedelta(hours=MIN_HOURS_BETWEEN):
        hours = (now - max(sent)).total_seconds() / 3600
        return f"son taslak {hours:.1f} saat önce gönderildi, {MIN_HOURS_BETWEEN} saat bekleniyor"
    return None


def share(result: dict, youtube_id: str, data_dir: Path, env=os.environ, now: datetime | None = None,
          access_for=fresh_access, upload=upload_draft, status=draft_status, sleep=time.sleep,
          spacing: bool = True) -> dict | None:
    """Sends a published Short to TikTok as a draft when one is due. Never raises: TikTok must not fail the run."""
    client_key, secret = env.get("TIKTOK_CLIENT_KEY", ""), env.get("TIKTOK_CLIENT_SECRET", "")
    if not client_key or not secret:
        print("ℹ️ TikTok anahtarları tanımlı değil, TikTok taslağı atlandı")
        return None
    if not (Path(data_dir) / TOKEN_FILE).exists():
        print("ℹ️ TikTok'a giriş yapılmadı, TikTok taslağı atlandı")
        return None
    now = now or datetime.now(timezone.utc)
    drafts = load_drafts(data_dir)
    reason = skip_reason(drafts, now, spacing=spacing)
    if reason:
        print(f"ℹ️ TikTok taslağı atlandı: {reason}")
        return None
    meta = result["meta"]
    entry = {"youtube_id": youtube_id, "topic_id": meta.get("topic_id", ""), "title": meta.get("title", ""),
             "caption": caption(meta), "sent_at": now.isoformat(timespec="seconds")}
    try:
        access = access_for(data_dir, client_key, secret, now=now)
        entry["publish_id"] = upload(result["video"], access)
        state = "PROCESSING_UPLOAD"
        for wait in STATUS_WAITS:
            sleep(wait)
            state = status(entry["publish_id"], access)
            if state.startswith(DONE_STATUSES):
                break
        entry["status"] = state
    except (TikTokError, urllib.error.URLError, OSError, ValueError, KeyError) as e:
        entry["status"] = f"error: {str(e)[:200]}"
    drafts.append(entry)
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    (Path(data_dir) / DRAFTS_FILE).write_text(json.dumps(drafts, ensure_ascii=False, indent=2), encoding="utf-8")
    if entry["status"].startswith(("FAILED", "error")):
        print(f"⚠️ TikTok taslağı gönderilemedi: {entry['status']}")
    else:
        print(f"📱 TikTok taslağı gönderildi ({entry['status']}): {entry['caption']}")
    return entry


def _github(url: str, token: str, raw: bool = False):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    # The artifact link redirects to blob storage, which must not get the GitHub token.
    req.add_unredirected_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = resp.read()
    return body if raw else json.loads(body)


def _unpack(archive: bytes, out_dir: Path, topic_id: str) -> tuple[Path, dict] | None:
    """The Short on this topic and its meta from a publishing run's artifact zip; other files are left out."""
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        names = {Path(n).name: n for n in z.namelist()}
        metas = [b for b in names if re.fullmatch(r"(ORIGINAL|COMEDY)_[\w-]+_meta\.json", b)]
        for base in metas:
            video = base.removesuffix("_meta.json") + ".mp4"
            meta = json.loads(z.read(names[base]).decode("utf-8"))
            if video not in names or meta.get("topic_id") != topic_id:
                continue
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / video).write_bytes(z.read(names[video]))
            return out_dir / video, meta
    return None


def find_short(upload: dict, out_dir: Path, repo: str, token: str, get=_github) -> tuple[Path, dict] | None:
    """Downloads the uploaded Short from the artifact of the run that published it: the newest successful Shorts
    run that started before the upload and whose meta has the same topic."""
    uploaded_at = datetime.fromisoformat(upload["uploaded_at"])
    runs = get(f"{GITHUB_API}/{repo}/actions/workflows/{SHORTS_WORKFLOW}/runs?status=success&per_page=100",
               token)["workflow_runs"]
    for run in sorted(runs, key=lambda r: r["created_at"], reverse=True):
        started = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        if started > uploaded_at:
            continue
        if uploaded_at - started > MAX_RUN_LEAD:
            break
        artifacts = get(f"{GITHUB_API}/{repo}/actions/runs/{run['id']}/artifacts", token)["artifacts"]
        for artifact in artifacts:
            if artifact["name"] != f"{ARTIFACT_PREFIX}{run['id']}" or artifact.get("expired"):
                continue
            found = _unpack(get(artifact["archive_download_url"], token, raw=True), Path(out_dir),
                            upload.get("topic_id", ""))
            if found:
                return found
    return None


def share_existing(youtube_id: str, data_dir: Path, out_dir: Path, env=os.environ, find=find_short, **kwargs) -> int:
    """Sends an already published Short to TikTok as a draft right away, without waiting for the spacing."""
    data_dir = Path(data_dir)
    try:
        uploads = json.loads((data_dir / UPLOADS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        uploads = []
    upload = next((u for u in uploads if isinstance(u, dict) and u.get("video_id") == youtube_id), None)
    if upload is None:
        print(f"❌ {youtube_id} kanal hafızasında (uploads.json) yok")
        return 1
    for draft in load_drafts(data_dir):
        if draft.get("youtube_id") == youtube_id and not str(draft.get("status", "")).startswith(("FAILED", "error")):
            print(f"ℹ️ {youtube_id} zaten TikTok'a gönderilmiş ({draft.get('status')})")
            return 0
    try:
        found = find(upload, Path(out_dir), env.get("GITHUB_REPOSITORY", ""), env.get("GITHUB_TOKEN", ""))
    except (urllib.error.URLError, OSError, ValueError, KeyError, zipfile.BadZipFile) as e:
        print(f"❌ Videonun yayın paketi indirilemedi: {e}")
        return 1
    if found is None:
        print(f"❌ {youtube_id} için yayın paketi bulunamadı (paketler 14 gün saklanıyor)")
        return 1
    video, meta = found
    entry = share({"video": video, "meta": meta}, youtube_id, data_dir, env=env, spacing=False, **kwargs)
    return 0 if entry and not entry["status"].startswith(("FAILED", "error")) else 1


def login(data_dir: Path, code: str, env=os.environ, exchange=exchange_code, name_of=account_name) -> int:
    """Without a code, writes the consent link; with the code from the callback page, stores the token."""
    client_key, secret = env.get("TIKTOK_CLIENT_KEY", ""), env.get("TIKTOK_CLIENT_SECRET", "")
    if not client_key or not secret:
        print("❌ TIKTOK_CLIENT_KEY ve TIKTOK_CLIENT_SECRET GitHub secrets'a eklenmeli")
        return 1
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    if not code.strip():
        url = login_url(client_key, sign_state(secret))
        (data_dir / LOGIN_FILE).write_text(json.dumps({"url": url, "created_at": now.isoformat(timespec="seconds")},
                                                      indent=2), encoding="utf-8")
        print(f"🔗 TikTok izin linki: {url}")
        return 0
    state, _, code = code.strip().partition(" ")
    if not state_is_ours(secret, state) or not code.strip():
        print("❌ Bu kod bizim izin linkimizden gelmedi; izin sayfasındaki metnin tamamını kopyala")
        return 1
    try:
        token = exchange(client_key, secret, code, now=now)
        if "video.upload" not in [s.strip() for s in token.get("scope", "").split(",")]:
            raise TikTokError(f"video yükleme izni verilmedi (verilen: {token.get('scope', '')}); "
                              "izin ekranında video yükleme seçeneği açık kalmalı")
        save_token(data_dir, token, secret)
        name = name_of(token["access_token"])
    except (TikTokError, urllib.error.URLError, OSError) as e:
        print(f"❌ TikTok girişi tamamlanamadı: {e}")
        return 1
    (data_dir / ACCOUNT_FILE).write_text(json.dumps({
        "display_name": name, "scope": token.get("scope", ""), "connected_at": now.isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (data_dir / LOGIN_FILE).unlink(missing_ok=True)
    print(f"✅ TikTok hesabı bağlandı: {name} ({token.get('scope', '')})")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="TikTok login and drafts by hand for Zirvenin Kanunu")
    parser.add_argument("command", choices=["login", "share"])
    parser.add_argument("--code", default="", help="İzin sayfasının gösterdiği metin (boş = izin linki üret)")
    parser.add_argument("--youtube-id", default="", help="share: TikTok'a taslak olarak gidecek yayındaki Short")
    parser.add_argument("--data", default="channel-data", help="Kanal hafızası klasörü")
    parser.add_argument("--out", default="output/tiktok", help="share: yayın paketinin açılacağı klasör")
    args = parser.parse_args(argv)
    if args.command == "share":
        return share_existing(args.youtube_id.strip(), Path(args.data), Path(args.out))
    return login(Path(args.data), args.code)


if __name__ == "__main__":
    sys.exit(main())
