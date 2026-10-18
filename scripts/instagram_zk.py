"""
Zirvenin Kanunu on Instagram: the best Shorts as Reels, and replies to comments and direct messages.

Ulaş asked on 2026-10-18 for what Ya Olsaydı? has on Instagram, with Zirvenin Kanunu's own account and Meta app.
Every Short is also rendered as an Instagram copy (original_factory.instagram_subtitles: the hook inside the 4:5
feed crop, captions clear of the buttons, no @handle stamp) that travels in the publishing run's artifact, kept 14
days. At each Reel hour (ZK_IG_SAATLER, UTC; TR 13, 17 and 21 by default, an hour after Ya Olsaydı?'s Reels) the
watch posts the copy of the Short with the most YouTube views that is not on Instagram yet.

  python3 scripts/instagram_zk.py --kontrol           account, permissions and Reel stats -> instagram_kontrol.json
  python3 scripts/instagram_zk.py --reel [YOUTUBE_ID] post one Reel now (the best waiting Short without an id)
  python3 scripts/instagram_zk.py --deneme            write the replies to instagram_preview.json, post nothing
  scripts/instagram_watch.py runs cevap() every minute and reel_slot() at the Reel hours.

Needs the ZK_IG_TOKEN secret: a long-lived user token of the "Instagram API with Instagram login" (60 days) from the
"Zirvenin Kanunu" Meta app, never Ya Olsaydı?'s. Actions cannot write secrets and channel-data is public, so the
watch refreshes the token once a week and keeps the fresh one in channel-data/instagram_token.enc, encrypted with a
key derived from the secret; a new secret simply replaces it. Instagram fetches a Reel's video from a public URL, so
the video and cover sit on a temporary GitHub release for the few minutes Instagram needs.
Comments and DMs are judged by the YouTube responder's Gemini prompt (scripts/comment_replies.py), reworded for
Instagram. The branch is public: people are stored as a short hash, DM text never.
"""

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(HERE.parent))
import comment_replies  # noqa: E402

API = "https://graph.instagram.com/v25.0"
GITHUB_API = "https://api.github.com/repos"
STATE_FILE = "instagram.json"
REPORT_FILE = "instagram_report.json"
PREVIEW_FILE = "instagram_preview.json"
CHECK_FILE = "instagram_kontrol.json"
KEY_FILE = "instagram_token.enc"
DAILY = 1000  # the caps Ulaş set for Ya Olsaydı? on 2026-10-18 (our own safety limits, not Meta's)
PER_RUN = 20
PER_PERSON = 20  # replies to one person in 24 hours, so one chatter cannot use up the day
MAX_AGE_DAYS = 60
DM_WINDOW = 23 * 3600  # Instagram allows an answer within 24 hours of the person's message
REFRESH_AFTER = 7 * 86400
REEL_HOURS = (10, 14, 18)  # UTC; Ya Olsaydı?'s Reels go at 9, 13 and 17
SLOT_GRACE = timedelta(hours=3)
ARTIFACT_DAYS = 13  # the publishing run's artifact, with the Instagram copy, is kept 14 days
MAX_RUN_LEAD = 2 * 3600  # a Short's meta is written at most this long before its upload
FIXED_TAGS = ("#bilgi", "#merak", "#bilim", "#ilginçbilgiler", "#keşfet")
MAX_TAGS = 5  # Instagram is testing a 5-hashtag cap; more reads as spam
CACHE_TTL = 3600

# The watch runs cevap() every minute for hours: CACHE = {} there, so unchanged media and conversations cost no calls.
CACHE = None
# Only the watch writes the key file; one-off runs set this to False.
REFRESH = True


class IGError(RuntimeError):
    pass


class ShortError(IGError):
    """This Short cannot go to Instagram (no artifact, no Instagram copy); the next one can."""


def _open(request: urllib.request.Request, timeout: float) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def http(method: str, url: str, params=None, form=None, body=None, data=None, headers=None, timeout: float = 60):
    """-> (status, bytes). `form` is sent url-encoded, `body` as JSON, `data` as it is."""
    headers = dict(headers or {})
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    if form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if data is None and method in ("POST", "PUT"):
        data = b""
    return _open(urllib.request.Request(url, data=data, method=method, headers=headers), timeout)


def call(tok: str, method: str, path: str, params=None, form=None, body=None, timeout: float = 60) -> dict:
    status, raw = http(method, f"{API}/{path.lstrip('/')}", params=params, form=form, body=body, timeout=timeout,
                       headers={"Authorization": f"Bearer {tok}"})
    if status >= 400:
        raise IGError(f"{method} {path.split('?')[0]} {status}: {raw[:400].decode('utf-8', 'replace')}")
    return json.loads(raw) if raw.strip() else {}


def rate_limited(error) -> bool:
    """Instagram's 'too many calls' answers: HTTP 429 or error codes 4, 17, 32, 613."""
    text = str(error)
    return " 429:" in text or bool(re.search(r'"code":\s*(4|17|32|613)\b', text))


def iso(moment: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if moment is None else moment, timezone.utc).isoformat(
        timespec="seconds")


# ---------------------------------------------------------------- state and token

def load_state(data_dir: Path) -> dict:
    try:
        state = json.loads((Path(data_dir) / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    return state if isinstance(state, dict) else {}


def dump(state: dict) -> str:
    return json.dumps(state, ensure_ascii=False, indent=1)


def _cipher(secret: str):
    from cryptography.fernet import Fernet

    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"zirve-ig:" + secret.encode()).digest()))


def token(data_dir: Path, report: dict, env=os.environ) -> str | None:
    """The current access token, refreshed once a week; None without the ZK_IG_TOKEN secret."""
    secret = env.get("ZK_IG_TOKEN", "").strip()
    if not secret:
        return None
    keyfile = Path(data_dir) / KEY_FILE
    tok = {"t": secret, "at": 0}
    if keyfile.exists():
        from cryptography.fernet import InvalidToken
        try:
            tok = json.loads(_cipher(secret).decrypt(keyfile.read_bytes()))
        except (InvalidToken, ValueError):
            pass  # made from an older secret: the new secret wins
    if REFRESH and time.time() - tok.get("at", 0) > REFRESH_AFTER:
        status, raw = http("GET", "https://graph.instagram.com/refresh_access_token", timeout=30,
                           params={"grant_type": "ig_refresh_token", "access_token": tok["t"]})
        if status == 200:
            data = json.loads(raw)
            tok = {"t": data["access_token"], "at": time.time(),
                   "expires": time.time() + int(data.get("expires_in", 0))}
            keyfile.write_bytes(_cipher(secret).encrypt(json.dumps(tok).encode()))
            print("🔑 Instagram anahtarı yenilendi")
        else:  # a token under 24 hours old cannot be refreshed yet; it still works
            report.setdefault("warnings", []).append(f"anahtar yenilenemedi ({status}): {raw[:200]!r}")
    if tok.get("expires"):
        report["anahtar_bitis"] = iso(tok["expires"])[:16]
    return tok["t"]


def me(tok: str, state: dict) -> dict:
    if CACHE is not None and CACHE.get("me"):
        data = CACHE["me"]
    else:
        data = call(tok, "GET", "me", params={"fields": "user_id,username,account_type,media_count,followers_count"})
        if CACHE is not None:
            CACHE["me"] = data
    state["user"] = {k: data.get(k) for k in ("user_id", "username", "account_type")}
    return data


def git(data_dir: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(data_dir), *args], capture_output=True, text=True)


def sync(data_dir: Path) -> None:
    """Takes the channel-data branch as it is on GitHub."""
    if git(data_dir, "fetch", "-q", "origin", "+channel-data:refs/remotes/origin/channel-data").returncode == 0:
        git(data_dir, "reset", "-q", "--hard", "origin/channel-data")


def push(data_dir: Path, make, message: str) -> bool:
    """Writes make()'s {file name: text or bytes} on top of the newest channel-data and pushes -> True when it is
    on GitHub. make() runs after each sync, so it can merge with what other runs pushed meanwhile."""
    for attempt in range(4):
        sync(data_dir)
        files = make()
        for name, content in files.items():
            path = Path(data_dir) / name
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content, encoding="utf-8")
        git(data_dir, "add", *files)
        if git(data_dir, "diff", "--cached", "--quiet").returncode == 0:
            return True
        git(data_dir, "-c", "user.name=zirve-bot", "-c",
            "user.email=41898282+github-actions[bot]@users.noreply.github.com", "commit", "-q", "-m", message)
        if git(data_dir, "push", "-q", "origin", "HEAD:channel-data").returncode == 0:
            return True
        time.sleep(5 * (attempt + 1))
    return False


# ---------------------------------------------------------------- Reels

def caption(meta: dict) -> str:
    """The Reel's caption: the title, the short explanation from the YouTube description, the pinned question as the
    call to comment, and at most five hashtags. No @handle: on Instagram it would link to whoever holds that name."""
    paragraphs = [p.strip() for p in str(meta.get("description") or "").split("\n\n") if p.strip()]
    question = " ".join(str(meta.get("pinned_comment") or "").split())
    summary = next((p for p in paragraphs[1:] if not p.startswith(("#", "Seri:")) and p != question), "")
    if not summary:
        summary = " ".join(str(s.get("text") or "") for s in meta.get("scenes") or [] if isinstance(s, dict))
    lines = [str(meta.get("title") or "").strip(), ""]
    if summary:
        lines += [" ".join(summary.split())[:600], ""]
    if question:
        lines += [f"{question} Yorumlara yaz 👇", ""]
    lines += ["Daha fazlası YouTube'da: Zirvenin Kanunu (link profilde)", ""]
    tags, seen = [], set()
    for tag in re.findall(r"#[\w]+", " ".join(paragraphs)) + list(FIXED_TAGS):
        if tag.lower() not in seen and tag.lower() != "#shorts":
            seen.add(tag.lower())
            tags.append(tag)
    text = "\n".join(lines + [" ".join(tags[:MAX_TAGS])])
    return re.sub(r"@(?=\w)", "", text).replace("<", "‹").replace(">", "›")[:2200]


def _github(method: str, url: str, env=os.environ, **kw) -> dict:
    status, raw = http(method, url, timeout=kw.pop("timeout", 120), headers={
        "Authorization": f"Bearer {env.get('GITHUB_TOKEN', '')}", "Accept": "application/vnd.github+json",
        **kw.pop("headers", {})}, **kw)
    if status >= 400 and not (method == "DELETE" and status == 404):
        raise IGError(f"GitHub {method} {status}: {raw[:300].decode('utf-8', 'replace')}")
    return json.loads(raw) if raw.strip() else {}


def host(files: dict, tag: str, env=os.environ) -> tuple[dict, int]:
    """Puts local files on a temporary GitHub release -> ({name: public url}, release id)."""
    repo = env["GITHUB_REPOSITORY"]
    release = _github("POST", f"{GITHUB_API}/{repo}/releases", env, body={
        "tag_name": tag, "name": tag, "prerelease": True,
        "body": "Instagram'ın videoyu alması için geçici; birkaç dakika içinde silinir."})
    urls = {}
    try:
        for name, (path, ctype) in files.items():
            asset = _github("POST", f"https://uploads.github.com/repos/{repo}/releases/{release['id']}/assets", env,
                            params={"name": name}, data=Path(path).read_bytes(), headers={"Content-Type": ctype},
                            timeout=300)
            urls[name] = asset["browser_download_url"]
    except Exception:
        unhost(release["id"], tag, env)
        raise
    return urls, release["id"]


def unhost(release_id: int, tag: str, env=os.environ) -> None:
    repo = env["GITHUB_REPOSITORY"]
    for url in (f"{GITHUB_API}/{repo}/releases/{release_id}", f"{GITHUB_API}/{repo}/git/refs/tags/{tag}"):
        try:
            _github("DELETE", url, env)
        except IGError as e:
            print(f"⚠️ Geçici sürüm silinemedi: {e}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _direct(url: str) -> str:
    """GitHub answers a release download with a redirect to a short-lived signed link; that link itself."""
    try:
        urllib.request.build_opener(_NoRedirect).open(urllib.request.Request(url, method="HEAD"), timeout=30)
    except urllib.error.HTTPError as e:
        return e.headers.get("Location") or url
    except OSError:
        pass
    return url


def reel(tok: str, urls: dict, text: str, sleep=time.sleep) -> str:
    """Creates a Reels container and waits until Instagram has fetched and processed the video -> container id. If
    Instagram cannot fetch through GitHub's redirect, tries once more with the signed link it points to."""
    status = {}
    for attempt in (1, 2):
        u = urls if attempt == 1 else {k: _direct(v) for k, v in urls.items()}
        cid = call(tok, "POST", "me/media", form={
            "media_type": "REELS", "video_url": u["video.mp4"], "cover_url": u["kapak.jpg"],
            "caption": text, "share_to_feed": "true"})["id"]
        for _ in range(40):  # Instagram fetches and processes the video, usually within a minute or two
            sleep(15)
            status = call(tok, "GET", cid, params={"fields": "status_code,status"})
            if status.get("status_code") == "FINISHED":
                return cid
            if status.get("status_code") in ("ERROR", "EXPIRED"):
                break
        else:
            raise IGError("Instagram videoyu 10 dakikada işlemedi")
        print(f"⚠️ Instagram videoyu alamadı ({attempt}. deneme): {status.get('status')}")
    raise IGError(f"Instagram videoyu işleyemedi: {status.get('status')}")


def load_uploads(data_dir: Path) -> list[dict]:
    try:
        data = json.loads((Path(data_dir) / "uploads.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [u for u in data if isinstance(u, dict) and u.get("video_id")] if isinstance(data, list) else []


def _uploaded(upload: dict) -> float:
    try:
        return datetime.fromisoformat(upload["uploaded_at"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return 0.0


def short_meta(data_dir: Path, upload: dict) -> dict | None:
    """The meta the publishing run left in channel-data/history: same topic and title, written in the two hours
    before the upload (a topic can be made more than once)."""
    uploaded = _uploaded(upload)
    best = None
    for path in Path(data_dir, "history").glob(f"ORIGINAL_{upload.get('topic_id')}_*_meta.json"):
        match = re.search(r"_(\d+)_meta\.json$", path.name)
        stamp = int(match.group(1)) if match else 0
        if not uploaded - MAX_RUN_LEAD <= stamp <= uploaded:
            continue
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(meta, dict) and meta.get("title") == upload.get("title") and (best is None or stamp > best[0]):
            best = (stamp, meta)
    return best[1] if best else None


def candidates(data_dir: Path, state: dict, now: float) -> list[dict]:
    """Public Shorts of the last ARTIFACT_DAYS with an Instagram copy that are not on Instagram yet, newest first."""
    posted, failed = state.get("posted", {}), state.get("basarisiz", {})
    found = []
    for upload in load_uploads(data_dir):
        vid = upload["video_id"]
        if upload.get("privacy", "public") != "public" or vid in posted or vid in failed:
            continue
        if now - _uploaded(upload) > ARTIFACT_DAYS * 86400:
            continue
        meta = short_meta(data_dir, upload)
        if meta and meta.get("instagram_video"):
            found.append(upload)
    return sorted(found, key=_uploaded, reverse=True)


def youtube_views(ids: list[str], env=os.environ) -> dict:
    """Public views of up to 50 videos (1 YouTube unit)."""
    if not ids:
        return {}
    tok = comment_replies.access_token(env)
    query = urllib.parse.urlencode({"part": "statistics", "id": ",".join(ids[:50])})
    data = comment_replies._request(f"{comment_replies.YOUTUBE_API}/videos?{query}",
                                    headers={"Authorization": f"Bearer {tok}"})
    return {v["id"]: int((v.get("statistics") or {}).get("viewCount") or 0) for v in data.get("items") or []}


def publish(data_dir: Path, state: dict, upload: dict, out_dir: Path, env=os.environ, find=None,
            sleep=time.sleep) -> dict:
    """Posts one published Short's Instagram copy as a Reel -> its record in state["posted"]. Raises IGError."""
    if find is None:
        from app.services import tiktok
        find = tiktok.find_short
    report = {}
    tok = token(data_dir, report, env)
    if not tok:
        raise IGError("ZK_IG_TOKEN yok")
    try:
        found = find(upload, Path(out_dir), env.get("GITHUB_REPOSITORY", ""), env.get("GITHUB_TOKEN", ""),
                     extras=("_ig.mp4", "_thumb.jpg"))
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        raise IGError(f"yayın paketi indirilemedi: {e}") from e
    if found is None:
        raise ShortError("yayın paketi bulunamadı (paketler 14 gün saklanıyor)")
    video, meta = found
    ig, cover = video.with_name(f"{video.stem}_ig.mp4"), video.with_name(f"{video.stem}_thumb.jpg")
    if not ig.exists():
        raise ShortError("yayın paketinde Instagram kopyası yok")
    if not cover.exists():
        try:
            subprocess.run(["ffmpeg", "-y", "-ss", "0.8", "-i", str(ig), "-vframes", "1", "-q:v", "2", str(cover)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as e:
            raise ShortError(f"kapak resmi yok ve çıkarılamadı: {e}") from e
    me(tok, state)
    tag = f"ig-zk-{upload['video_id']}-{int(time.time())}"
    urls, release = host({"video.mp4": (ig, "video/mp4"), "kapak.jpg": (cover, "image/jpeg")}, tag, env)
    try:
        cid = reel(tok, urls, caption(meta), sleep=sleep)
        media_id = call(tok, "POST", "me/media_publish", form={"creation_id": cid})["id"]
    finally:
        unhost(release, tag, env)
    link = call(tok, "GET", media_id, params={"fields": "permalink"}).get("permalink")
    record = {"media_id": media_id, "url": link, "at": round(time.time()), "title": upload.get("title", ""),
              "topic_id": upload.get("topic_id", "")}
    state.setdefault("posted", {})[upload["video_id"]] = record
    print(f"📸 Instagram Reel: {link}")
    return record


def best_first(data_dir: Path, state: dict, now: float, env=os.environ, views=youtube_views) -> list[dict]:
    """The waiting Shorts, the one with the most YouTube views first; without views, newest first."""
    waiting = candidates(data_dir, state, now)
    try:
        counts = views([u["video_id"] for u in waiting], env)
    except Exception as e:  # noqa: BLE001  (the newest one is a fine choice too)
        print(f"⚠️ YouTube izlenmeleri okunamadı: {str(e)[:200]}")
        counts = {}
    return sorted(waiting, key=lambda u: counts.get(u["video_id"], 0), reverse=True)


def due_slot(now: datetime, state: dict, hours=REEL_HOURS) -> str | None:
    """The Reel hour that has started in the last SLOT_GRACE and has no Reel yet, as "YYYY-MM-DDTHH" (UTC)."""
    for hour in sorted(hours, reverse=True):
        start = now.astimezone(timezone.utc).replace(hour=hour, minute=0, second=0, microsecond=0)
        if start <= now < start + SLOT_GRACE:
            key = start.strftime("%Y-%m-%dT%H")
            return None if key in state.get("slots", {}) else key
    return None


def reel_hours(env=os.environ) -> tuple[int, ...]:
    hours = tuple(int(h) for h in re.findall(r"\d+", env.get("ZK_IG_SAATLER") or "") if 0 <= int(h) < 24)
    return hours or REEL_HOURS


def reel_slot(data_dir: Path, state: dict, report: dict, now: datetime, out_dir: Path, env=os.environ,
              order=best_first, post=publish) -> str | None:
    """Posts the Reel of the current Reel hour once -> what happened, or None when no hour is due."""
    key = due_slot(now, state, reel_hours(env))
    if key is None or env.get("ZK_IG_KAPALI"):
        return None
    slots = state.setdefault("slots", {})
    result = "atlandı: Instagram kopyası olan, paylaşılmamış Short yok"
    for upload in order(data_dir, state, now.timestamp(), env)[:3]:
        try:
            record = post(data_dir, state, upload, out_dir, env)
            result = f"{upload['video_id']} {record.get('url') or ''}".strip()
            break
        except Exception as e:  # noqa: BLE001  (the hour closes either way: no retry every minute)
            result = f"hata: {upload['video_id']}: {type(e).__name__}: {e}"[:300]
            report.setdefault("reel_hatalari", []).append(result)
            if not isinstance(e, ShortError):  # Instagram, the token or GitHub: the next hour tries again
                break
            state.setdefault("basarisiz", {})[upload["video_id"]] = [round(now.timestamp()), str(e)[:160]]
    slots[key] = result
    state["slots"] = dict(sorted(slots.items())[-40:])
    report["reel"] = {key: result}
    return result


# ---------------------------------------------------------------- comments and DMs

def instagram_prompt(prompt: str) -> str:
    """The YouTube responder's prompt, reworded for Instagram comments and direct messages."""
    for old, new in (
            ('You answer YouTube comments for "Zirvenin Kanunu", a Turkish YouTube Shorts channel',
             'You answer Instagram comments and direct messages for "Zirvenin Kanunu", a Turkish curiosity Reels '
             'account (the same videos run as Shorts on YouTube)'),
            ("Under every Short the channel pins a question for the viewers.",
             "Every Reel's caption ends with a question for the viewers."),
            ('- "comment": a viewer\'s own comment under a video.',
             '- "comment": a viewer\'s own comment under a video.\n- "dm": a private direct message to the account. '
             'Answer it as a private chat, with the same rules.')):
        if old not in prompt:
            raise ValueError(f"comment_replies.PROMPT değişmiş, Instagram metni uyarlanamadı: {old[:40]}")
        prompt = prompt.replace(old, new)
    return prompt


def _ts(text) -> float:
    try:
        return datetime.strptime(str(text), "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except ValueError:
        return time.time()


def _who(name) -> str:
    """channel-data is public: people are kept as a short hash, enough to count replies per person."""
    return hashlib.sha256(str(name).encode()).hexdigest()[:12]


def _seen(kind: str, key: str, mark) -> bool:
    """With the watch's CACHE: True when this media's comment count or this conversation's update time is what we
    last fully handled, less than CACHE_TTL ago (Instagram can count a comment before it hands it out)."""
    hit = CACHE.setdefault(kind, {}).get(key) if CACHE is not None else None
    return bool(hit) and hit[0] == mark and time.time() - hit[1] < CACHE_TTL


def fresh_comments(tok: str, user: dict, done: dict, pending: dict) -> tuple[list[dict], dict]:
    """-> (comments to judge, {id: why} to close without a reply); `pending` collects the cache marks to set once
    every fresh comment of that media is handled."""
    fresh, quiet = [], {}
    oldest = time.time() - MAX_AGE_DAYS * 86400
    media = call(tok, "GET", "me/media", params={"fields": "id,timestamp,comments_count", "limit": 100}).get("data", [])
    for m in media:
        if not m.get("comments_count") or _ts(m.get("timestamp")) < oldest or _seen("media", m["id"],
                                                                                    m["comments_count"]):
            continue
        comments = call(tok, "GET", f"{m['id']}/comments", params={
            "fields": "id,text,username,timestamp,replies{username}", "limit": 50}).get("data", [])
        ids = []
        for c in comments:
            cid, name = c["id"], c.get("username")
            if cid in done or name == user["username"]:
                continue
            ids.append(cid)
            text = " ".join((c.get("text") or "").split())
            if any(r.get("username") == user["username"] for r in (c.get("replies") or {}).get("data", [])):
                quiet[cid] = "hesap zaten cevaplamış"
            elif _ts(c.get("timestamp")) < oldest:
                quiet[cid] = "eski"
            elif not text:
                quiet[cid] = "boş"
            elif comment_replies.LINK.search(text):
                quiet[cid] = "link veya etiket içeriyor"
            else:
                fresh.append({"id": cid, "kind": "comment", "video": m["id"], "text": text[:500], "author": name,
                              "at": _ts(c.get("timestamp")), "context": ""})
        pending[("media", m["id"])] = (m["comments_count"], ids)
    return fresh, quiet


def fresh_dms(tok: str, user: dict, done: dict, pending: dict) -> tuple[list[dict], dict]:
    fresh, quiet = [], {}
    conversations = call(tok, "GET", "me/conversations", params={
        "platform": "instagram", "fields": "id,updated_time", "limit": 25}).get("data", [])
    mine = {str(user.get("user_id")), user["username"]}
    for cv in conversations:
        if _seen("conv", cv["id"], cv.get("updated_time")):
            continue
        msgs = call(tok, "GET", cv["id"], params={
            "fields": "messages.limit(5){id,message,from,created_time}"}).get("messages", {}).get("data", [])
        msgs = sorted(msgs, key=lambda m: _ts(m.get("created_time")))  # oldest first; the API lists newest first
        pending[("conv", cv["id"])] = (cv.get("updated_time"), [msgs[-1]["id"]] if msgs else [])
        if not msgs:
            continue
        last = msgs[-1]
        sender = last.get("from") or {}
        if last["id"] in done or {sender.get("id"), sender.get("username")} & mine:
            pending[("conv", cv["id"])] = (cv.get("updated_time"), [])
            continue  # answered already, or our own message is the latest
        tail = []  # everything the person wrote since our last message, so a split question is read whole
        for m in reversed(msgs):
            if {(m.get("from") or {}).get("id"), (m.get("from") or {}).get("username")} & mine:
                break
            tail.insert(0, " ".join((m.get("message") or "").split()))
        text = " / ".join(t for t in tail if t)
        if time.time() - _ts(last.get("created_time")) > DM_WINDOW:
            quiet[last["id"]] = "24 saat geçmiş"
        elif not text:
            quiet[last["id"]] = "metin yok (fotoğraf, ses veya paylaşım)"
        elif comment_replies.LINK.search(text):
            quiet[last["id"]] = "link veya etiket içeriyor"
        else:
            fresh.append({"id": last["id"], "kind": "dm", "video": "dm", "to": sender.get("id"), "text": text[:500],
                          "author": sender.get("username") or sender.get("id"), "at": _ts(last.get("created_time")),
                          "context": ""})
    return fresh, quiet


def subscribe(tok: str, report: dict) -> None:
    """Instagram refuses DM replies ("outside of allowed window") unless the account is subscribed to the app's
    message events, even within the 24 hours (Ya Olsaydı?, 2026-10-18). Checked once per watch."""
    if CACHE is not None and CACHE.get("abone"):
        return
    try:
        if not call(tok, "GET", "me/subscribed_apps").get("data"):
            call(tok, "POST", "me/subscribed_apps", params={"subscribed_fields": "messages,comments"})
            print("📨 Instagram: hesap mesaj bildirimlerine abone edildi")
        if CACHE is not None:
            CACHE["abone"] = True
    except IGError as e:
        report.setdefault("warnings", []).append(f"abonelik: {e}"[:300])


def media_videos(data_dir: Path, state: dict) -> dict:
    """Instagram media id -> the Short's title, pinned question and narration, for the reply prompt."""
    info = comment_replies.video_info(Path(data_dir))
    return {r["media_id"]: info[vid] for vid, r in state.get("posted", {}).items() if vid in info}


def gemini_asker(env=os.environ):
    key = env.get("GEMINI_API_KEY") or ""
    if not key:
        raise RuntimeError("GEMINI_API_KEY yok")
    models = list(dict.fromkeys(([env["ZK_YORUM_MODEL"]] if env.get("ZK_YORUM_MODEL") else [])
                                + list(comment_replies.MODELS)))
    return lambda prompt: comment_replies.gemini(instagram_prompt(prompt), key, models)


def cevap(data_dir: Path, state: dict, report: dict, dry: bool = False, env=os.environ, ask=None) -> None:
    if env.get("ZK_IG_KAPALI"):
        report["skipped"] = "ZK_IG_KAPALI açık"
        return
    tok = token(data_dir, report, env)
    if not tok:
        report["skipped"] = "ZK_IG_TOKEN yok"
        return
    user = me(tok, state)
    subscribe(tok, report)
    box = state.setdefault("cevaplar", {"done": {}, "son": []})
    done = box["done"]
    now = time.time()
    recent = [v for v in done.values() if v[1] == "r" and now - v[0] < 86400]
    per_person = {}
    for v in recent:
        if len(v) > 3:
            per_person[v[3]] = per_person.get(v[3], 0) + 1
    daily, person_cap = int(env.get("ZK_IG_GUNLUK") or DAILY), int(env.get("ZK_IG_KISI") or PER_PERSON)
    room = max(0, min(PER_RUN, daily - len(recent)))
    pending = {}
    fresh, quiet = fresh_comments(tok, user, done, pending)
    try:
        dms, dm_quiet = fresh_dms(tok, user, done, pending)
        fresh, quiet = fresh + dms, {**quiet, **dm_quiet}
    except IGError as e:  # messages may be off in the app; comments still go out
        report.setdefault("warnings", []).append(f"mesajlar okunamadı: {e}"[:500])
    if not dry:
        for cid, why in quiet.items():
            done[cid] = [now, "s", why]
    fresh.sort(key=lambda c: (c["kind"] != "dm", -c["at"]))  # DMs first: their window closes
    batch, later = [], 0
    for c in fresh:
        who = _who(c["author"])
        if per_person.get(who, 0) >= person_cap:
            if not dry:
                done[c["id"]] = [now, "s", f"kişi başı günlük {person_cap} cevap doldu"]
            continue
        if len(batch) >= room:
            later += 1
            continue
        batch.append(c)
        per_person[who] = per_person.get(who, 0) + 1
    report.update(new=len(fresh), quiet=len(quiet), room=room, sent_last_24h=len(recent), later=later)
    if batch:
        _answer(data_dir, state, report, dry, tok, box, batch, now, ask or gemini_asker(env))
    if not dry and CACHE is not None:  # remember what is fully handled, so the next minute skips it
        for (kind, key), (mark, ids) in pending.items():
            if all(i in box["done"] for i in ids):
                CACHE.setdefault(kind, {})[key] = (mark, time.time())
    cut = now - MAX_AGE_DAYS * 86400
    box["done"] = {k: v for k, v in box["done"].items() if v[0] > cut}


def _answer(data_dir, state, report, dry, tok, box, batch, now, ask) -> None:
    done = box["done"]
    model, verdicts = comment_replies.judge(batch, media_videos(data_dir, state), ask)
    report["model"] = model
    out = report["items"] = []
    for c in batch:
        verdict = verdicts.get(c["id"], {})
        private = c["kind"] == "dm"
        # a DM is private and this report lands on the public branch: only comments keep their text
        item = {"id": c["id"], "kind": c["kind"], "author": _who(c["author"]),
                "text": "(özel mesaj)" if private else c["text"][:160]}
        out.append(item)
        if verdict.get("action") != "reply":
            item["result"] = f"atlandı: {verdict.get('why') or 'karar yok'}"[:160]
            if not dry:  # no verdict counts too: asking Gemini again every minute would not change it
                done[c["id"]] = [now, "s", item["result"]]
            continue
        reply = comment_replies.clean_reply(verdict)
        if not reply:
            item["result"] = "atlandı: cevap kurala uymadı"
            if not dry:
                done[c["id"]] = [now, "s", item["result"]]
            continue
        item["reply"] = "(özel mesaj)" if private else reply  # an answer to a DM says what it asked
        idea = " ".join(str(verdict.get("idea") or "").split())[:120]
        if idea and not private:
            item["idea"] = idea
        if dry:
            item["result"] = "deneme, gönderilmedi"
            continue
        try:
            if private:
                call(tok, "POST", "me/messages", body={"recipient": {"id": c["to"]}, "message": {"text": reply}})
            else:
                call(tok, "POST", f"{c['id']}/replies", form={"message": reply})
        except IGError as e:
            item["result"] = f"gönderilemedi: {e}"[:300]
            if rate_limited(e):
                report["rate_limit"] = True
                break  # the rest waits for the pause
            done[c["id"]] = [now, "s", "gönderilemedi"]
            continue
        done[c["id"]] = [now, "r", "" if private else reply[:80], _who(c["author"])]
        item["result"] = "gönderildi"
        box["son"] = (box.get("son", []) + [{"at": round(now), "kind": c["kind"], "text": item["text"],
                                             "reply": item["reply"]}])[-40:]
        if idea:
            state["fikirler"] = (state.get("fikirler", []) + [{"at": round(now), "idea": idea}])[-100:]


# ---------------------------------------------------------------- check

def kontrol(data_dir: Path, state: dict, report: dict, env=os.environ) -> None:
    tok = token(data_dir, report, env)
    if not tok:
        report["skipped"] = "ZK_IG_TOKEN yok"
        return
    data = me(tok, state)
    report["hesap"] = {k: data.get(k) for k in ("username", "account_type", "media_count", "followers_count")}
    report["paylasim_hakki"] = call(tok, "GET", "me/content_publishing_limit", params={"fields": "quota_usage,config"})
    try:
        call(tok, "GET", "me/insights", params={"metric": "reach", "period": "day"})
        report["istatistik_izni"] = "var"
    except IGError as e:
        report["istatistik_izni"] = f"yok: {e}"[:300]
    try:
        report["abonelik"] = call(tok, "GET", "me/subscribed_apps").get("data")
    except IGError as e:
        report["abonelik"] = f"okunamadı: {e}"[:300]
    try:  # empty while "Allow access to messages" is off in the Instagram app
        conversations = call(tok, "GET", "me/conversations", params={"platform": "instagram", "fields": "id"})
        report["dm_konusmalari"] = len(conversations.get("data", []))
    except IGError as e:
        report["dm_konusmalari"] = f"okunamadı: {e}"[:300]
    media = call(tok, "GET", "me/media", params={"fields": "id,permalink,comments_count,timestamp",
                                                 "limit": 25}).get("data", [])
    report["medya"] = []
    for m in media:
        row = {"link": m.get("permalink"), "zaman": m.get("timestamp"), "yorum_sayisi": m.get("comments_count"),
               "istatistik": {}}
        for metric in ("views", "reach", "saved", "shares", "likes", "ig_reels_avg_watch_time"):
            try:  # one metric per call, so a metric Instagram retired does not hide the others
                values = call(tok, "GET", f"{m['id']}/insights", params={"metric": metric}).get("data", [])
                row["istatistik"][metric] = (values[0].get("values") or [{}])[0].get("value") if values else None
            except IGError as e:
                row["istatistik"][metric] = f"okunamadı: {e}"[:120]
        report["medya"].append(row)
    waiting = candidates(data_dir, state, time.time())
    report["bekleyen_short"] = [{"video_id": u["video_id"], "title": u.get("title")} for u in waiting]


def merged(data_dir: Path, state: dict) -> str:
    """instagram.json for a push: our state plus any Reel record another run pushed meanwhile."""
    fresh = load_state(data_dir).get("posted", {})
    return dump({**state, "posted": {**fresh, **state.get("posted", {})}})


def main(argv=None, env=None) -> int:
    global REFRESH
    env = os.environ if env is None else env
    parser = argparse.ArgumentParser(description="Zirvenin Kanunu Instagram")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--kontrol", action="store_true", help="Hesabı ve izinleri kontrol et")
    group.add_argument("--reel", nargs="?", const="", metavar="YOUTUBE_ID", help="Şimdi bir Reel paylaş")
    group.add_argument("--deneme", action="store_true", help="Cevapları yaz, gönderme")
    parser.add_argument("--data", default="channel-data", help="Kanal hafızası klasörü")
    parser.add_argument("--out", default="output/instagram", help="Yayın paketinin açılacağı klasör")
    parser.add_argument("--yaz", action="store_true", help="Sonucu channel-data dalına gönder")
    args = parser.parse_args(argv)
    REFRESH = False  # only the watch writes the key file
    data = Path(args.data)
    state = load_state(data)
    report = {"started": iso()}
    code = 0
    try:
        if args.kontrol or args.deneme:
            if args.kontrol:
                kontrol(data, state, report, env)
            else:
                cevap(data, state, report, dry=True, env=env)
            text = json.dumps({**report, "finished": iso()}, ensure_ascii=False, indent=1)
            name = CHECK_FILE if args.kontrol else PREVIEW_FILE
            (data / name).write_text(text, encoding="utf-8")
            if args.yaz and not push(data, lambda: {name: text}, f"Instagram: {name}"):
                raise IGError("sonuç channel-data dalına gönderilemedi")
        else:
            uploads = {u["video_id"]: u for u in load_uploads(data)}
            if args.reel:
                if args.reel not in uploads:
                    raise IGError(f"{args.reel} kanal hafızasında (uploads.json) yok")
                if args.reel in state.get("posted", {}):
                    raise IGError(f"{args.reel} zaten Instagram'da: {state['posted'][args.reel].get('url')}")
                order = [uploads[args.reel]]
            else:
                order = best_first(data, state, time.time(), env)[:1]
                if not order:
                    raise IGError("Instagram kopyası olan, paylaşılmamış Short yok")
            record = publish(data, state, order[0], Path(args.out), env)
            report["reel"] = {order[0]["video_id"]: record}
            vid = order[0]["video_id"]
            make = lambda: {STATE_FILE: merged(data, {**load_state(data), "posted": {vid: record}})}  # noqa: E731
            if args.yaz and not push(data, make, f"Instagram Reel: {vid}"):
                raise IGError(f"Reel paylaşıldı ({record.get('url')}), kaydı channel-data dalına gönderilemedi")
    except Exception as e:  # noqa: BLE001
        report["error"] = f"{type(e).__name__}: {e}"[:1000]
        print(f"❌ {report['error']}")
        code = 1
    print(json.dumps({k: v for k, v in report.items() if k not in ("medya",)}, ensure_ascii=False)[:2000])
    return code


if __name__ == "__main__":
    sys.exit(main())
