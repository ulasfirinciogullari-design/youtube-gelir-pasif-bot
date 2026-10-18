"""Ya Olsaydı? on Instagram: every new episode as a Reel, and replies to comments and DMs in character.

  python fabrika/instagram.py --kontrol    check the key: account name and type, token age (fabrika/ig_rapor.json)
  python fabrika/instagram.py --cevap      answer new comments and direct messages once (the reply watcher,
                                           fabrika/nobet.py, calls cevap() every minute)
  python fabrika/instagram.py --deneme     write the replies into fabrika/ig_rapor.json, post nothing
  fabrika/uret.py calls paylas() right after the YouTube upload.

Needs the YA_IG_TOKEN secret: a long-lived user token of the "Instagram API with Instagram login" (60 days), made
in the Meta app dashboard. Actions cannot write secrets and the branch is public, so the token is refreshed once a
week and the fresh one is kept in fabrika/ig_anahtar.enc, encrypted with a key derived from the secret; a new
secret simply replaces it. Instagram fetches a Reel's video from a public URL, so the video and cover sit as a
GitHub release asset for the few minutes Instagram needs, then the release is deleted.
Comments and DMs are judged by the same Gemini prompt as YouTube comments (fabrika/yorum.py): the text is only
data, links and touchy subjects are skipped, Pi or Bit signs every reply. A DM can only be answered within 24 hours.
"""
import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cevapdurum  # noqa: E402
import yazar  # noqa: E402
import yorum  # noqa: E402

API = "https://graph.instagram.com/v25.0"
KEYFILE = HERE / "ig_anahtar.enc"
REPORT = HERE / "ig_rapor.json"
DAILY = int(os.environ.get("YO_IG_GUNLUK") or 200)
PER_RUN = int(os.environ.get("YO_IG_TUR") or 20)
PER_PERSON = int(os.environ.get("YO_IG_KISI") or 10)  # replies per person per 24 h, so one chatter cannot use up the day
MAX_AGE_DAYS = 14
DM_WINDOW = 23 * 3600  # Instagram allows an answer within 24 hours of the person's message
REFRESH_AFTER = 7 * 86400
TAGS = ["#yaolsaydı", "#bilim", "#bilimsel", "#merak", "#öğren", "#reels", "#keşfet"]
PROMPT = yorum.PROMPT.replace(
    'You answer YouTube comments for "Ya Olsaydı?", a Turkish science Shorts channel',
    'You answer Instagram comments and direct messages for "Ya Olsaydı?", a Turkish science Reels account').replace(
    "Comments:", "Comments (\"video\": \"dm\" means a direct message, answer it as a private chat):")


# The reply watcher runs cevap() every minute for hours: it sets REFRESH = False (the factory's runs refresh the
# key, so only one writer touches ig_anahtar.enc) and CACHE = {} so unchanged media and conversations cost no calls.
REFRESH = True
CACHE = None


class IGError(RuntimeError):
    pass


def log(msg):
    yazar.log(msg)


# ---------------------------------------------------------------- token

def _cipher(secret):
    from cryptography.fernet import Fernet
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"ya-olsaydi-ig:" + secret.encode()).digest()))


def token(report):
    """The current access token, refreshed once a week; None without the YA_IG_TOKEN secret."""
    secret = os.environ.get("YA_IG_TOKEN", "").strip()
    if not secret:
        return None
    tok = {"t": secret, "at": 0}
    if KEYFILE.exists():
        from cryptography.fernet import InvalidToken
        try:
            tok = json.loads(_cipher(secret).decrypt(KEYFILE.read_bytes()))
        except (InvalidToken, ValueError):
            pass  # made from an older secret: the new secret wins
    age = time.time() - tok.get("at", 0)
    if age > REFRESH_AFTER and REFRESH:
        r = requests.get("https://graph.instagram.com/refresh_access_token", timeout=30,
                         params={"grant_type": "ig_refresh_token", "access_token": tok["t"]})
        if r.ok:
            d = r.json()
            tok = {"t": d["access_token"], "at": time.time(), "expires": time.time() + int(d.get("expires_in", 0))}
            KEYFILE.write_bytes(_cipher(secret).encrypt(json.dumps(tok).encode()))
            log("Instagram anahtarı yenilendi")
        else:  # a token under 24 hours old cannot be refreshed yet; it still works
            report.setdefault("warnings", []).append(f"anahtar yenilenemedi ({r.status_code}): {r.text[:200]}")
    if tok.get("expires"):
        report["anahtar_bitis"] = datetime.fromtimestamp(tok["expires"], timezone.utc).isoformat(timespec="minutes")
    return tok["t"]


def call(tok, method, path, **kw):
    r = requests.request(method, f"{API}/{path.lstrip('/')}", headers={"Authorization": f"Bearer {tok}"},
                         timeout=kw.pop("timeout", 60), **kw)
    if not r.ok:
        raise IGError(f"{method} {path.split('?')[0]} {r.status_code}: {r.text[:400]}")
    return r.json() if r.content else {}


def me(tok, state):
    if CACHE is not None and CACHE.get("me"):
        d = CACHE["me"]
    else:
        d = call(tok, "GET", "me", params={"fields": "user_id,username,account_type,media_count,followers_count"})
        if CACHE is not None:
            CACHE["me"] = d
    state.setdefault("instagram", {})["user"] = {k: d.get(k) for k in ("user_id", "username", "account_type")}
    return d


# ---------------------------------------------------------------- Reels

def caption(ep):
    lines = [ep["title"], ""]
    if ep.get("summary"):
        lines += [ep["summary"], ""]
    facts = [f.get("text", "") for f in ep.get("facts", []) if f.get("text")][:3]
    if facts:
        lines += ["Videodaki bilgiler:"] + [f"• {t}" for t in facts] + [""]
    # no @handle: on Instagram it would link to someone else's account
    lines += ["Sen ne düşünüyorsun? Yorumlara yaz! 👇", "", "Tüm bölümler YouTube'da: Ya Olsaydı? (link profilde)", ""]
    tags, seen = [], set()
    for t in list(ep.get("hashtags") or []) + TAGS:
        t = "#" + re.sub(r"[^\w]", "", str(t).lstrip("#"))
        if len(t) > 2 and t.lower() not in seen:
            seen.add(t.lower())
            tags.append(t)
    text = "\n".join(lines + [" ".join(tags[:5])])  # Instagram is testing a 5-hashtag cap; more reads as spam
    return text.replace("<", "‹").replace(">", "›")[:2200]


def _gh(method, url, **kw):
    r = requests.request(method, url, timeout=kw.pop("timeout", 120), headers={
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json",
        **kw.pop("headers", {})}, **kw)
    if not r.ok and not (method == "DELETE" and r.status_code == 404):
        raise IGError(f"GitHub {method} {r.status_code}: {r.text[:300]}")
    return r.json() if r.content else {}


def host(files, tag):
    """Put local files on a temporary GitHub release -> ({name: public url}, release id)."""
    repo = os.environ["GITHUB_REPOSITORY"]
    rel = _gh("POST", f"https://api.github.com/repos/{repo}/releases", json={
        "tag_name": tag, "name": tag, "prerelease": True,
        "body": "Instagram'ın videoyu alması için geçici; birkaç dakika içinde silinir."})
    urls = {}
    try:
        for name, (path, ctype) in files.items():
            a = _gh("POST", f"https://uploads.github.com/repos/{repo}/releases/{rel['id']}/assets",
                    params={"name": name}, data=Path(path).read_bytes(), headers={"Content-Type": ctype}, timeout=300)
            urls[name] = a["browser_download_url"]
    except Exception:
        unhost(rel["id"], tag)
        raise
    return urls, rel["id"]


def unhost(rel_id, tag):
    repo = os.environ["GITHUB_REPOSITORY"]
    for url in (f"https://api.github.com/repos/{repo}/releases/{rel_id}",
                f"https://api.github.com/repos/{repo}/git/refs/tags/{tag}"):
        try:
            _gh("DELETE", url)
        except IGError as e:
            log(f"geçici sürüm silinemedi: {e}")


def _direct(url):
    """GitHub answers a release download with a redirect to a short-lived signed link; that link itself."""
    r = requests.head(url, allow_redirects=False, timeout=30)
    return r.headers.get("Location") or url


def reel(tok, urls, text):
    """Create a Reels container and wait until Instagram has fetched and processed the video -> container id.
    If Instagram cannot fetch through GitHub's redirect, try once more with the signed link it points to."""
    for attempt in (1, 2):
        u = urls if attempt == 1 else {k: _direct(v) for k, v in urls.items()}
        cid = call(tok, "POST", "me/media", data={
            "media_type": "REELS", "video_url": u["video.mp4"], "cover_url": u["kapak.jpg"],
            "caption": text, "share_to_feed": "true"})["id"]
        for _ in range(40):  # Instagram fetches and processes the video, usually within a minute or two
            time.sleep(15)
            st = call(tok, "GET", cid, params={"fields": "status_code,status"})
            if st.get("status_code") == "FINISHED":
                return cid
            if st.get("status_code") in ("ERROR", "EXPIRED"):
                break
        else:
            raise IGError("Instagram videoyu 10 dakikada işlemedi")
        log(f"Instagram videoyu alamadı ({attempt}. deneme): {st.get('status')}")
    raise IGError(f"Instagram videoyu işleyemedi: {st.get('status')}")


def paylas(ep, video, cover, state):
    """Publish one episode as a Reel -> record for durum.json. Raises IGError when Instagram refuses."""
    report = {}
    tok = token(report)
    if not tok:
        return {"skipped": "YA_IG_TOKEN yok"}
    ig = state.setdefault("instagram", {})
    if ep["id"] in ig.get("posted", {}):
        return {"skipped": "zaten paylaşıldı", **ig["posted"][ep["id"]]}
    me(tok, state)
    tag = f"ig-{ep['id']}-{int(time.time())}"
    urls, rel = host({"video.mp4": (video, "video/mp4"), "kapak.jpg": (cover, "image/jpeg")}, tag)
    try:
        cid = reel(tok, urls, caption(ep))
        mid = call(tok, "POST", "me/media_publish", data={"creation_id": cid})["id"]
    finally:
        unhost(rel, tag)
    link = call(tok, "GET", mid, params={"fields": "permalink"}).get("permalink")
    rec = {"media_id": mid, "url": link, "at": time.time()}
    ig.setdefault("posted", {})[ep["id"]] = rec
    log(f"Instagram: {link}")
    return {**report, **rec}


# ---------------------------------------------------------------- comments and DMs

def videos(state):
    """Instagram media id -> the episode's title and facts, for the reply prompt."""
    by_ep = yorum.videos(state)
    yt = {"ay": next(iter(yorum.PILOT)), **{e["id"]: e["video_id"] for e in state.get("episodes", [])}}
    return {r["media_id"]: by_ep.get(yt.get(eid), {"title": "?", "facts": []})
            for eid, r in state.get("instagram", {}).get("posted", {}).items()}


def _ts(s):
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except (TypeError, ValueError):
        return time.time()


CACHE_TTL = 600


def _who(name):
    """The branch is public: people are kept as a short hash, enough to count replies per person."""
    return hashlib.sha256(str(name).encode()).hexdigest()[:12]


def _seen(kind, key, mark):
    """With the watcher's CACHE: True when this media's comment count or this conversation's update time is
    what we last fully handled, less than CACHE_TTL ago (Instagram can count a comment before it hands it out)."""
    hit = CACHE.setdefault(kind, {}).get(key) if CACHE is not None else None
    return bool(hit) and hit[0] == mark and time.time() - hit[1] < CACHE_TTL


def fresh_comments(tok, user, done, pending):
    """-> (comments to judge, ids to mark done without a reply); `pending` collects the cache marks to set once
    every fresh comment of that media is handled."""
    fresh, quiet = [], {}
    limit = time.time() - MAX_AGE_DAYS * 86400
    media = call(tok, "GET", "me/media", params={"fields": "id,timestamp,comments_count", "limit": 25}).get("data", [])
    for m in media:
        if not m.get("comments_count") or _ts(m.get("timestamp")) < limit:
            continue
        if _seen("media", m["id"], m["comments_count"]):
            continue
        cs = call(tok, "GET", f"{m['id']}/comments", params={
            "fields": "id,text,username,timestamp,replies{username}", "limit": 50}).get("data", [])
        ids = []
        for c in cs:
            cid, name = c["id"], c.get("username")
            if cid in done or name == user["username"]:
                continue
            ids.append(cid)
            text = " ".join((c.get("text") or "").split())
            if any(r.get("username") == user["username"] for r in (c.get("replies") or {}).get("data", [])):
                quiet[cid] = "hesap zaten cevaplamış"
            elif _ts(c.get("timestamp")) < limit:
                quiet[cid] = "eski"
            elif not text:
                quiet[cid] = "boş"
            elif yorum.LINK.search(text):
                quiet[cid] = "link içeriyor"
            else:
                fresh.append({"id": cid, "kind": "yorum", "video": m["id"], "text": text[:500], "author": name,
                              "at": _ts(c.get("timestamp"))})
        pending[("media", m["id"])] = (m["comments_count"], ids)
    return fresh, quiet


def fresh_dms(tok, user, done, pending):
    fresh, quiet = [], {}
    convs = call(tok, "GET", "me/conversations", params={"platform": "instagram", "fields": "id,updated_time",
                                                         "limit": 25}).get("data", [])
    mine = {str(user.get("user_id")), user["username"]}
    for cv in convs:
        if _seen("conv", cv["id"], cv.get("updated_time")):
            continue
        msgs = call(tok, "GET", cv["id"], params={
            "fields": "messages.limit(5){id,message,from,created_time}"}).get("messages", {}).get("data", [])
        msgs = sorted(reversed(msgs), key=lambda m: _ts(m.get("created_time")))  # oldest first; the API lists newest first
        pending[("conv", cv["id"])] = (cv.get("updated_time"), [msgs[-1]["id"]] if msgs else [])
        if not msgs:
            continue
        last = msgs[-1]
        frm = last.get("from") or {}
        mid = last["id"]
        if mid in done or {frm.get("id"), frm.get("username")} & mine:
            pending[("conv", cv["id"])] = (cv.get("updated_time"), [])
            continue  # answered already, or our own message is the latest
        tail = []  # everything the person wrote since our last message, so a split question is read whole
        for m in reversed(msgs):
            if {(m.get("from") or {}).get("id"), (m.get("from") or {}).get("username")} & mine:
                break
            tail.insert(0, " ".join((m.get("message") or "").split()))
        text = " / ".join(t for t in tail if t)
        if time.time() - _ts(last.get("created_time")) > DM_WINDOW:
            quiet[mid] = "24 saat geçmiş"
        elif not text:
            quiet[mid] = "metin yok (fotoğraf, ses veya paylaşım)"
        elif yorum.LINK.search(text):
            quiet[mid] = "link içeriyor"
        else:
            fresh.append({"id": mid, "kind": "dm", "video": "dm", "to": frm.get("id"), "text": text[:500],
                          "author": frm.get("username") or frm.get("id"), "at": _ts(last.get("created_time"))})
    return fresh, quiet


def judge(batch, vids, planned):
    vlines = [f"- {vid}: {v['title']}" + (" | facts: " + " / ".join(v["facts"]) if v["facts"] else "")
              for vid, v in vids.items() if vid in {c["video"] for c in batch}]
    comments = json.dumps([{"id": c["id"], "video": c["video"], "text": c["text"]} for c in batch], ensure_ascii=False)
    prompt = PROMPT.format(videos="\n".join(vlines) or "-", planned="; ".join(planned[:150]) or "-", comments=comments)
    text, _ = yazar.call(prompt, search=True, temperature=0.6)
    items = yazar.parse_json(text).get("items", [])
    return {str(i.get("id")): i for i in items if isinstance(i, dict)}


def cevap(state, report, dry):
    if os.environ.get("YO_IG_KAPALI"):
        report["skipped"] = "YO_IG_KAPALI açık"
        return
    tok = token(report)
    if not tok:
        report["skipped"] = "YA_IG_TOKEN yok"
        return
    user = me(tok, state)
    box = state.setdefault("instagram", {}).setdefault("cevaplar", {"done": {}, "son": []})
    done = box["done"]
    now = time.time()
    recent = [v for v in done.values() if v[1] == "r" and now - v[0] < 86400]
    sent = len(recent)
    per_person = {}
    for v in recent:
        if len(v) > 3:
            per_person[v[3]] = per_person.get(v[3], 0) + 1
    room = max(0, min(PER_RUN, DAILY - sent))
    pending = {}
    fresh, quiet = fresh_comments(tok, user, done, pending)
    try:
        f2, q2 = fresh_dms(tok, user, done, pending)
        fresh, quiet = fresh + f2, {**quiet, **q2}
    except IGError as e:  # messaging may need its own permission; comments still go out
        report.setdefault("warnings", []).append(f"mesajlar okunamadı: {e}"[:500])
    if not dry:
        for cid, why in quiet.items():
            done[cid] = [now, "s", why]
    fresh.sort(key=lambda c: (c["kind"] != "dm", -c["at"]))  # DMs first: their window closes
    batch, later = [], 0
    for c in fresh:
        who = _who(c["author"])
        if per_person.get(who, 0) >= PER_PERSON:
            if not dry:
                done[c["id"]] = [now, "s", f"kişi başı günlük {PER_PERSON} cevap doldu"]
            continue
        if len(batch) >= room:
            later += 1
            continue
        batch.append(c)
        per_person[who] = per_person.get(who, 0) + 1
    report.update({"new": len(fresh), "quiet": len(quiet), "room": room, "sent_last_24h": sent, "later": later})
    if batch:
        _answer(state, report, dry, tok, box, batch, now)
    if not dry and CACHE is not None:  # remember what is fully handled, so the next minute skips it
        for (kind, key), (mark, ids) in pending.items():
            if all(i in box["done"] for i in ids):
                CACHE.setdefault(kind, {})[key] = (mark, time.time())
    cut = now - 60 * 86400
    box["done"] = {k: v for k, v in box["done"].items() if v[0] > cut}


def _answer(state, report, dry, tok, box, batch, now):
    done = box["done"]
    planned = yorum.planned_questions(state)
    verdicts = judge(batch, videos(state), planned)
    out = report["items"] = []
    for c in batch:
        v = verdicts.get(c["id"], {})
        # a DM is private and this report lands on the public branch: only comments keep their text
        item = {"id": c["id"], "kind": c["kind"], "author": _who(c["author"]),
                "text": c["text"] if c["kind"] != "dm" else "(özel mesaj)"}
        out.append(item)
        if v.get("action") != "reply":
            item["result"] = f"atlandı: {v.get('why') or 'karar yok'}"
            if not dry:  # no verdict counts too: asking Gemini again every minute would not change it
                done[c["id"]] = [now, "s", item["result"]]
            continue
        reply = yorum.clean_reply(v)
        if not reply:
            item["result"] = "atlandı: cevap kurala uymadı"
            if not dry:
                done[c["id"]] = [now, "s", item["result"]]
            continue
        item["reply"] = reply if c["kind"] != "dm" else "(özel mesaj)"  # an answer to a DM says what it asked
        topic = None if dry or v.get("planned") else yorum.add_topic(state, v.get("topic"), planned)
        if topic:
            item["topic"] = topic["q"]
            planned.append(topic["q"])
        if dry:
            item["result"] = "deneme, gönderilmedi"
            continue
        try:
            if c["kind"] == "dm":
                call(tok, "POST", "me/messages", json={"recipient": {"id": c["to"]}, "message": {"text": reply}})
            else:
                call(tok, "POST", f"{c['id']}/replies", data={"message": reply})
            done[c["id"]] = [now, "r", reply[:80] if c["kind"] != "dm" else "", _who(c["author"])]
            item["result"] = "gönderildi"
            box["son"] = (box["son"] + [{"at": now, "kind": c["kind"], "text": item["text"][:120],
                                         "reply": reply if c["kind"] != "dm" else "(özel mesaj)"}])[-40:]
        except IGError as e:
            item["result"] = f"gönderilemedi: {e}"[:300]
            if rate_limited(e):
                report["rate_limit"] = True
                break  # try again after the pause
            done[c["id"]] = [now, "s", "gönderilemedi"]


def rate_limited(e):
    """Instagram's 'too many calls' answers: HTTP 429 or error codes 4, 17, 32, 613."""
    t = str(e)
    return " 429:" in t or bool(re.search(r'"code":\s*(4|17|32|613)\b', t))


def kontrol(state, report):
    tok = token(report)
    if not tok:
        report["skipped"] = "YA_IG_TOKEN yok"
        return
    d = me(tok, state)
    report["hesap"] = {k: d.get(k) for k in ("username", "account_type", "media_count", "followers_count")}
    report["paylasim_hakki"] = call(tok, "GET", "me/content_publishing_limit", params={"fields": "quota_usage,config"})
    try:  # empty while "Allow access to messages" is off in the Instagram app
        convs = call(tok, "GET", "me/conversations", params={"platform": "instagram", "fields": "id,updated_time"})
        report["dm_konusmalari"] = len(convs.get("data", []))
        # which id does a reply need? Compare the sender id on the messages with the conversation's participants
        # (ids only as hashes and lengths: this report is public)
        report["dm_kimlik"] = []
        for cv in convs.get("data", [])[:3]:
            cd = call(tok, "GET", cv["id"], params={"fields": "participants,messages.limit(3){from,to,created_time}"})
            row = {"katilimci": [{"id": _who(x.get("id")), "uzunluk": len(str(x.get("id") or "")),
                                  "biz": x.get("username") == d.get("username")}
                                 for x in (cd.get("participants") or {}).get("data", [])],
                   "mesajlar": []}
            for m in (cd.get("messages") or {}).get("data", []):
                f = m.get("from") or {}
                row["mesajlar"].append({"from": _who(f.get("id")), "uzunluk": len(str(f.get("id") or "")),
                                        "to": [_who(t.get("id")) for t in (m.get("to") or {}).get("data", [])],
                                        "zaman": m.get("created_time")})
            report["dm_kimlik"].append(row)
    except IGError as e:
        report["dm_konusmalari"] = f"okunamadı: {e}"[:300]
    # what the reply watcher sees: Instagram's comment count per Reel against the comments the API hands out
    media = call(tok, "GET", "me/media", params={"fields": "id,permalink,comments_count", "limit": 10}).get("data", [])
    report["medya"] = []
    for m in media:
        row = {"link": m.get("permalink"), "yorum_sayisi": m.get("comments_count")}
        try:
            row["api_yorum"] = len(call(tok, "GET", f"{m['id']}/comments", params={"fields": "id", "limit": 50}).get("data", []))
        except IGError as e:
            row["api_yorum"] = f"okunamadı: {e}"[:200]
        report["medya"].append(row)
    # does Instagram fetch a video from where paylas() puts it? A container that is never published shows nowhere;
    # once a Reel is out, that is proof enough
    t = HERE / "deneme"
    if not state.get("instagram", {}).get("posted") and (t / "video.mp4").exists() and (t / "kapak.jpg").exists():
        tag = f"ig-test-{int(time.time())}"
        urls, rel = host({"video.mp4": (t / "video.mp4", "video/mp4"), "kapak.jpg": (t / "kapak.jpg", "image/jpeg")}, tag)
        try:
            reel(tok, urls, "deneme, paylaşılmayacak")
            report["video_testi"] = "Instagram videoyu aldı ve işledi (paylaşılmadı)"
        except IGError as e:
            report["video_testi"] = f"başarısız: {e}"[:500]
        finally:
            unhost(rel, tag)


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--kontrol", action="store_true")
    g.add_argument("--cevap", action="store_true")
    g.add_argument("--deneme", action="store_true")
    a = ap.parse_args()
    replies = not a.kontrol
    state = cevapdurum.load() if replies else yorum.load_state()
    report = {"started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "mod": "kontrol" if a.kontrol else "deneme" if a.deneme else "cevap"}
    try:
        if a.kontrol:
            kontrol(state, report)
        else:
            cevap(state, report, a.deneme)
    except (Exception, SystemExit) as e:  # never stop the episode run that follows
        report["error"] = f"{type(e).__name__}: {e}"[:1000]
        print(report["error"], file=sys.stderr)
    if replies and not a.deneme:
        cevapdurum.save(state)  # also after an error, so a sent reply is never sent twice
    elif not replies:
        yorum.save_state(state)
    if report.get("error") or report.get("items") or report.get("hesap") or a.deneme:
        report["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    sent = sum(1 for i in report.get("items", []) if i.get("result") == "gönderildi")
    why = report.get("skipped") or report.get("error", "")[:80]
    print(f"Instagram: {report.get('new', 0)} yeni, {sent} cevap" + (f" ({why})" if why else "")
          + (f", hesap @{report['hesap']['username']}" if report.get("hesap") else "")
          + (f", video testi: {report['video_testi'][:60]}" if report.get("video_testi") else ""))


if __name__ == "__main__":
    main()
