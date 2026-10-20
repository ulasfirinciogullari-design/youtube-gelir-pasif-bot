"""Curiosity scripts Claude writes ahead of time (Ulaş, 2026-10-19, card "İki kanalın video metinlerini kim yazsın?":
"Claude yazsın"), so the Shorts stop paying Gemini for topics, scripts, fact checks and the SEO pack.

A daily Claude Code routine pushes one JSON file per Short to kuyruk/ on the zk-metin branch, and the Shorts
workflow checks that branch out next to channel-data (ZK_KUYRUK points at its kuyruk/ folder). The factory takes
the oldest file whose id has not been uploaded yet. Gemini still picks the stock clips from their thumbnails
(original_visuals.collect_matched_stock). With no usable file the Gemini flow runs exactly as before.

A file: {"id": "mk-c0001", "topic": "...", "kategori": "hayvan", "subject": ["owl"], "stock_hint": "...",
"script": {title, hook_text, scenes: [{text, visual, stock_query, source, callout}], pinned_comment, mood},
"seo": {description, hashtags, tags, localizations: {lang: {title, description}}}}.
"""
import json
import os
from pathlib import Path

from app.services.original_script import (MERAK_MIN_SCENES, MERAK_MIN_WORDS, ScriptError, merak_overrun,
                                          validate_script)

QUEUE_ENV = "ZK_KUYRUK"
PROVIDER = "claude (kuyruk)"


def queue_dir() -> Path | None:
    path = os.environ.get(QUEUE_ENV, "").strip()
    return Path(path) if path and Path(path).is_dir() else None


def take(published: set[str], qdir: Path | None = None) -> dict | None:
    """-> {"topic", "script", "file"} for the oldest unused, valid file, or None. Broken files are reported and
    passed over, never fatal: a bad file must not cost a slot."""
    qdir = qdir or queue_dir()
    if qdir is None:
        return None
    for path in sorted(qdir.glob("*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            tid = str(item.get("id") or "").strip()
            if not tid or tid in published:
                continue
            script = validate_script(item["script"], None, min_words=MERAK_MIN_WORDS, min_scenes=MERAK_MIN_SCENES)
            overrun = merak_overrun(script)
            if overrun:
                raise ScriptError(overrun)
        except (OSError, ValueError, KeyError, TypeError, ScriptError) as e:
            print(f"ℹ️ Kuyruktaki {path.name} kullanılamadı: {str(e)[:200]}")
            continue
        if isinstance(item.get("seo"), dict):
            script["seo"] = item["seo"]
        topic = {"id": tid, "topic": str(item.get("topic") or script["title"]), "source": "claude",
                 "kategori": str(item.get("kategori") or ""), "stock_hint": str(item.get("stock_hint") or ""),
                 "subject": [str(s) for s in item.get("subject") or [] if str(s).strip()]}
        return {"topic": topic, "script": script, "file": path.name}
    return None
