import sys, os
from pathlib import Path
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from publish_to_youtube import get_authenticated_service
from googleapiclient.http import MediaFileUpload

yt = get_authenticated_service()

tasks = [
    {
        "videoId": "Bjygmw4Wel0",
        "lang": "en",
        "name": "English",
        "srt": """1\n00:00:00,000 --> 00:00:05,000\nEvery hour on this planet is seven years on Earth.\n\n2\n00:00:05,000 --> 00:00:10,000\nTime is the only unstoppable weapon; the wise learn to master it.\n\n3\n00:00:10,000 --> 00:00:16,000\nLaw of the Summit: Never lose control of your time.\n"""
    },
    {
        "videoId": "Bjygmw4Wel0",
        "lang": "es",
        "name": "Español",
        "srt": """1\n00:00:00,000 --> 00:00:05,000\nCada hora en este planeta equivale a siete años en la Tierra.\n\n2\n00:00:05,000 --> 00:00:10,000\nEl tiempo es la única arma imparable; los sabios aprenden a dominarlo.\n\n3\n00:00:10,000 --> 00:00:16,000\nLey de la Cima: Nunca pierdas el control de tu tiempo.\n"""
    },
    {
        "videoId": "qC3rlBHh2GY",
        "lang": "en",
        "name": "English",
        "srt": """1\n00:00:00,000 --> 00:00:05,500\n"Now I am become Death, the destroyer of worlds..."\n\n2\n00:00:05,500 --> 00:00:11,000\nOppenheimer and humanity's most dangerous forbidden discovery.\n\n3\n00:00:11,000 --> 00:00:16,000\nLaw of the Summit: True power comes with eternal responsibility.\n"""
    },
    {
        "videoId": "qC3rlBHh2GY",
        "lang": "es",
        "name": "Español",
        "srt": """1\n00:00:00,000 --> 00:00:05,500\n"Ahora me he convertido en la Muerte, el destructor de mundos..."\n\n2\n00:00:05,500 --> 00:00:11,000\nOppenheimer y el descubrimiento prohibido más peligroso de la humanidad.\n\n3\n00:00:11,000 --> 00:00:16,000\nLey de la Cima: El verdadero poder conlleva una responsabilidad eterna.\n"""
    }
]

out_dir = Path("output/captions_temp")
out_dir.mkdir(parents=True, exist_ok=True)

for t in tasks:
    srt_file = out_dir / f"{t['videoId']}_{t['lang']}.srt"
    with open(srt_file, "w", encoding="utf-8") as f:
        f.write(t["srt"])
    
    try:
        req = yt.captions().insert(
            part="snippet",
            body={
                "snippet": {
                    "videoId": t["videoId"],
                    "language": t["lang"],
                    "name": t["name"],
                    "isDraft": False
                }
            },
            media_body=MediaFileUpload(str(srt_file), mimetype="application/x-subrip", resumable=True)
        )
        res = req.execute()
        print(f"✅ Başarılı: {t['videoId']} [{t['lang']}] Altyazı Yüklendi! ID: {res.get('id')}")
    except Exception as e:
        print(f"⚠️ Hata: {t['videoId']} [{t['lang']}]: {e}")
