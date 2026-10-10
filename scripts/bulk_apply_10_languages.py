"""
Bulk 10-Language Global Localizer for Zirvenin Kanunu.
Updates the latest 25 published videos on YouTube with localized titles & descriptions
in 10 major global languages (English, Spanish, German, French, Portuguese, Italian, Arabic, Japanese, Hindi, Russian).
"""

import sys
import io
import time

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from publish_to_youtube import get_authenticated_service

def bulk_localize_channel_videos(max_videos: int = 25):
    print("=" * 70)
    print("🌍 KÜRESEL 10 DİL YERELLEŞTİRME VE DÜNYA İNDEKSLEME BAŞLATILDI")
    print("=" * 70)

    youtube = get_authenticated_service()

    # Get recent videos
    v_res = youtube.search().list(part="id,snippet", forMine=True, type="video", order="date", maxResults=max_videos).execute()
    items = v_res.get("items", [])
    print(f"🎬 Toplam {len(items)} video taranıyor...\n")

    updated = 0
    for it in items:
        vid_id = it["id"]["videoId"]
        orig_title = it["snippet"]["title"]
        base_clean = orig_title.replace("👑 ", "").replace(" #shorts", "").replace("#shorts", "").strip()

        locs = {
            "en": {
                "title": f"👑 {base_clean} #shorts"[:100],
                "description": f"{base_clean}\n\nUnwritten power laws, dark psychology & cinema masters.\n⚡ Subscribe for daily 60 FPS analysis: @zirveninkanunu\n\n#shorts #powerlaws #darkpsychology #stoic #sigma"[:5000]
            },
            "es": {
                "title": f"👑 {base_clean} #shorts"[:100],
                "description": f"{base_clean}\n\nLeyes del poder, psicología oscura y maestros del cine.\n⚡ Suscríbete para análisis en 60 FPS: @zirveninkanunu\n\n#shorts #leyesdelpoder #psicologiaoscura #motivacion"[:5000]
            },
            "de": {
                "title": f"👑 {base_clean} #shorts"[:100],
                "description": f"{base_clean}\n\nGesetze der Macht, dunkle Psychologie und Filmgeschichte.\n⚡ Täglich neue 60 FPS Analysen: @zirveninkanunu\n\n#shorts #psychologie #macht #motivation"[:5000]
            },
            "fr": {
                "title": f"👑 {base_clean} #shorts"[:100],
                "description": f"{base_clean}\n\nLois du pouvoir, psychologie sombre et légendes du cinéma.\n⚡ Analyses quotidiennes en 60 FPS: @zirveninkanunu\n\n#shorts #pouvoir #psychologie #motivation"[:5000]
            },
            "pt": {
                "title": f"👑 {base_clean} #shorts"[:100],
                "description": f"{base_clean}\n\nLeis do poder, psicologia sombria e mestres do cinema.\n⚡ Inscreva-se para análises diárias em 60 FPS: @zirveninkanunu\n\n#shorts #leisdopoder #psicologia #sucesso"[:5000]
            },
            "it": {
                "title": f"👑 {base_clean} #shorts"[:100],
                "description": f"{base_clean}\n\nLeggi del potere, psicologia oscura e maestri del cinema.\n⚡ Iscriviti per analisi in 60 FPS: @zirveninkanunu\n\n#shorts #potere #psicologia #cinema"[:5000]
            },
            "ar": {
                "title": f"👑 {base_clean} #shorts"[:100],
                "description": f"{base_clean}\n\nقوانين القوة وعلم النفس المظلم وروائع السينما.\n⚡ اشترك للحصول على تحليلات يومية بدقة 60 إطارًا: @zirveninkanunu\n\n#shorts #علم_النفس #القوة #تحفيز"[:5000]
            },
            "ja": {
                "title": f"👑 {base_clean} #shorts"[:100],
                "description": f"{base_clean}\n\n力の法則、ダーク心理学、そして映画の巨匠たち。\n⚡ 毎日60 FPSで分析をお届け: @zirveninkanunu\n\n#shorts #心理学 #マインドセット #映画"[:5000]
            },
            "hi": {
                "title": f"👑 {base_clean} #shorts"[:100],
                "description": f"{base_clean}\n\nशक्ति के नियम, डार्क साइकोलॉजी और सिनेमा के महानतम क्षण।\n⚡ दैनिक 60 FPS विश्लेषण के लिए सब्सक्राइब करें: @zirveninkanunu\n\n#shorts #प्रेरणा #सफलता #माइंडसेट"[:5000]
            },
            "ru": {
                "title": f"👑 {base_clean} #shorts"[:100],
                "description": f"{base_clean}\n\nЗаконы власти, темная психология и шедевры кино.\n⚡ Подпишитесь на ежедневный разбор в 60 FPS: @zirveninkanunu\n\n#shorts #психология #власть #мотивация"[:5000]
            }
        }

        try:
            youtube.videos().update(
                part="localizations",
                body={"id": vid_id, "localizations": locs}
            ).execute()
            print(f"✅ [{updated+1}/{len(items)}] 10 Dil Mühürlendi: {orig_title[:38]} (ID: {vid_id})")
            updated += 1
            time.sleep(0.5)
        except Exception as e:
            print(f"⚠️ Hata ({vid_id}): {e}")

    print("\n" + "=" * 70)
    print(f"👑 TOPLAM {updated} VİDEO 10 KÜRESEL DİLDE TÜM DÜNYA ARAMALARINA AÇILDI!")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    bulk_localize_channel_videos(25)
