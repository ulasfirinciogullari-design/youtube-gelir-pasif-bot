"""Zirvenin Kanunu on Instagram: the Reel copy keeps its text inside Instagram's crops, the best waiting Short goes
out once per Reel hour, and replies never put a DM's text or a person's name on the public branch."""

from datetime import datetime, timezone
import importlib.util
import io
import json
import zipfile

import pytest

import original_factory
from app.services import tiktok
from app.services.subtitle_engine import generate_shorts_ass_subtitles


def _load(name):
    spec = importlib.util.spec_from_file_location(name, original_factory.BASE_DIR / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ig = _load("instagram_zk")
watch = _load("instagram_watch")

NOW = datetime(2026, 10, 18, 14, 30, tzinfo=timezone.utc)
META = {
    "topic_id": "mk-011", "title": "Soğan Doğrarken Gözler Neden Yaşarır? 🧅",
    "pinned_comment": "Sence soğan kendini bu gazla hangi canlıdan korur: böceklerden mi insanlardan mı?",
    "description": "\n\n".join([
        "Soğan doğrarken gözler neden yaşarır?",
        "Soğan kestiğinizde hücreleri parçalanır ve kükürtlü bileşikler açığa çıkar.",
        "Sence soğan kendini bu gazla hangi canlıdan korur: böceklerden mi insanlardan mı?",
        "#soğan #ilginçbilgiler #shorts #bilim #merak #keşfet", "Seri: mk-011"]),
    "scenes": [{"text": "Soğan kendini korur."}],
}


@pytest.fixture(autouse=True)
def fresh_module_state(monkeypatch):
    monkeypatch.setattr(ig, "CACHE", None)
    monkeypatch.setattr(ig, "REFRESH", False)


# ---------------------------------------------------------------- the Instagram copy

def test_the_instagram_copy_moves_the_hook_into_the_feed_crop_and_drops_the_handle(tmp_path):
    ass = tmp_path / "subtitles.ass"
    generate_shorts_ass_subtitles(sentences=[{"text": "Soğan kendini korur", "start": 0.0, "end": 2.0}],
                                  output_path=ass, font_name="Montserrat", font_size=74, margin_v=620)
    original_factory.add_hook_overlay(ass, "Soğan neden ağlatır?", 2.0)
    original_factory.add_callouts(ass, [{"callout": "Kükürt"}], [2.0])
    youtube = ass.read_text(encoding="utf-8")

    copy = original_factory.instagram_subtitles(ass)

    assert ass.read_text(encoding="utf-8") == youtube  # the YouTube render is untouched
    assert copy.name == "subtitles_ig.ass"
    text = copy.read_text(encoding="utf-8")
    styles = {line.split(",")[0]: line.split(",") for line in text.splitlines() if line.startswith("Style:")}
    assert styles["Style: HookStyle"][-2] == "330" and styles["Style: HookStyle"][-5] == "8"  # top, inside y 285
    assert styles["Style: ShortsStyle"][-4:-1] == ["120", "120", "620"]
    assert "@zirveninkanunu" in youtube and "@zirveninkanunu" not in text
    # Everything else, the words and the callouts, is the same.
    assert [l for l in youtube.splitlines() if "Dialogue" in l and "Watermark" not in l] == \
        [l for l in text.splitlines() if "Dialogue" in l]


def test_the_artifact_hands_over_the_instagram_copy_and_the_cover(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ORIGINAL_mk-011_7.mp4", b"youtube")
        z.writestr("ORIGINAL_mk-011_7_ig.mp4", b"instagram")
        z.writestr("ORIGINAL_mk-011_7_thumb.jpg", b"jpg")
        z.writestr("ORIGINAL_mk-011_7_meta.json", json.dumps(META))
    video, meta = tiktok._unpack(buf.getvalue(), tmp_path, "mk-011", ("_ig.mp4", "_thumb.jpg"))
    assert meta["title"] == META["title"]
    assert (tmp_path / "ORIGINAL_mk-011_7_ig.mp4").read_bytes() == b"instagram"
    assert (tmp_path / "ORIGINAL_mk-011_7_thumb.jpg").exists()


# ---------------------------------------------------------------- Reels

def test_the_caption_asks_the_pinned_question_with_at_most_five_hashtags_and_no_handle():
    text = ig.caption({**META, "title": "Soğan @zirveninkanunu"})
    assert text.startswith("Soğan zirveninkanunu\n\nSoğan kestiğinizde")
    assert "böceklerden mi insanlardan mı? Yorumlara yaz 👇" in text
    tags = text.splitlines()[-1].split()
    assert tags == ["#soğan", "#ilginçbilgiler", "#bilim", "#merak", "#keşfet"]
    assert "@" not in text and "#shorts" not in text and "Seri:" not in text
    # Without a description the narration explains it and the fixed hashtags fill in.
    bare = ig.caption({"title": "T", "scenes": [{"text": "Bir."}, {"text": "İki."}]})
    assert "Bir. İki." in bare and bare.splitlines()[-1] == " ".join(ig.FIXED_TAGS)


def _channel(tmp_path, uploads, metas):
    (tmp_path / "history").mkdir()
    (tmp_path / "uploads.json").write_text(json.dumps(uploads), encoding="utf-8")
    for name, meta in metas.items():
        (tmp_path / "history" / name).write_text(json.dumps(meta), encoding="utf-8")
    return tmp_path


def _stamp(iso):
    return int(datetime.fromisoformat(iso).timestamp())


def test_only_recent_public_shorts_with_an_instagram_copy_wait_for_a_reel(tmp_path):
    def up(vid, topic, at, **kw):
        return {"video_id": vid, "topic_id": topic, "title": f"T {topic}", "uploaded_at": at, "privacy": "public", **kw}

    uploads = [up("new", "mk-1", "2026-10-18T13:25:00+00:00"), up("old", "mk-2", "2026-10-01T13:25:00+00:00"),
               up("nocopy", "mk-3", "2026-10-18T11:25:00+00:00"), up("posted", "mk-4", "2026-10-18T09:25:00+00:00"),
               up("private", "mk-5", "2026-10-18T07:25:00+00:00", privacy="private"),
               up("again", "mk-1", "2026-10-17T13:25:00+00:00")]
    copy = {"instagram_video": "x_ig.mp4"}
    metas = {f"ORIGINAL_mk-1_{_stamp('2026-10-18T13:10:00+00:00')}_meta.json": {"title": "T mk-1", **copy},
             f"ORIGINAL_mk-1_{_stamp('2026-10-17T13:10:00+00:00')}_meta.json": {"title": "T mk-1"},  # before copies
             f"ORIGINAL_mk-2_{_stamp('2026-10-01T13:10:00+00:00')}_meta.json": {"title": "T mk-2", **copy},
             f"ORIGINAL_mk-3_{_stamp('2026-10-18T11:10:00+00:00')}_meta.json": {"title": "T mk-3"},
             f"ORIGINAL_mk-4_{_stamp('2026-10-18T09:10:00+00:00')}_meta.json": {"title": "T mk-4", **copy},
             f"ORIGINAL_mk-5_{_stamp('2026-10-18T07:10:00+00:00')}_meta.json": {"title": "T mk-5", **copy}}
    data = _channel(tmp_path, uploads, metas)
    waiting = ig.candidates(data, {"posted": {"posted": {}}}, NOW.timestamp())
    assert [u["video_id"] for u in waiting] == ["new"]


def test_the_most_watched_waiting_short_comes_first(tmp_path, monkeypatch):
    monkeypatch.setattr(ig, "candidates", lambda *a: [{"video_id": "a"}, {"video_id": "b"}, {"video_id": "c"}])
    order = ig.best_first(tmp_path, {}, NOW.timestamp(), views=lambda ids, env: {"a": 10, "b": 900, "c": 40})
    assert [u["video_id"] for u in order] == ["b", "c", "a"]

    def broken(ids, env):
        raise OSError("YouTube down")

    assert [u["video_id"] for u in ig.best_first(tmp_path, {}, 0, views=broken)] == ["a", "b", "c"]


def test_each_reel_hour_is_due_once_and_only_for_three_hours():
    state = {"slots": {}}
    assert ig.due_slot(NOW, state) == "2026-10-18T14"
    assert ig.due_slot(NOW.replace(hour=12, minute=59), state) == "2026-10-18T10"
    assert ig.due_slot(NOW.replace(hour=13, minute=5), state) is None  # 10:00 + 3 h has passed
    assert ig.due_slot(NOW.replace(hour=9, minute=59), state) is None
    state["slots"]["2026-10-18T14"] = "x"
    assert ig.due_slot(NOW, state) is None
    assert ig.reel_hours({"ZK_IG_SAATLER": "9, 21 ,25"}) == (9, 21)
    assert ig.reel_hours({}) == ig.REEL_HOURS


def test_a_reel_hour_posts_the_first_short_that_can_go_and_closes(tmp_path):
    state = {}
    tried = []

    def post(data, st, upload, out, env):
        tried.append(upload["video_id"])
        if upload["video_id"] == "a":
            raise ig.ShortError("yayın paketinde Instagram kopyası yok")
        st.setdefault("posted", {})[upload["video_id"]] = {"url": "https://instagram.com/reel/X"}
        return st["posted"][upload["video_id"]]

    order = lambda *a: [{"video_id": "a"}, {"video_id": "b"}, {"video_id": "c"}]  # noqa: E731
    report = {}
    result = ig.reel_slot(tmp_path, state, report, NOW, tmp_path, {}, order=order, post=post)
    assert result == "b https://instagram.com/reel/X" and tried == ["a", "b"]
    assert state["slots"] == {"2026-10-18T14": result} and "a" in state["basarisiz"]
    # The same hour never posts twice.
    assert ig.reel_slot(tmp_path, state, {}, NOW, tmp_path, {}, order=order, post=post) is None
    # An hour with nothing to post is closed too, and an unexpected error closes it without blaming the Short.
    empty = {}
    assert ig.reel_slot(tmp_path, empty, {}, NOW, tmp_path, {}, order=lambda *a: [], post=post).startswith("atlandı")

    def crash(*a):
        raise KeyError("id")

    def refused(*a):
        raise ig.IGError('POST me/media 400: {"error":{"code":190}}')

    for failure in (crash, refused):  # not the Short's fault: no other Short is tried, none is blamed
        odd = {}
        assert ig.reel_slot(tmp_path, odd, {}, NOW, tmp_path, {}, order=order, post=failure).startswith("hata: a")
        assert "basarisiz" not in odd and odd["slots"]
    assert ig.reel_slot(tmp_path, {}, {}, NOW, tmp_path, {"ZK_IG_KAPALI": "1"}, order=order, post=post) is None


def test_a_one_off_reel_record_survives_the_watch_push(tmp_path):
    (tmp_path / ig.STATE_FILE).write_text(json.dumps({"posted": {"one": {"url": "u1"}}}), encoding="utf-8")
    pushed = json.loads(ig.merged(tmp_path, {"posted": {"two": {"url": "u2"}}, "nobet": {"tur": 3}}))
    assert pushed["posted"] == {"one": {"url": "u1"}, "two": {"url": "u2"}} and pushed["nobet"] == {"tur": 3}


# ---------------------------------------------------------------- comments and DMs

def test_the_reply_prompt_is_the_youtube_one_reworded_for_instagram():
    text = ig.instagram_prompt(ig.comment_replies.PROMPT.format(videos="-", comments="[]"))
    assert "Instagram comments and direct messages" in text and '"dm": a private direct message' in text
    assert "YouTube comments for" not in text
    with pytest.raises(ValueError):
        ig.instagram_prompt("something else")


class FakeInstagram:
    """Answers the Graph API calls cevap() makes and records what it sends."""

    def __init__(self, now):
        stamp = lambda minutes: datetime.fromtimestamp(now - minutes * 60, timezone.utc).strftime(  # noqa: E731
            "%Y-%m-%dT%H:%M:%S+0000")
        self.sent = []
        self.routes = {
            "me": {"user_id": "17841", "username": "zirveninkanunu", "account_type": "MEDIA_CREATOR"},
            "me/subscribed_apps": {"data": [{"subscribed_fields": ["messages"]}]},
            "me/media": {"data": [{"id": "m1", "timestamp": stamp(60), "comments_count": 4}]},
            "m1/comments": {"data": [
                {"id": "c1", "text": "Bu gerçekten doğru mu?", "username": "ayse", "timestamp": stamp(5)},
                {"id": "c2", "text": "@ali @veli.tr", "username": "veli", "timestamp": stamp(4)},
                {"id": "c3", "text": "Peki ya kırmızı soğan?", "username": "ayse", "timestamp": stamp(3)},
                {"id": "c4", "text": "Teşekkürler", "username": "zirveninkanunu", "timestamp": stamp(2)}]},
            "me/conversations": {"data": [{"id": "cv1", "updated_time": stamp(1)}]},
            "cv1": {"messages": {"data": [
                {"id": "d2", "message": "kaç yaşındasın?", "from": {"id": "999", "username": "gizli_kisi"},
                 "created_time": stamp(1)},
                {"id": "d1", "message": "merhaba", "from": {"id": "999", "username": "gizli_kisi"},
                 "created_time": stamp(2)}]}},
        }

    def __call__(self, tok, method, path, params=None, form=None, body=None, timeout=60):
        if method == "POST":
            self.sent.append((path, form or body))
            return {"id": "r"}
        return self.routes[path]


def test_comments_and_dms_get_replies_without_their_private_parts_on_the_branch(tmp_path, monkeypatch):
    fake = FakeInstagram(datetime.now(timezone.utc).timestamp())
    monkeypatch.setattr(ig, "call", fake)
    (tmp_path / "uploads.json").write_text("[]", encoding="utf-8")
    asked = []

    def ask(prompt):
        asked.append(prompt)
        return "gemini-test", json.dumps({"items": [
            {"id": "d2", "action": "reply", "reply": "Yaşım yok, ben bir bilgi kanalıyım 🙂"},
            {"id": "c3", "action": "reply", "reply": "Harika soru! Kırmızı soğanda da aynı gaz var."}]})

    state, report = {}, {}
    ig.cevap(tmp_path, state, report, env={"ZK_IG_TOKEN": "tok", "ZK_IG_KISI": "1"}, ask=ask)

    assert fake.sent == [("me/messages", {"recipient": {"id": "999"},
                                          "message": {"text": "Yaşım yok, ben bir bilgi kanalıyım 🙂"}}),
                         ("c3/replies", {"message": "Kırmızı soğanda da aynı gaz var."})]
    done = state["cevaplar"]["done"]
    assert done["c2"][1:] == ["s", "sadece etiket"]
    assert done["c1"][2].startswith("kişi başı günlük 1")  # ayse's newer comment got this run's one reply
    assert "c4" not in done  # our own comment
    # The DM is read whole (both messages) but never stored: no text, no name, on the public branch.
    assert "merhaba / kaç yaşındasın?" in asked[0]
    public = json.dumps(state, ensure_ascii=False) + json.dumps(report, ensure_ascii=False)
    assert "kaç yaşındasın" not in public and "gizli_kisi" not in public and "ayse" not in public
    assert "Yaşım yok" not in public
    assert report["items"][0]["text"] == "(özel mesaj)" and report["items"][0]["result"] == "gönderildi"


def test_without_the_token_nothing_is_read(tmp_path, monkeypatch):
    monkeypatch.setattr(ig, "call", lambda *a, **k: pytest.fail("no call without a token"))
    report = {}
    ig.cevap(tmp_path, {}, report, env={})
    assert report["skipped"] == "ZK_IG_TOKEN yok"


def test_the_token_is_refreshed_weekly_and_kept_encrypted(tmp_path, monkeypatch):
    monkeypatch.setattr(ig, "REFRESH", True)
    calls = []

    def http(method, url, **kw):
        calls.append(kw["params"]["access_token"])
        return 200, json.dumps({"access_token": "fresh-token", "expires_in": 5184000}).encode()

    monkeypatch.setattr(ig, "http", http)
    report = {}
    assert ig.token(tmp_path, report, {"ZK_IG_TOKEN": "first-token"}) == "fresh-token"
    raw = (tmp_path / ig.KEY_FILE).read_bytes()
    assert b"fresh-token" not in raw and calls == ["first-token"]
    assert ig.token(tmp_path, {}, {"ZK_IG_TOKEN": "first-token"}) == "fresh-token" and len(calls) == 1
    # A new secret wins over a key file made from the old one.
    monkeypatch.setattr(ig, "REFRESH", False)
    assert ig.token(tmp_path, {}, {"ZK_IG_TOKEN": "second-token"}) == "second-token"


def test_rate_limit_answers_are_recognised():
    assert ig.rate_limited('POST me/messages 400: {"error":{"code": 4,"message":"too many"}}')
    assert ig.rate_limited("GET me/media 429: slow down")
    assert not ig.rate_limited('POST x 400: {"error":{"code": 100}}')


def test_the_watch_is_not_started_before_the_token_exists(monkeypatch):
    monkeypatch.delenv("ZK_IG_HAZIR", raising=False)
    monkeypatch.delenv("ZK_IG_NOBET_KAPALI", raising=False)
    monkeypatch.setattr(watch, "github", lambda *a, **k: pytest.fail("no GitHub call"))
    assert "ZK_IG_TOKEN henüz yok" in watch.ensure()
    monkeypatch.setenv("ZK_IG_HAZIR", "1")
    monkeypatch.setattr(watch, "github", lambda method, path, body=None: (200, {"workflow_runs": [
        {"status": "in_progress"}]}))
    assert "çalışıyor" in watch.ensure()


def test_what_people_write_under_our_reply_gets_an_answer_in_the_same_thread(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc).timestamp()
    stamp = lambda minutes: datetime.fromtimestamp(now - minutes * 60, timezone.utc).strftime(  # noqa: E731
        "%Y-%m-%dT%H:%M:%S+0000")
    fake = FakeInstagram(now)
    fake.routes["me/conversations"] = {"data": []}
    fake.routes["m1/comments"] = {"data": [
        {"id": "c1", "text": "Bu gerçekten doğru mu?", "username": "ayse", "timestamp": stamp(30), "replies": {
            "data": [{"id": "r4", "text": "peki ya kırmızı soğan?", "username": "ayse", "timestamp": stamp(3)},
                     {"id": "r0", "text": "bence de", "username": "mehmet", "timestamp": stamp(28)},
                     {"id": "r1", "text": "Evet, kükürt gazı yüzünden.", "username": "zirveninkanunu",
                      "timestamp": stamp(25)},
                     {"id": "r2", "text": "@zirveninkanunu teşekkürler 🙏", "username": "ayse",
                      "timestamp": stamp(10)},
                     {"id": "r3", "text": "@ayse katılıyorum", "username": "veli", "timestamp": stamp(5)}]}}]}
    monkeypatch.setattr(ig, "call", fake)
    (tmp_path / "uploads.json").write_text("[]", encoding="utf-8")
    asked = []

    def ask(prompt):
        asked.append(prompt)
        return "gemini-test", json.dumps({"items": [{"id": "r4", "action": "reply",
                                                     "reply": "Kırmızı soğanda da aynı gaz var 🧅"}]})

    state, report = {}, {}
    box = state.setdefault("cevaplar", {"done": {"c1": [now - 1500, "r", "Evet", "x"]}, "son": []})
    ig.cevap(tmp_path, state, report, env={"ZK_IG_TOKEN": "tok"}, ask=ask)

    assert fake.sent == [("c1/replies", {"message": "@ayse Kırmızı soğanda da aynı gaz var 🧅"})]
    comments = json.loads(asked[0].split("Comments:\n")[1].split("\n\nAnswer with")[0])
    assert comments == [{"id": "r4", "video": "m1", "kind": "followup", "text": "teşekkürler 🙏 / peki ya kırmızı soğan?",
                         "our_reply": "Evet, kükürt gazı yüzünden."}]
    done = box["done"]
    assert done["r4"][1] == "r" and done["r2"][1:] == ["s", "aynı sohbette birlikte cevaplandı"]
    assert done["r3"][1:] == ["s", "başka birine yazılmış"] and "r0" not in done  # before our reply
    assert "ayse" not in json.dumps(state, ensure_ascii=False) + json.dumps(report, ensure_ascii=False)


def test_every_page_of_a_busy_reels_comments_is_read(monkeypatch):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")
    pages = {None: {"data": [{"id": f"a{i}", "text": "soru?", "username": f"u{i}", "timestamp": now}
                             for i in range(50)],
                    "paging": {"cursors": {"after": "P2"}, "next": "https://graph.instagram.com/next"}},
             "P2": {"data": [{"id": "b1", "text": "son soru?", "username": "z", "timestamp": now}],
                    "paging": {"cursors": {"after": "P3"}}}}

    def call(tok, method, path, params=None, **kw):
        if path == "me/media":
            return {"data": [{"id": "m1", "timestamp": now, "comments_count": 51}]}
        return pages[params.get("after")]

    monkeypatch.setattr(ig, "call", call)
    fresh, quiet = ig.fresh_comments("tok", {"username": "zirveninkanunu"}, {}, {})
    assert len(fresh) == 51 and fresh[-1]["id"] == "b1"


def test_a_one_off_reel_waits_while_the_watch_posts_the_hours_reel(tmp_path, monkeypatch):
    monkeypatch.setattr(ig, "due_slot", lambda now, state, hours: "2026-10-18T14")
    monkeypatch.setattr(ig, "publish", lambda *a, **k: pytest.fail("no Reel while the watch posts one"))
    assert ig.main(["--reel", "--data", str(tmp_path)], env={"ZK_IG_TOKEN": "tok"}) == 1
