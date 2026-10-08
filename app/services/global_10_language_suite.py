"""
Global 10-Language Dubbing, Multi-Audio & Closed Caption Suite.
Expands Zirvenin Kanunu into a true worldwide media empire.

10 Supported Global Languages:
1. 🇹🇷 tr (Turkish) - tr-TR-AhmetNeural
2. 🇺🇸 en (English) - en-US-ChristopherNeural
3. 🇪🇸 es (Spanish) - es-ES-AlvaroNeural
4. 🇩🇪 de (German) - de-DE-ConradNeural
5. 🇫🇷 fr (French) - fr-FR-HenriNeural
6. 🇧🇷 pt (Portuguese) - pt-BR-AntonioNeural
7. 🇮🇹 it (Italian) - it-IT-DiegoNeural
8. 🇸🇦 ar (Arabic) - ar-SA-HamedNeural
9. 🇮🇳 hi (Hindi) - hi-IN-MadhurNeural
10. 🇯🇵 ja (Japanese) - ja-JP-KeitaNeural
"""

import asyncio
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Dict, List, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

import edge_tts
from googleapiclient.http import MediaFileUpload
from publish_to_youtube import get_authenticated_service

GLOBAL_10_LANGUAGES = {
    "tr": {"name": "Turkish (Türkçe)", "voice": "tr-TR-AhmetNeural", "hl": "tr"},
    "en": {"name": "English (US)", "voice": "en-US-ChristopherNeural", "hl": "en"},
    "es": {"name": "Spanish (Español)", "voice": "es-ES-AlvaroNeural", "hl": "es"},
    "de": {"name": "German (Deutsch)", "voice": "de-DE-ConradNeural", "hl": "de"},
    "fr": {"name": "French (Français)", "voice": "fr-FR-HenriNeural", "hl": "fr"},
    "pt": {"name": "Portuguese (Português)", "voice": "pt-BR-AntonioNeural", "hl": "pt"},
    "it": {"name": "Italian (Italiano)", "voice": "it-IT-DiegoNeural", "hl": "it"},
    "ar": {"name": "Arabic (العربية)", "voice": "ar-SA-HamedNeural", "hl": "ar"},
    "hi": {"name": "Hindi (हिन्दी)", "voice": "hi-IN-MadhurNeural", "hl": "hi"},
    "ja": {"name": "Japanese (日本語)", "voice": "ja-JP-KeitaNeural", "hl": "ja"},
}

# Core philosophical dialogue translations for flagship videos
TRANSLATION_BANK_SHELBY = {
    "tr": [
        "Bu dünyada bana huzur yok. Belki diğerinde.",
        "Herkes bir şeylerini satar, Grace. Sadece farklı parçalarımızı satıyoruz.",
        "Ne yaptığını değiştirebilirsin, ama ne istediğini asla değiştiremezsin.",
        "Çünkü sonunda, korku seni tahmin edilebilir yapar. Kanun budur."
    ],
    "en": [
        "There is no rest for me in this world. Perhaps in the next.",
        "Everyone is a whore, Grace. We just sell different parts of ourselves.",
        "You can change what you do, but you can't change what you want.",
        "Because in the end, fear makes you predictable. That is the law."
    ],
    "es": [
        "No hay descanso para mí en este mundo. Quizás en el próximo.",
        "Todo el mundo se vende, Grace. Solo vendemos partes diferentes de nosotros.",
        "Puedes cambiar lo que haces, pero nunca podrás cambiar lo que deseas.",
        "Porque al final, el miedo te hace predecible. Esa es la ley del poder."
    ],
    "de": [
        "Für mich gibt es keine Ruhe in dieser Welt. Vielleicht in der nächsten.",
        "Jeder verkauft sich, Grace. Wir verkaufen nur unterschiedliche Teile von uns.",
        "Du kannst ändern, was du tust, aber du kannst niemals ändern, was du willst.",
        "Denn am Ende macht dich Angst vorhersehbar. Das ist das Gesetz."
    ],
    "fr": [
        "Il n'y a pas de repos pour moi dans ce monde. Peut-être dans le prochain.",
        "Tout le monde se vend, Grace. Nous vendons simplement différentes parties de nous-mêmes.",
        "Tu peux changer ce que tu fais, mais tu ne pourras jamais changer ce que tu désires.",
        "Parce qu'à la fin, la peur te rend prévisible. C'est la loi suprême."
    ],
    "pt": [
        "Não há descanso para mim neste mundo. Talvez no próximo.",
        "Todo mundo se vende, Grace. Apenas vendemos partes diferentes de nós mesmos.",
        "Você pode mudar o que faz, mas nunca poderá mudar o que deseja.",
        "Porque no final, o medo torna você previsível. Essa é a lei do poder."
    ],
    "it": [
        "Non c'è riposo per me in questo mondo. Forse nel prossimo.",
        "Tutti si vendono, Grace. Vendiamo solo parti diverse di noi stessi.",
        "Puoi cambiare quello che fai, ma non potrai mai cambiare quello che desideri.",
        "Perché alla fine, la paura ti rende prevedibile. Questa è la legge."
    ],
    "ar": [
        "لا راحة لي في هذا العالم. ربما في العالم القادم.",
        "الجميع يبيع شيئاً يا غريس. نحن فقط نبيع أجزاء مختلفة من أنفسنا.",
        "يمكنك تغيير ما تفعله، لكنك لن تغير أبداً ما تريده في أعماقك.",
        "لأنه في النهاية، الخوف يجعلك قابلاً للتنبؤ. هذا هو قانون القوة."
    ],
    "hi": [
        "इस दुनिया में मेरे लिए कोई आराम नहीं है। शायद अगली दुनिया में।",
        "हर कोई किसी न किसी तरह बिकता है। हम बस अपने अलग-अलग हिस्से बेचते हैं।",
        "आप जो करते हैं उसे बदल सकते हैं, लेकिन जो आप चाहते हैं उसे कभी नहीं बदल सकते।",
        "क्योंकि अंत में, डर आपको पूरी तरह से कमज़ोर बना देता है। यही शक्ति का नियम है।"
    ],
    "ja": [
        "この世界で私が安らぐことはない。おそらく次の世界でだろう。",
        "誰もが何かを売っている。私たちは自分たちの違う部分を売っているに過ぎない。",
        "行動を変えることはできても、真に欲するものを変えることは決してできない。",
        "なぜなら最後には、恐れがお前を予測可能な存在にするからだ。それが力の法則だ。"
    ]
}


async def synthesize_language_track(lang_code: str, lines: List[str], output_mp3: Path):
    """Synthesizes high-fidelity voice track using localized neural voice."""
    if lang_code not in GLOBAL_10_LANGUAGES:
        lang_code = "en"
    
    voice = GLOBAL_10_LANGUAGES[lang_code]["voice"]
    full_text = " ... ".join(lines)
    communicate = edge_tts.Communicate(full_text, voice, rate="-4%", pitch="-2Hz")
    await communicate.save(str(output_mp3))


def generate_srt_track(lines: List[str], duration: float, output_srt: Path):
    """Generates standard .srt Closed Caption track."""
    chunk_dur = duration / max(1, len(lines))
    srt_lines = []
    
    def to_time(s):
        hrs = int(s // 3600)
        mins = int((s % 3600) // 60)
        secs = int(s % 60)
        millis = int(round((s - int(s)) * 1000))
        return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"

    for i, line in enumerate(lines, 1):
        st = (i - 1) * chunk_dur
        en = min(duration, i * chunk_dur)
        srt_lines.append(str(i))
        srt_lines.append(f"{to_time(st)} --> {to_time(en)}")
        srt_lines.append(line.strip())
        srt_lines.append("")

    with open(output_srt, "w", encoding="utf-8") as f:
        f.write("\n".join(srt_lines))


def upload_closed_caption(youtube, video_id: str, srt_file: Path, lang_code: str) -> bool:
    """Uploads Closed Caption track directly to YouTube video via API."""
    if not srt_file.exists():
        return False
    lang_name = GLOBAL_10_LANGUAGES.get(lang_code, {}).get("name", lang_code)
    body = {
        "snippet": {
            "videoId": video_id,
            "language": lang_code,
            "name": lang_name,
            "isDraft": False
        }
    }
    media = MediaFileUpload(str(srt_file), mimetype="application/x-subrip", resumable=True)
    try:
        youtube.captions().insert(part="snippet", body=body, media_body=media).execute()
        print(f"🌍 [CC Eklendi] {lang_name} ({lang_code}) -> Video {video_id}")
        return True
    except Exception as e:
        print(f"ℹ️ CC notu ({lang_code}): {e}")
        return False


def build_and_upload_all_10_captions(video_id: str, campaign_type: str = "shelby", duration: float = 25.5):
    """
    Generates and uploads Closed Captions in all 10 major global languages
    to the target YouTube video.
    """
    youtube = get_authenticated_service()
    bank = TRANSLATION_BANK_SHELBY
    out_dir = BASE_DIR / "output" / f"captions_10lang_{video_id}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 70)
    print(f"🌐 10 DİLDE KÜRESEL ALTYAZI (CLOSED CAPTIONS) DAĞITIMI: Video {video_id}")
    print("=" * 70)

    success_count = 0
    for lang_code, lines in bank.items():
        srt_file = out_dir / f"caption_{lang_code}.srt"
        generate_srt_track(lines, duration, srt_file)
        if upload_closed_caption(youtube, video_id, srt_file, lang_code):
            success_count += 1

    print("\n" + "=" * 70)
    print(f"👑 KÜRESEL ALTYAZI TAMAMLANDI: {success_count}/10 Dilde Altyazı YouTube'a İşlendi!")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    vid = sys.argv[1] if len(sys.argv) > 1 else "L8Eze1G3OFw"
    build_and_upload_all_10_captions(vid, duration=25.5)
