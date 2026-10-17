"""YA OLSAYDI? episode factory: one new episode per run, written, voiced, drawn and uploaded.

  python fabrika/uret.py --kapi            only decide whether this tick should produce (stdlib only)
  python fabrika/uret.py --mod saatlik     produce and upload public when the hourly slot is free
  python fabrika/uret.py --mod zorla       produce and upload now, whatever the slot
  python fabrika/uret.py --mod deneme      produce, keep video and cover in fabrika/deneme/, do not upload
  python fabrika/uret.py --mod igbolum --bolum y001   voice and draw an earlier episode again, post it to Instagram only

State: fabrika/durum.json (uploaded episodes, used topics). Outcome of every run: fabrika/rapor.json.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / "durum.json"
REPORT = HERE / "rapor.json"
TOPICS = HERE / "konular.json"
WORK = HERE / "_is"
DAILY = int(os.environ.get("YO_GUNLUK") or 6)
# Turkish prime time (UTC+3): 09, 12, 15, 18, 20, 22. Ulaş cut 24/day to 6/day on 2026-10-16 (views too thin).
SLOTS = [int(h) for h in (os.environ.get("YO_SAATLER") or "6,9,12,15,17,19").split(",") if h.strip()]
GAP_MIN = 35
MIN_CREDITS = 12000


def now():
    return time.time()


def load():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"episodes": [], "used": [], "extra": []}


def save(state):
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def gate(state, mod):
    """-> (go, reason). Hourly ticks come twice an hour (GitHub drops some), a slot holds one episode."""
    if mod in ("zorla", "deneme", "igbolum"):
        return True, mod
    if os.environ.get("YO_DURDUR"):
        return False, "YO_DURDUR değişkeni açık, üretim durduruldu"
    eps = state.get("episodes", [])
    t = now()
    hour = t - t % 3600
    if int(t // 3600 % 24) not in SLOTS:
        return False, f"yayın saati değil (UTC {SLOTS})"
    if any(e.get("at", 0) >= hour for e in eps):
        return False, "bu saatin bölümü yüklendi"
    if eps and t - eps[-1].get("at", 0) < GAP_MIN * 60:
        return False, f"son yüklemeden bu yana {GAP_MIN} dakika geçmedi"
    # per UTC day, not the last 24 h: the slots are fixed hours, and a rolling window blocked the first
    # morning slots of 2026-10-17 because of the previous day's hourly run
    if sum(1 for e in eps if e.get("at", 0) >= t - t % 86400) >= DAILY:
        return False, f"bugün {DAILY} bölüm yüklendi"
    if state.get("fail_streak", 0) >= 6 and t - state.get("last_fail", 0) < 3 * 3600:
        return False, "üst üste 6 deneme başarısız oldu, 3 saat bekleniyor"
    return True, "saat boş"


# Words every question shares; the rest name its subject (Güneş, kara delik, oksijen...).
COMMON = {w[:5] for w in ("olurdu olsaydı olur olsa olmasaydı kadar gerçekte büyük günde boyunca nasıl neler bütün "
                          "bir anda hiç yok kaç kez saniyeliğine dakika saniye yerinde görürdük etrafında gibi "
                          "atar olsun olmaz insan insanlar").split()}


def subjects(text):
    """Subject word stems of a title or question (first 5 letters, Turkish-aware lowercase)."""
    t = text.replace("I", "ı").replace("İ", "i").lower()
    words = [w.split("'")[0].split("’")[0] for w in t.split()]
    return {w[:5] for w in ("".join(ch for ch in w if ch.isalpha()) for w in words) if len(w) >= 4} - COMMON


def varied(pool, state, last=3):
    """First topic that does not share a subject with the last few episodes (4 Sun episodes in a row
    happened on 2026-10-16); falls back to the first one when every topic overlaps."""
    recent = set().union(*[subjects(e.get("title", "")) for e in state.get("episodes", [])[-last:]] or [set()])
    return next((t for t in pool if not subjects(t["q"]) & recent), pool[0])


def pick_topic(state):
    import yazar
    conf = json.loads(TOPICS.read_text(encoding="utf-8"))
    order = conf["order"]
    series = order[len(state.get("episodes", [])) % len(order)]
    used = set(state.get("used", [])) | set(state.get("skipped", []))
    pool = [t for t in conf["topics"] + state.get("extra", []) if t["series"] == series and t["id"] not in used]
    pool.sort(key=lambda t: not t.get("oneri"))  # viewers' ideas from the comments go first (fabrika/yorum.py)
    if not pool:
        known = [t["q"] for t in conf["topics"] + state.get("extra", [])]
        new = yazar.new_topics(series, known)
        base = len(state.get("extra", []))
        for i, q in enumerate(new):
            state.setdefault("extra", []).append({"id": f"x{base + i + 1:04d}", "series": series, "q": q})
        pool = [t for t in state["extra"] if t["series"] == series and t["id"] not in used]
        yazar.log(f"{series} için {len(new)} yeni konu üretildi")
    return varied(pool, state)


def write_episode(topic, state, report):
    import yazar
    recent = [e["title"] for e in state.get("episodes", [])]
    last_probs = []
    for attempt in range(3):
        t = dict(topic)
        if last_probs:
            t["angle"] = (t.get("angle", "") + " Fix these problems of the previous draft: " + "; ".join(last_probs)).strip()
        try:
            ep = yazar.write(t, recent)
        except (yazar.GeminiError, ValueError) as e:
            last_probs = [f"geçersiz cevap: {e}"[:200]]
            yazar.log(f"Yazım denemesi {attempt + 1}: {last_probs[0]}")
            continue
        probs = yazar.validate(ep)
        if not probs:
            report["attempts"] = attempt + 1
            report["model"] = getattr(yazar.call, "model", None)
            return ep
        last_probs = probs
        yazar.log(f"Yazım denemesi {attempt + 1} kurallara uymadı: {'; '.join(probs)}")
    raise RuntimeError("3 denemede kurallara uyan senaryo çıkmadı: " + "; ".join(last_probs))


def fact_check(ep, report):
    import yazar
    res = yazar.check(ep)
    fixes = [f for f in res.get("fixes", []) if isinstance(f, dict) and isinstance(f.get("line"), int)
             and 0 <= f["line"] < len(ep["lines"]) - 1 and str(f.get("text", "")).strip()]
    for f in fixes:
        ep["lines"][f["line"]]["text"] = " ".join(str(f["text"]).split())  # validate() strips a "pi:" prefix
    report["fact_check"] = {"ok": res.get("ok"), "fixes": [{"line": f["line"], "why": f.get("why", "")} for f in fixes]}
    if fixes:
        probs = yazar.validate(ep)
        if probs:
            raise RuntimeError("bilgi düzeltmesinden sonra senaryo bozuldu: " + "; ".join(probs))
    return res.get("_chunks", [])


def render(ep, timings_path, mix, out_video, out_cover, layout=None):
    """layout 'ig': Instagram's copy, title and subtitles kept clear of its overlays and crops (motor.html LY)."""
    motor = HERE / "motor"
    (motor / "bolum.js").write_text("window.EP = " + json.dumps(
        {"series": ep["series"], "title": ep["bar"], "lines": ep["lines"], "layout": layout},
        ensure_ascii=False) + ";\n", encoding="utf-8")
    subprocess.run(["node", str(motor / "render.js"), str(out_video), str(timings_path), str(mix)], check=True)
    subprocess.run(["node", str(motor / "render.js"), "--cover", str(ep.get("cover", 1)), str(out_cover), str(timings_path)],
                   check=True)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(out_video)], capture_output=True, text=True, check=True).stdout.strip())
    if not 14 <= dur <= 60:
        raise RuntimeError(f"video süresi beklenmedik: {dur:.1f} sn")
    return dur


def to_instagram(ep, timings_path, mix, state):
    import instagram
    video, cover = WORK / "ig_video.mp4", WORK / "ig_kapak.jpg"
    render(ep, timings_path, mix, video, cover, layout="ig")
    return instagram.paylas(ep, video, cover, state)


def ig_bolum(state, ep_id, report):
    """An episode from before Instagram: voice it again (the audio is not kept) and post the Instagram copy."""
    import ses
    p = HERE / "bolumler" / f"{ep_id}.json"
    if not p.exists():
        raise SystemExit(f"bölüm bulunamadı: {ep_id}")
    ep = json.loads(p.read_text(encoding="utf-8"))
    if ep_id in state.get("instagram", {}).get("posted", {}):
        report["skipped"] = f"{ep_id} Instagram'da zaten var"
        return None
    cr = ses.credits()
    if cr and cr[1] and cr[1] - cr[0] < MIN_CREDITS:
        report["skipped"] = f"ElevenLabs kredisi az ({cr[1] - cr[0]} kaldı)"
        return None
    WORK.mkdir(exist_ok=True)
    timings, mix, chars = ses.make(ep, WORK)
    report.update({"title": ep["title"], "chars": chars})
    report["instagram"] = to_instagram(ep, WORK / "timings.json", mix, state)
    return ep


def load_ystate():
    p = HERE.parent / "pilot-ay" / "youtube_state.json"
    return json.loads(p.read_text()) if p.exists() else {}


def produce(state, mod, report):
    import yazar
    import ses
    yt = ystate = None
    if mod != "deneme":
        # the key's channel first: on 2026-10-17 a key for Zirvenin Kanunu sat in YA_YT_REFRESH_TOKEN and the
        # check only came after the voice and render were paid for
        import yukle
        ystate = load_ystate()
        yt = yukle.connect(ystate, report)
        cr = ses.credits()
        if cr and cr[1] and cr[1] - cr[0] < MIN_CREDITS:
            report["skipped"] = f"ElevenLabs kredisi az ({cr[1] - cr[0]} kaldı), Zirvenin Kanunu'na bırakıldı"
            return None
        report["credits_before"] = cr
    topic = pick_topic(state)
    report["topic"] = topic
    yazar.log(f"Konu: {topic['id']} {topic['q']} ({topic['series']})")
    try:
        ep = write_episode(topic, state, report)
    except RuntimeError:
        state.setdefault("skipped", []).append(topic["id"])  # do not get stuck on one topic
        raise
    chunks = fact_check(ep, report)
    ep["sources"] = yazar.sources(ep, chunks)
    if not ep["sources"]:
        report.setdefault("warnings", []).append("çalışan kaynak linki bulunamadı")
    try:
        ep["i18n"] = yazar.translate(ep)
    except (yazar.GeminiError, ValueError) as e:
        ep["i18n"] = {}
        report.setdefault("warnings", []).append(f"çeviri yapılamadı: {e}"[:300])
    ep.update({"id": topic["id"], "series": topic["series"], "q": topic["q"]})
    for k in ("_chunks", "_grounded"):
        report[k.strip("_")] = len(ep[k]) if k == "_chunks" else ep[k]
        ep.pop(k, None)

    WORK.mkdir(exist_ok=True)
    timings, mix, chars = ses.make(ep, WORK)
    report["chars"] = chars
    video, cover = WORK / "video.mp4", WORK / "kapak.jpg"
    report["duration"] = round(render(ep, WORK / "timings.json", mix, video, cover), 1)
    (HERE / "bolumler").mkdir(exist_ok=True)
    (HERE / "bolumler" / f"{ep['id']}.json").write_text(
        json.dumps({**ep, "timings": timings}, ensure_ascii=False, indent=1), encoding="utf-8")
    report["title"] = ep["title"]

    if mod == "deneme":
        d = HERE / "deneme"
        d.mkdir(exist_ok=True)
        (d / "video.mp4").write_bytes(video.read_bytes())
        (d / "kapak.jpg").write_bytes(cover.read_bytes())
        (d / "bolum.json").write_text(json.dumps(ep, ensure_ascii=False, indent=1), encoding="utf-8")
        report["deneme"] = "fabrika/deneme/"
        return ep

    import yukle
    ystate["fabrika"] = state.get("episodes", [])
    rec = yukle.upload(yt, ep, video, cover, ystate, report, privacy=os.environ.get("YO_GORUNURLUK") or "public")
    rec.update({"at": now(), "chars": chars})
    state.setdefault("episodes", []).append(rec)
    state.setdefault("used", []).append(topic["id"])
    save(state)  # never lose an uploaded video's id
    report["url"] = rec["url"]
    if os.environ.get("YA_IG_TOKEN") and not os.environ.get("YO_IG_KAPALI"):
        try:  # Instagram is a bonus: a refusal there never fails the YouTube episode
            report["instagram"] = to_instagram(ep, WORK / "timings.json", mix, state)
        except Exception as e:  # noqa: BLE001
            report.setdefault("warnings", []).append(f"Instagram: {type(e).__name__}: {e}"[:500])
        save(state)
    try:
        st = yukle.status(yt, state["episodes"][-20:])
        for e in state["episodes"]:
            if e["video_id"] in st:
                e["status"] = st[e["video_id"]]
        report["recent_status"] = {e["id"]: st.get(e["video_id"]) for e in state["episodes"][-5:]}
    except RuntimeError as e:
        report.setdefault("warnings", []).append(f"durum okunamadı: {e}")
    cr = ses.credits()
    report["credits_after"] = cr
    return ep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kapi", action="store_true")
    ap.add_argument("--mod", default="saatlik", choices=["saatlik", "zorla", "deneme", "igbolum"])
    ap.add_argument("--bolum", help="igbolum: the episode id, e.g. y001")
    a = ap.parse_args()
    state = load()
    go, why = gate(state, a.mod)
    if a.kapi:
        print(f"go={'true' if go else 'false'}")
        print(f"Kapı: {why}", file=sys.stderr)
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a") as f:
                f.write(f"go={'true' if go else 'false'}\n")
        return
    report = {"started": datetime.now(timezone.utc).isoformat(timespec="seconds"), "mod": a.mod, "gate": why}
    sys.path.insert(0, str(HERE))
    import yazar
    try:
        if go:
            ep = ig_bolum(state, a.bolum, report) if a.mod == "igbolum" else produce(state, a.mod, report)
            if ep is not None:
                state["fail_streak"] = 0
    except (Exception, SystemExit) as e:  # the Actions log is hard to reach, so the reason lands in rapor.json
        report["error"] = f"{type(e).__name__}: {e}"[:1500]
        report["trace"] = traceback.format_exc()[-3000:]
        if not isinstance(e, SystemExit):  # a wrong or missing key costs nothing; once fixed, the next slot should run
            state["fail_streak"] = state.get("fail_streak", 0) + 1
            state["last_fail"] = now()
        print(traceback.format_exc(), file=sys.stderr)
    finally:
        report["log"] = yazar.LOG[-60:]
        report["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        runs = state.setdefault("runs", [])
        runs.append({k: report.get(k) for k in ("finished", "mod", "title", "url", "skipped", "error", "duration", "chars")
                     if report.get(k)})
        state["runs"] = runs[-60:]
        save(state)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    if report.get("error"):
        sys.exit(1)


if __name__ == "__main__":
    main()
