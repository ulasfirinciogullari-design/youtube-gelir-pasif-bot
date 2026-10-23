import copy
from datetime import datetime, timezone
import importlib.util
import json

import pytest

import original_factory

_spec = importlib.util.spec_from_file_location("comment_replies",
                                               original_factory.BASE_DIR / "scripts" / "comment_replies.py")
comment_replies = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(comment_replies)

CH = "UCzirve"
NOW = datetime(2026, 10, 16, 21, 0, tzinfo=timezone.utc).timestamp()
HOUR = 3600


@pytest.fixture(autouse=True)
def today_s_rules(monkeypatch):
    monkeypatch.setattr(comment_replies, "RULES_AT", 0)  # NOW is before the rule change; only one test needs it


def stamp(hours_ago: float, now: float = NOW) -> str:
    return datetime.fromtimestamp(now - hours_ago * HOUR, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def snippet(author: str, text: str, hours_ago: float = 1, now: float = NOW) -> dict:
    return {"authorChannelId": {"value": author}, "authorDisplayName": f"@{author}", "textDisplay": text,
            "publishedAt": stamp(hours_ago, now)}


def thread(tid: str, author: str, text: str, hours_ago: float = 1, count: int = 0, replies=(), video: str = "v1",
           can_reply: bool = True) -> dict:
    return {"id": tid, "snippet": {"videoId": video, "canReply": can_reply, "totalReplyCount": count,
                                   "topLevelComment": {"id": tid, "snippet": snippet(author, text, hours_ago)}},
            "replies": {"comments": [{"id": f"{tid}.{i}", "snippet": s} for i, s in enumerate(replies)]}}


def reply(rid: str, author: str, text: str, hours_ago: float = 1, now: float = NOW) -> dict:
    return {"id": rid, "snippet": snippet(author, text, hours_ago, now)}


class FakeYouTube:
    def __init__(self, threads=(), replies=None, fail=None, titles=None, older=()):
        self.thread_items = list(threads)
        self.older = list(older)  # threads past the newest pages, reachable only by id
        self.by_id = []
        self.reply_items = replies or {}
        self.fail = fail
        self.titles = titles or {}
        self.posted, self.reads, self.looked_up = [], [], []

    def channel(self):
        return {"id": CH, "handle": "zirveninkanunu"}

    def threads(self, channel_id, oldest=0.0):
        assert channel_id == CH
        return list(self.thread_items)

    def threads_by_id(self, ids):
        self.by_id.append(ids)
        return [t for t in self.older if t["id"] in ids]

    def replies(self, thread_id):
        self.reads.append(thread_id)
        return self.reply_items.get(thread_id, [])

    def videos(self, ids):
        self.looked_up.append(ids)
        return {vid: {"title": self.titles[vid], "question": "", "narration": ""} for vid in ids if vid in self.titles}

    def reply(self, parent_id, text):
        if self.fail:
            raise self.fail
        self.posted.append((parent_id, text))
        return f"r{len(self.posted)}"


class FakeGemini:
    """Answers every comment it is shown with decide(comment), the way Gemini's JSON comes back. Comments carry
    short keys (c1, c2... newest first), so the default reply "cevap c1" names the first one."""

    def __init__(self, decide=None):
        self.decide = decide or (lambda c: {"action": "reply", "reply": f"cevap {c['id']}"})
        self.prompts = []

    def comments(self, n=-1):
        prompt = self.prompts[n]
        return json.loads(prompt.split("Comments:\n", 1)[1].split("\n\nAnswer with ONLY JSON", 1)[0])

    def __call__(self, prompt):
        self.prompts.append(prompt)
        items = [{"id": c["id"], **self.decide(c)} for c in self.comments()]
        return "fake-flash", "```json\n" + json.dumps({"items": [i for i in items if i.get("action")]}) + "\n```"


VIDEOS = {"v1": {"title": "Kedin seni neden izliyor? 🤯", "question": "Senin kedin de seni izliyor mu? 👇",
                 "narration": "Kediler sahiplerini taklit eder."}}


def go(yt, ask=None, state=None, now=NOW, **kwargs):
    state = {} if state is None else state
    report = {}
    comment_replies.run(yt, ask or FakeGemini(), state, VIDEOS, report, now, log=lambda line: None, **kwargs)
    return state, report


def test_answers_viewer_comments_and_answers_to_the_pinned_question():
    yt = FakeYouTube(
        threads=[thread("t1", "ayse", "Benim kedim de hep beni izliyor", hours_ago=2),
                 thread("t2", CH, "Senin kedin de seni izliyor mu? 👇", hours_ago=5, count=2)],
        replies={"t2": [reply("t2.a", "mert", "Evet, banyoya kadar geliyor", hours_ago=1),
                        reply("t2.b", CH, "Bizim kanal cevabı", hours_ago=0.5)]})
    ask = FakeGemini()
    state, report = go(yt, ask)
    # Newest first; an answer under our pinned question names the person, as YouTube does in a thread.
    assert yt.posted == [("t2", "@mert cevap c1"), ("t1", "cevap c2")]
    assert ask.comments() == [
        {"id": "c1", "video": "v1", "kind": "answer", "text": "Evet, banyoya kadar geliyor",
         "question": "Senin kedin de seni izliyor mu? 👇"},
        {"id": "c2", "video": "v1", "kind": "comment", "text": "Benim kedim de hep beni izliyor"}]
    assert "Kedin seni neden izliyor? 🤯 | pinned question: Senin kedin de seni izliyor mu?" in ask.prompts[0]
    assert state["done"]["t1"] == [round(NOW), "r", "r2", "ayse"]
    assert state["done"]["t2.a"] == [round(NOW), "r", "r1", "mert"]
    assert state["tracked"]["t1"] == {"at": round(NOW), "n": 1, "reply": "cevap c2"}
    assert state["tracked"]["t2"]["n"] == 3  # with our reply
    assert report["model"] == "fake-flash" and [r["result"] for r in report["items"]] == ["gönderildi"] * 2
    assert [r["reply"] for r in state["recent"]] == ["@mert cevap c1", "cevap c2"]


def test_older_videos_are_looked_up_for_their_title():
    yt = FakeYouTube(threads=[thread("t1", "a", "O sahne efsane", video="film1"), thread("t2", "b", "Güzel")],
                     titles={"film1": "Scarface masa sahnesi"})
    ask = FakeGemini()
    go(yt, ask)
    assert yt.looked_up == [["film1"]]  # v1 is already known from uploads.json
    assert "- film1: Scarface masa sahnesi" in ask.prompts[0]


def test_links_bare_tags_old_and_already_answered_comments_get_no_answer():
    yt = FakeYouTube(threads=[
        thread("link", "a", "Bana da bak www.kanalim.com"),
        thread("tags", "b", "@ali @veli.tr"),
        thread("to-us", "c", "@zirveninkanunu   bu bilgi doğru mu?"),
        thread("answered", "d", "Süper", count=1, replies=[snippet(CH, "Teşekkürler")]),
        thread("closed", "e", "Yorum", can_reply=False),
        thread("old", "f", "Eski yorum", hours_ago=15 * 24),
        thread("empty", "g", "   ")])
    ask = FakeGemini()
    state, _ = go(yt, ask)
    assert [c["text"] for c in ask.comments()] == ["bu bilgi doğru mu?"]
    assert yt.posted == [("to-us", "cevap c1")]
    reasons = {k: v[2] for k, v in state["done"].items() if v[1] == "s"}
    assert reasons == {"link": "link içeriyor", "tags": "sadece etiket", "closed": "cevaplanamaz", "empty": "boş"}
    assert "answered" not in state["done"] and state["tracked"]["answered"]["reply"] == "Teşekkürler"


def test_a_friend_tagged_next_to_real_words_still_gets_an_answer():
    inside = "Bunu @ayse.tr ile tartıştık, gerçekten doğru mu?"
    yt = FakeYouTube(threads=[thread("tag", "b", "@ali buna bak, sen de hep böyle yapıyorsun 😂", hours_ago=1),
                              thread("inside", "c", inside, hours_ago=2)])
    ask = FakeGemini()
    go(yt, ask)
    assert [c["text"] for c in ask.comments()] == ["buna bak, sen de hep böyle yapıyorsun 😂", inside]
    assert yt.posted == [("tag", "cevap c1"), ("inside", "cevap c2")]


def test_the_prompt_answers_thanks_gibberish_and_lone_emoji_instead_of_skipping_them():
    text = comment_replies.PROMPT
    assert "Always reply" in text and "a lone emoji" in text and "when in doubt, reply" in text
    skip = text[text.index("Skip (no reply) only:"):]
    assert "gibberish" not in skip and "medical" not in skip and "tagging other people" in skip


def test_a_viewer_writing_back_under_an_answer_given_by_hand_in_studio_gets_a_reply():
    yt = FakeYouTube(threads=[thread("t1", "ayse", "Kediler neden mırlar?", hours_ago=5, count=2,
                                     replies=[snippet(CH, "Mutlu olunca ve kendini iyileştirirken.", hours_ago=4)])],
                     replies={"t1": [reply("t1.a", CH, "Mutlu olunca ve kendini iyileştirirken.", hours_ago=4),
                                     reply("t1.b", "ayse", "@zirveninkanunu teşekkürler 🙏", hours_ago=1)]})
    ask = FakeGemini()
    state, _ = go(yt, ask)
    assert ask.comments() == [{"id": "c1", "video": "v1", "kind": "followup", "text": "teşekkürler 🙏",
                               "our_reply": "Mutlu olunca ve kendini iyileştirirken."}]
    assert yt.posted == [("t1", "@ayse cevap c1")] and state["tracked"]["t1"]["n"] == 3


def test_in_a_viewers_thread_only_what_follows_our_latest_answer_is_new():
    state = {"done": {}, "tracked": {"t1": {"at": round(NOW - 5 * HOUR), "n": 1, "reply": "İlk cevabımız"}}}
    yt = FakeYouTube(threads=[thread("t1", "ayse", "Kediler neden mırlar?", hours_ago=6, count=4)],
                     replies={"t1": [reply("t1.a", CH, "İlk cevabımız", hours_ago=5),
                                     reply("t1.b", "ayse", "@zirveninkanunu peki neden?", hours_ago=4),
                                     reply("t1.c", CH, "Elle yazılan cevap", hours_ago=3),
                                     reply("t1.d", "ayse", "@zirveninkanunu teşekkürler", hours_ago=1)]})
    ask = FakeGemini()
    go(yt, ask, state)
    assert ask.comments() == [{"id": "c1", "video": "v1", "kind": "followup", "text": "teşekkürler",
                               "our_reply": "Elle yazılan cevap"}]


def test_the_same_comment_pasted_twice_gets_one_answer():
    yt = FakeYouTube(threads=[thread("new", "a", "Harika kanal!", hours_ago=1, video="v2"),
                              thread("old", "a", "harika kanal", hours_ago=3)])
    state, _ = go(yt)
    assert yt.posted == [("new", "cevap c1")]
    assert state["done"]["old"][2] == "aynı kişinin aynı yorumu, bir kez cevaplandı"


def test_later_runs_read_only_threads_that_grew_and_answer_followups():
    first = FakeYouTube(threads=[thread("t1", "ayse", "Kediler neden kutuyu sever?", hours_ago=2),
                                 thread("t2", CH, "Senin kedin de seni izliyor mu? 👇", hours_ago=5, count=1)],
                        replies={"t2": [reply("t2.a", "mert", "Evet", hours_ago=1)]})
    state, _ = go(first)
    later = NOW + 2 * HOUR
    second = FakeYouTube(
        threads=[thread("t1", "ayse", "Kediler neden kutuyu sever?", hours_ago=4, count=3),
                 thread("t2", CH, "Senin kedin de seni izliyor mu? 👇", hours_ago=7, count=2)],
        replies={"t1": [reply("t1.x", "zeynep", "Bence de", hours_ago=3, now=later),  # before our reply
                        reply("t1.y", CH, "cevap t1", hours_ago=1.9, now=later),
                        reply("t1.z", "ayse", "@zirveninkanunu peki köpekler?", hours_ago=1, now=later)]})
    ask = FakeGemini()
    go(second, ask, state, now=later)
    assert second.reads == ["t1"]  # t2 grew only by our own reply
    assert ask.comments() == [{"id": "c1", "video": "v1", "kind": "followup", "text": "peki köpekler?",
                               "our_reply": "cevap t1"}]
    assert second.posted == [("t1", "@ayse cevap c1")]
    assert state["tracked"]["t1"]["reply"] == "@ayse cevap c1"
    assert state["tracked"]["t1"]["n"] == 4


def test_a_reply_to_our_reply_glued_to_our_handle_is_answered_without_the_handle():
    # YouTube's own form: a zero-width space, our handle, the text glued to it (2026-10-23 replies in channel-data).
    state = {"done": {}, "tracked": {"t1": {"at": round(NOW - 5 * HOUR), "n": 1, "reply": "İlk cevabımız"}}}
    yt = FakeYouTube(threads=[thread("t1", "ayse", "Kediler neden mırlar?", hours_ago=6, count=4)],
                     replies={"t1": [reply("t1.a", CH, "İlk cevabımız", hours_ago=5),
                                     reply("t1.b", "ayse", "\u200b@zirveninkanunusağolun", hours_ago=3),
                                     reply("t1.c", "mert", "\u200b@ZirveninKanunu kralsın 👑", hours_ago=2),
                                     reply("t1.d", "can", "\u200b@ayse bence de", hours_ago=1)]})
    ask = FakeGemini()
    go(yt, ask, state)
    # Newest first. Our handle is gone, even glued to the text; a reply to another viewer keeps its name as before.
    assert [c["text"] for c in ask.comments()] == ["\u200b@ayse bence de", "kralsın 👑", "sağolun"]


def test_a_comment_the_model_left_out_is_asked_once_more():
    calls = []

    def ask(prompt):
        rows = json.loads(prompt.split("Comments:\n", 1)[1].split("\n\nAnswer with ONLY JSON", 1)[0])
        calls.append([r["id"] for r in rows])
        if len(calls) == 1:  # a garbled key for the second comment, as long YouTube ids came back garbled
            return "m", json.dumps({"items": [{"id": "c1", "action": "reply", "reply": "Bir"},
                                              {"id": "c 2", "action": "reply", "reply": "İki"}]})
        return "m", json.dumps({"items": [{"id": "c1", "action": "reply", "reply": "İki"}]})

    yt = FakeYouTube(threads=[thread("UgwYiII9NU9V4sQqpzR4AaABAg", "a", "İlk", hours_ago=1),
                              thread("UgyU9EpFAp-8SNjndQ14AaABAg", "b", "Harika", hours_ago=2)])
    state, _ = go(yt, ask)
    assert calls == [["c1", "c2"], ["c1"]]
    assert yt.posted == [("UgwYiII9NU9V4sQqpzR4AaABAg", "Bir"), ("UgyU9EpFAp-8SNjndQ14AaABAg", "İki")]


def test_comments_closed_by_the_old_id_mixup_are_looked_at_again():
    state = {"done": {"t1": [round(NOW) - HOUR, "s", "karar yok"], "t2": [round(NOW) - HOUR, "s", "reklam"],
                      "t3.b": [round(NOW) - HOUR, "s", "karar yok"]},
             "tracked": {"t3": {"at": round(NOW - 5 * HOUR), "n": 2, "reply": "Cevabımız"}}}
    # t3's two replies were counted as seen when its reply was closed, so the unchanged thread is read again
    yt = FakeYouTube(threads=[thread("t1", "a", "İlk", hours_ago=1), thread("t2", "b", "Satın al", hours_ago=2),
                              thread("t3", "c", "Soru", hours_ago=6, count=2)],
                     replies={"t3": [reply("t3.a", CH, "Cevabımız", hours_ago=5),
                                     reply("t3.b", "c", "\u200b@zirveninkanunu teşekkürler", hours_ago=4)]})
    state, _ = go(yt, state=state)
    assert yt.posted == [("t1", "cevap c1"), ("t3", "@c cevap c2")] and state["done"]["t2"][2] == "reklam"
    assert state["tracked"]["t3"]["n"] == 3


def test_what_the_old_rules_closed_is_looked_at_again_once(monkeypatch):
    monkeypatch.setattr(comment_replies, "RULES_AT", NOW - 2 * HOUR)
    old = round(NOW) - 3 * HOUR
    state = {"done": {"t1.b": [old, "s", "link veya etiket içeriyor"], "t2": [old, "s", "Gibberish/Ascii art"],
                      "t3": [old, "s", "kanal zaten cevaplamış"], "t4": [round(NOW) - HOUR, "s", "reklam"]},
             "tracked": {"t1": {"at": round(NOW - 5 * HOUR), "n": 2, "reply": "Cevabımız"}}}
    yt = FakeYouTube(threads=[thread("t1", "ayse", "Soru", hours_ago=6, count=2), thread("t2", "b", "asdfgh"),
                              thread("t3", "c", "Elle cevaplandı"), thread("t4", "d", "Satın al")],
                     replies={"t1": [reply("t1.a", CH, "Cevabımız", hours_ago=5),
                                     reply("t1.b", "ayse", "\u200b@zirveninkanunu teşekkürler", hours_ago=4)]})
    state, _ = go(yt, state=state)
    assert sorted(yt.posted) == [("t1", "@ayse cevap c2"), ("t2", "cevap c1")]
    assert state["done"]["t3"][2] == "kanal zaten cevaplamış" and state["done"]["t4"][2] == "reklam"
    yt.posted.clear()
    go(yt, state=state)
    assert yt.posted == []


def test_a_followed_thread_past_the_newest_pages_is_read_by_id():
    state = {"done": {}, "tracked": {"old": {"at": round(NOW - 9 * 24 * HOUR), "n": 1, "reply": "Eski cevabımız"}}}
    yt = FakeYouTube(threads=[thread("t1", "a", "Yeni yorum")],
                     older=[thread("old", "ayse", "Eski soru", hours_ago=9 * 24 + 1, count=2)],
                     replies={"old": [reply("old.a", CH, "Eski cevabımız", hours_ago=9 * 24),
                                      reply("old.b", "ayse", "\u200b@zirveninkanunu peki ya köpekler?", hours_ago=2)]})
    ask = FakeGemini()
    state, _ = go(yt, ask, state)
    assert yt.by_id == [["old"]] and yt.posted == [("t1", "cevap c1"), ("old", "@ayse cevap c2")]
    assert ask.comments()[1] == {"id": "c2", "video": "v1", "kind": "followup", "text": "peki ya köpekler?",
                                 "our_reply": "Eski cevabımız"}


def test_daily_cap_leaves_the_rest_for_later_and_the_thread_is_read_again():
    sent = {f"old{i}": [round(NOW) - HOUR, "r", f"x{i}", f"p{i}"] for i in range(comment_replies.DAILY - 1)}
    yt = FakeYouTube(threads=[thread("t2", CH, "Soru? 👇", hours_ago=5, count=2)],
                     replies={"t2": [reply("t2.a", "mert", "Bir", hours_ago=1),
                                     reply("t2.b", "ece", "İki", hours_ago=2)]})
    state, report = go(yt, state={"done": dict(sent)})
    assert report["room"] == 1 and yt.posted == [("t2", "@mert cevap c1")]
    assert "t2.b" not in state["done"] and "t2" not in state["tracked"]
    tomorrow = NOW + 25 * HOUR
    state["done"] = {k: v for k, v in state["done"].items() if not k.startswith("old")}
    again = FakeYouTube(threads=yt.thread_items, replies=yt.reply_items)
    go(again, state=state, now=tomorrow)
    assert again.reads == ["t2"] and again.posted == [("t2", "@ece cevap c1")]
    assert state["tracked"]["t2"]["n"] == 3


def test_one_person_gets_at_most_ten_replies_a_day():
    assert comment_replies.PER_PERSON == 10
    state = {"done": {f"x{i}": [round(NOW) - HOUR, "r", f"r{i}", "talkative"] for i in range(10)}}
    yt = FakeYouTube(threads=[thread("t9", "talkative", "Bir şey daha soracağım")])
    state, _ = go(yt, state=state)
    assert yt.posted == []
    assert state["done"]["t9"][1:] == ["s", "aynı kişiye 24 saatte 10 cevap verildi"]


def test_skips_and_drafts_that_break_the_rules_are_closed_without_posting():
    decisions = {"spam": {"action": "skip", "why": "reklam"},
                 "link": {"action": "reply", "reply": "Detaylar www.site.com adresinde"},
                 "long": {"action": "reply", "reply": "a" * 300},
                 "memory": {"action": "reply", "reply": "O sahneyi hatırladım, haklısın!"},
                 "silent": {}}
    yt = FakeYouTube(threads=[thread(tid, tid, f"yorum {tid}", hours_ago=i + 1) for i, tid in enumerate(decisions)])
    ask = FakeGemini(lambda c: decisions[c["text"].split()[1]])
    state, report = go(yt, ask)
    assert yt.posted == []
    assert {k: v[2] for k, v in state["done"].items()} == {"spam": "reklam", "link": "kural", "long": "kural",
                                                          "memory": "kural", "silent": "iki kez karar yok"}
    assert len(ask.prompts) == 2 and [c["text"] for c in ask.comments()] == ["yorum silent"]  # asked once more
    assert {r["id"]: r["result"] for r in report["items"]}["link"] == "cevap kurala uymadı, atlandı"


def test_stock_praise_at_the_start_is_cut_and_praise_alone_is_not_posted():
    drafts = {"t1": "Bu çok keskin bir gözlem! 🧐 O sahnedeki detayı fark etmen harika. Tyler saatine bakar.",
              "t2": "Harika soru! 😊",
              "t3": "Güzel video yorumun için sağ ol ama ahtapotun üç kalbi var.",
              "t4": "Great question! Octopuses have three hearts.",
              "t5": "Great question — cats do dream.",
              "t6": "Point mutations greatly increase diversity."}
    yt = FakeYouTube(threads=[thread(tid, tid, f"yorum {tid}", hours_ago=i + 1) for i, tid in enumerate(drafts)])
    state, _ = go(yt, FakeGemini(lambda c: {"action": "reply", "reply": drafts[c["text"].split()[1]]}))
    assert yt.posted == [("t1", "Tyler saatine bakar."),
                         ("t3", "Güzel video yorumun için sağ ol ama ahtapotun üç kalbi var."),
                         ("t4", "Octopuses have three hearts."),
                         ("t5", "cats do dream."),
                         ("t6", "Point mutations greatly increase diversity.")]
    assert state["done"]["t2"][2] == "kural"


def test_a_dry_run_posts_nothing_and_changes_nothing():
    yt = FakeYouTube(threads=[thread("t1", "ayse", "Güzel video"), thread("link", "b", "http://x.co")])
    before = {"done": {"x": [round(NOW) - HOUR, "s", "eski"]}, "tracked": {}}
    state, report = go(yt, state=copy.deepcopy(before), dry=True)
    assert yt.posted == []
    assert state["done"] == before["done"] and state["tracked"] == {}
    assert report["items"] == [{"id": "t1", "kind": "comment", "video": "v1", "comment": "Güzel video",
                                "reply": "cevap c1", "result": "deneme, gönderilmedi"}]


def test_quota_or_a_passing_error_stops_the_run_and_a_gone_comment_is_not_retried():
    threads = [thread("t1", "a", "Bir", hours_ago=1), thread("t2", "b", "İki", hours_ago=2)]
    full = FakeYouTube(threads=threads, fail=comment_replies.ApiError(403, '{"reason": "quotaExceeded"}'))
    state, report = go(full)
    assert report["stopped"] == "YouTube kotası doldu" and state["done"] == {}
    for status, text in ((503, "backendError"), (429, "Too Many Requests"), (403, "rateLimitExceeded")):
        state, report = go(FakeYouTube(threads=threads, fail=comment_replies.ApiError(status, text)))
        assert report["stopped"] == f"YouTube geçici hata verdi ({status})" and state["done"] == {}
    gone = FakeYouTube(threads=threads[:1], fail=comment_replies.ApiError(404, "commentNotFound"))
    state, _ = go(gone)
    assert state["done"]["t1"][1:] == ["s", "gönderilemedi"]


def test_video_ideas_are_kept():
    yt = FakeYouTube(threads=[thread("t1", "a", "Ahtapotlar hakkında video yapın")])
    state, _ = go(yt, FakeGemini(lambda c: {"action": "reply", "reply": "Üç kalpleri var!",
                                            "idea": "Ahtapotun neden üç kalbi var?"}))
    assert state["ideas"] == [{"at": round(NOW), "video": "v1", "idea": "Ahtapotun neden üç kalbi var?"}]


def test_old_ids_are_forgotten():
    state = {"done": {"ancient": [round(NOW) - 61 * 24 * HOUR, "s", "eski"], "recent": [round(NOW), "s", "boş"]},
             "tracked": {"t0": {"at": round(NOW) - 15 * 24 * HOUR, "n": 1}}}
    state, _ = go(FakeYouTube(), state=state)
    assert list(state["done"]) == ["recent"] and state["tracked"] == {}


def test_video_info_uses_the_published_runs_meta(tmp_path):
    (tmp_path / "history").mkdir()
    (tmp_path / "uploads.json").write_text(json.dumps([
        {"video_id": "v1", "topic_id": "mk-001", "title": "Kedin seni neden izliyor? 🤯"}]), encoding="utf-8")
    published = {"topic_id": "mk-001", "title": "Kedin seni neden izliyor? 🤯", "pinned_comment": "Soru? 👇",
                 "scenes": [{"text": "Kediler   izler."}, {"text": "Çünkü merak ederler."}]}
    sample = {"topic_id": "mk-001", "title": "Başka başlık", "pinned_comment": "Deneme", "scenes": []}
    (tmp_path / "history" / "ORIGINAL_mk-001_1792000000_meta.json").write_text(json.dumps(published), "utf-8")
    (tmp_path / "history" / "ORIGINAL_mk-001_1792100000_meta.json").write_text(json.dumps(sample), "utf-8")
    assert comment_replies.video_info(tmp_path) == {"v1": {"title": "Kedin seni neden izliyor? 🤯",
                                                           "question": "Soru? 👇",
                                                           "narration": "Kediler izler. Çünkü merak ederler."}}
    assert comment_replies.video_info(tmp_path / "missing") == {}


def test_api_calls():
    calls = []

    def request(url, body=None, form=None, headers=None, timeout=60.0):
        calls.append({"url": url, "body": body, "form": form, "headers": headers})
        if "oauth2" in url:
            return {"access_token": "tok"}
        if "parentId=long" in url:
            return {"items": [{"id": "b"}]} if "pageToken=p2" in url else {"items": [{"id": "a"}], "nextPageToken": "p2"}
        if "/channels?" in url:
            return {"items": [{"id": CH, "snippet": {"customUrl": "@ZirveninKanunu"}}]}
        return {"id": "new-reply", "items": []}

    env = {"YOUTUBE_CLIENT_ID": "id", "YOUTUBE_CLIENT_SECRET": "secret", "YOUTUBE_REFRESH_TOKEN": "refresh"}
    yt = comment_replies.YouTube(comment_replies.access_token(env, request), request)
    assert calls[0]["form"]["grant_type"] == "refresh_token"
    assert yt.channel() == {"id": CH, "handle": "zirveninkanunu"}
    yt.threads(CH)
    assert "allThreadsRelatedToChannelId=UCzirve" in calls[-1]["url"] and "maxResults=100" in calls[-1]["url"]
    yt.threads_by_id([f"t{i}" for i in range(60)])
    assert "commentThreads?part=snippet%2Creplies&id=t50%2Ct51" in calls[-1]["url"]
    assert "id=t0%2Ct1%2C" in calls[-2]["url"] and "t49" in calls[-2]["url"] and "t50" not in calls[-2]["url"]
    yt.replies("t1")
    assert "comments?part=snippet&parentId=t1" in calls[-1]["url"]
    assert [r["id"] for r in yt.replies("long")] == ["a", "b"]
    yt.videos(["a", "b"])
    assert "videos?part=snippet&id=a%2Cb" in calls[-1]["url"]
    assert yt.reply("t1", "Merhaba") == "new-reply"
    assert calls[-1]["body"] == {"snippet": {"parentId": "t1", "textOriginal": "Merhaba"}}
    assert calls[-1]["headers"] == {"Authorization": "Bearer tok"}


def test_gemini_moves_to_the_next_model_and_drops_thoughts():
    seen = []

    def request(url, body=None, form=None, headers=None, timeout=60.0):
        seen.append(url.rsplit("/", 1)[1])
        if "busy" in url:
            raise comment_replies.ApiError(429, "RESOURCE_EXHAUSTED")
        assert body["tools"] == [{"google_search": {}}]
        return {"candidates": [{"content": {"parts": [{"text": "düşünce", "thought": True}, {"text": '{"items": []}'}]}}]}

    assert comment_replies.gemini("p", "key", ["busy", "ok"], request) == ("ok", '{"items": []}')
    assert seen == ["busy:generateContent", "ok:generateContent"]


def test_main_never_fails_and_reports_why(tmp_path, capsys):
    assert comment_replies.main(["--data", str(tmp_path)], env={}) == 0
    report = json.loads((tmp_path / comment_replies.REPORT_FILE).read_text(encoding="utf-8"))
    assert report["error"] == "RuntimeError: GEMINI_API_KEY yok"
    assert json.loads((tmp_path / comment_replies.STATE_FILE).read_text(encoding="utf-8")) == {}
    preview = tmp_path / "preview"
    assert comment_replies.main(["--data", str(preview), "--deneme"], env={}) == 0
    assert sorted(p.name for p in preview.iterdir()) == [comment_replies.PREVIEW_FILE]  # no state, no report
    off = tmp_path / "off"
    assert comment_replies.main(["--data", str(off)], env={"ZK_YORUM_KAPALI": "1"}) == 0
    assert "ZK_YORUM_KAPALI" in capsys.readouterr().out
    assert not (off / comment_replies.REPORT_FILE).exists()


def test_workflows_run_it_hourly_and_offer_a_preview():
    workflows = original_factory.BASE_DIR / ".github" / "workflows"
    own = (workflows / "comment_replies.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in own and "cron: '50 * * * *'" in own
    assert "contents: write" in own and "group: comment-replies" in own
    assert "python3 scripts/comment_replies.py --data channel-data" in own
    for secret in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN", "GEMINI_API_KEY"):
        assert f"secrets.{secret}" in own
    shorts = (workflows / "viral_shorts_autopilot.yml").read_text(encoding="utf-8")
    assert "- 'yorum'" in shorts
    assert "python scripts/comment_replies.py --data channel-data --deneme" in shorts
    assert "git rebase --abort" in own  # a failed rebase must not wedge the retries
