"""
A second opinion on every finished Short, next to a reference Short.

Ulaş judges samples by eye and kept finding them "basit". Nothing in the run
could see what he sees, so after rendering, Gemini (free tier) watches a small
preview of our video together with one of his reference Shorts and lists, by
impact, what makes ours look simpler, with fixes we can actually make (free
stock footage, AI voice, automatic edit). The score and issues are printed and
saved in the meta; the upload never waits on them.
"""

import base64
import os
from pathlib import Path
import subprocess
import urllib.error

from app.services.original_script import ScriptError, _extract_json, _post_json

REFERENCE_ID = "k951xfBlETg"  # Keşif Vakti, "Kediler Fizik Kurallarına Nasıl Karşı Geliyor?", 5.6 M views

CRITIC_PROMPT = """Sen Türkiye'de milyonlarca izlenen bilgi Shorts'ları (Keşif Vakti, Full Asfalt tarzı) kurgulayan deneyimli bir
video editörüsün. İlk video referans: çok izlenen, profesyonel bir Short. İkinci video bizim ürettiğimiz Short.

Bizim videoyu referansla karşılaştır ve izleyicinin gözünde neden daha basit veya amatör durduğunu söyle. Şu başlıklarda somut
ol: görüntü (kalite, konuyla uyum, çeşitlilik), kurgu ve geçişler, ekrandaki yazılar ve grafikler, ses (anlatıcının hızı ve
enerjisi, müzik, efektler), senaryo (kanca, akış, bilginin şaşırtıcılığı).
Sınırlarımız: sadece ücretsiz stok video (Pexels, Pixabay), yapay zekâ sesi ve otomatik kurgu kullanabiliyoruz; film/dizi
görüntüsü, kendi çekimimiz veya animasyon yok. Önerilerin bu sınırlar içinde uygulanabilir olsun.

Sadece şu JSON'u döndür:
{"score": 1-10 arası (10 = referans kadar profesyonel),
 "issues": [{"area": "görüntü|kurgu|yazı|ses|senaryo", "problem": "kısa ve somut", "fix": "yapılacak somut değişiklik"}],
 "strengths": ["kısa"]}
"issues" etkisi en büyükten küçüğe sıralı olsun, en fazla 8 madde."""


def preview(video: Path, out: Path) -> Path:
    """360x640, 24 fps copy with audio: a few MB, small enough to send inline."""
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video), "-vf", "scale=360:640", "-r", "24", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "32", "-c:a", "aac", "-b:a", "64k", "-ac", "1", str(out)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300,
    )
    return out


def _call_gemini(parts: list[dict]) -> str | None:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    model = os.environ.get("GEMINI_SCRIPT_MODEL", "gemini-2.5-flash")
    data = _post_json(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        {"contents": [{"parts": parts}],
         "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3}},
        {"x-goog-api-key": key},
        timeout=300,
    )
    out = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in out)


def critique(video: Path, scratch: Path | None = None, reference_id: str = REFERENCE_ID, call=_call_gemini,
             shrink=preview) -> dict | None:
    """{"score": int, "issues": [{"area", "problem", "fix"}], "strengths": [str]}, or None if it could not run.

    The preview goes to `scratch` (the work folder), never next to the video, where the upload globs would pick it up.
    """
    try:
        small = shrink(video, (scratch or video.parent) / f"{video.stem}_critic.mp4")
        # Each video is named right before it: unlabelled, Gemini once critiqued the reference cat video as ours.
        raw = call([
            {"text": "1. video (REFERANS):"},
            {"fileData": {"fileUri": f"https://www.youtube.com/watch?v={reference_id}"}},
            {"text": "2. video (BİZİM ürettiğimiz, eleştirilecek olan):"},
            {"inlineData": {"mimeType": "video/mp4", "data": base64.b64encode(small.read_bytes()).decode("ascii")}},
            {"text": CRITIC_PROMPT},
        ])
        answer = _extract_json(raw) if raw else None
    except (ScriptError, ValueError, OSError, subprocess.SubprocessError, urllib.error.URLError, TimeoutError) as e:
        print(f"ℹ️ Video eleştirisi alınamadı: {str(e)[:200]}")
        return None
    if not isinstance(answer, dict):
        return None
    try:
        score = max(1, min(10, int(answer.get("score"))))
    except (TypeError, ValueError):
        return None
    issues = [
        {k: " ".join(str(item.get(k) or "").split())[:240] for k in ("area", "problem", "fix")}
        for item in answer.get("issues") or [] if isinstance(item, dict) and item.get("problem")
    ][:8]
    strengths = [" ".join(str(s).split())[:160] for s in answer.get("strengths") or [] if str(s).strip()][:5]
    return {"score": score, "issues": issues, "strengths": strengths, "reference": reference_id}


def format_critique(result: dict) -> str:
    lines = [f"🎬 Eleştirmen puanı (referans {result['reference']}): {result['score']}/10"]
    lines += [f"   - [{i['area']}] {i['problem']} → {i['fix']}" for i in result["issues"]]
    if result["strengths"]:
        lines.append("   + " + " | ".join(result["strengths"]))
    return "\n".join(lines)
