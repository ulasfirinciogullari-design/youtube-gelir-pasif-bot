"""Episodes written ahead by Claude (a scheduled session every morning), waiting for the factory. Since
2026-10-19 (Ulaş chose "Claude yazsın") the scripts come from here and Gemini only writes when the queue is empty.

  python fabrika/kuyruk.py durum             queue length, the next topics to write, recent titles to avoid
  python fabrika/kuyruk.py kontrol FILE...   check and tidy queue files the way the factory will; exit 1 on problems

A queue file fabrika/kuyruk/<YYYYMMDD>-<n>-<topic id>.json is a finished episode, the same JSON that
yazar.write + check + translate + sources give the factory: title, bar, summary, lines (each with "v"), facts,
tags, hashtags, cover, sources, i18n (all of yazar.LANGS), plus the topic's id, series and q. uret.py takes the
oldest valid file before asking Gemini and deletes it after the upload; a file it cannot use moves to
fabrika/kuyruk/bozuk/ with the reason in rapor.json warnings.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import yazar  # noqa: E402

DIR = HERE / "kuyruk"
BAD = DIR / "bozuk"
TARGET = int(os.environ.get("YO_KUYRUK") or 4)  # 3 episodes a day plus one spare for a morning that fails


def files():
    return sorted(DIR.glob("*.json"))


def problems(ep, used=()):
    """-> what keeps this episode from being voiced and uploaded; tidies it in place like the Gemini path."""
    probs = [f"{k} yok" for k in ("id", "series", "q", "summary") if not str(ep.get(k) or "").strip()]
    if ep.get("series") not in yazar.SERIES:
        probs.append(f"seri bilinmiyor: {ep.get('series')}")
    if ep.get("id") in used:
        probs.append(f"{ep.get('id')} konusu zaten kullanılmış")
    probs += yazar.validate(ep)
    n = len(ep.get("facts") or [])
    for lang in yazar.LANGS:
        t = (ep.get("i18n") or {}).get(lang) or {}
        if not t.get("title") or not t.get("summary") or len(t.get("facts") or []) != n:
            probs.append(f"çeviri eksik: {lang}")
    if not [u for u in ep.get("sources") or [] if isinstance(u, str) and u.startswith("http")]:
        probs.append("kaynak linki yok")
    return probs


def next_topics(n):
    """The topics the next n queue files should cover, picked like uret.pick_topic: series in konular.json
    order, viewers' ideas first, no subject shared with the last 3 titles."""
    import uret
    state = uret.load()
    conf = json.loads(uret.TOPICS.read_text(encoding="utf-8"))
    queued = [json.loads(p.read_text(encoding="utf-8")) for p in files()]
    taken = set(state.get("used", [])) | set(state.get("skipped", [])) | {q.get("id") for q in queued}
    titles = [e.get("title", "") for e in state.get("episodes", [])] + [q.get("title", "") for q in queued]
    out = []
    for k in range(n):
        series = conf["order"][(len(state.get("episodes", [])) + len(queued) + k) % len(conf["order"])]
        made = {uret.same_q(x) for x in titles}
        pool = [t for t in conf["topics"] + state.get("extra", [])
                if t["series"] == series and t["id"] not in taken and uret.same_q(t["q"]) not in made]
        pool.sort(key=lambda t: not t.get("oneri"))
        if pool:
            t = uret.varied(pool, {"episodes": [{"title": x} for x in titles]})
            out.append({"id": t["id"], "series": series, "q": t["q"], "izleyici_fikri": bool(t.get("oneri"))})
        else:  # the list ran out for this series: the writer makes up a new question
            day = datetime.now(timezone.utc).strftime("%m%d")
            out.append({"id": f"k{day}{k + 1}", "series": series, "q": None, "not": "konu kalmadı, yeni bir soru uydur"})
        taken.add(out[-1]["id"])
        titles.append(out[-1]["q"] or "")
    return out, titles


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "durum"
    if cmd == "durum":
        need = max(0, TARGET - len(files()))
        topics, titles = next_topics(need)
        print(json.dumps({"kuyrukta": [p.name for p in files()], "bozuk": [p.name for p in sorted(BAD.glob("*.json"))],
                          "yazilacak": need, "konular": topics, "kacinilacak_basliklar": titles[-30:]},
                         ensure_ascii=False, indent=1))
        return 0
    if cmd == "kontrol":
        import uret
        state = uret.load()
        used = set(state.get("used", [])) | set(state.get("skipped", []))
        bad = 0
        for f in sys.argv[2:]:
            p = Path(f)
            ep = json.loads(p.read_text(encoding="utf-8"))
            probs = problems(ep, used)
            if probs:
                bad += 1
                print(f"{p.name}: SORUN: " + "; ".join(probs))
            else:
                p.write_text(json.dumps(ep, ensure_ascii=False, indent=1), encoding="utf-8")
                chars = sum(len(line["text"]) for line in ep["lines"])
                print(f"{p.name}: tamam ({len(ep['lines'])} replik, {chars} karakter, {ep['title']})")
        return 1 if bad else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
