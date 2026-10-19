"""Checks every queued script the way the factory will read it, plus the SEO rules in YAZIM.md.
python3 kontrol.py <path to a checkout of main>"""
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else "..")
from app.services import script_queue  # noqa: E402
from app.services.seo_pack import LANGUAGES, clean_localizations  # noqa: E402

QDIR = Path(__file__).parent / "kuyruk"
files = sorted(QDIR.glob("*.json"))
ids = {json.loads(f.read_text(encoding="utf-8")).get("id", f.stem) for f in files}
bad = 0
for f in files:
    d = json.loads(f.read_text(encoding="utf-8"))
    problems = []
    if d.get("id") != f.stem:
        problems.append("dosya adı id ile aynı değil")
    taken = script_queue.take(ids - {d.get("id")}, QDIR)
    if not taken or taken["file"] != f.name:
        problems.append("fabrika bu dosyayı atlar (yukarıdaki nota bak)")
    sc = d.get("script", {})
    words = sum(len(s.get("text", "").split()) for s in sc.get("scenes", []))
    if not 40 <= words <= 55:
        problems.append(f"{words} kelime")
    if len(sc.get("title", "")) > 60:
        problems.append("başlık 60 karakterden uzun")
    if not 3 <= len(sc.get("hook_text", "").split()) <= 7:
        problems.append("hook_text 3-7 kelime değil")
    if not (sc.get("scenes") or [{}])[0].get("callout"):
        problems.append("ilk sahnede callout yok")
    if any(len(s.get("callout") or "") > 24 for s in sc.get("scenes", [])):
        problems.append("callout 24 karakterden uzun")
    if sc.get("scenes") and sc["scenes"][0]["text"].strip().endswith("?"):
        problems.append("ilk cümle soru")
    seo = d.get("seo", {})
    if len(seo.get("description", "")) < 120:
        problems.append("SEO açıklaması 120 karakterden kısa")
    langs = clean_localizations(seo.get("localizations"))
    if set(langs) != set(LANGUAGES):
        problems.append(f"eksik dil: {sorted(set(LANGUAGES) - set(langs))}")
    tags = seo.get("tags", [])
    if not 20 <= len(tags) <= 25 or any(len(t) > 30 for t in tags):
        problems.append(f"{len(tags)} etiket ya da 30 karakteri geçen etiket")
    if not 3 <= len(seo.get("hashtags", [])) <= 5:
        problems.append("hashtag sayısı 3-5 değil")
    bad += bool(problems)
    print(f"{f.name}: {'OK' if not problems else '; '.join(problems)} ({words} kelime)")
print(f"{len(files) - bad}/{len(files)} dosya hazır")
sys.exit(1 if bad else 0)
