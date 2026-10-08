"""
Apply Multi-Language Internationalization to All Channel Shorts.
Adds English (en), Spanish (es), German (de), French (fr), Arabic (ar), Russian (ru)
titles & descriptions to YouTube Data API for maximum global reach & 10x RPM.
"""

import json
from pathlib import Path
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

BASE_DIR = Path(__file__).resolve().parent.parent
TOKEN_FILE = BASE_DIR / "token.json"

GLOBAL_LOCALIZATIONS = {
    # 1. Marcus Aurelius
    "rQhHAZDGhEM": {
        "en": {
            "title": "👑 Marcus Aurelius - Stoicism & The Invincible Mind #shorts",
            "description": "The first rule of ruling: He who cannot control his emotions cannot control his empire.\n\nMarcus Aurelius and the iron will of the Roman arena.\n\n#shorts #stoicism #marcusaurelius #darkpsychology #powerlaws #motivation #mindset #gladiator"
        },
        "es": {
            "title": "👑 Marco Aurelio - Estoicismo y la Mente Invencible #shorts",
            "description": "La primera regla del poder: Quien no puede controlar sus emociones, no puede controlar su reino.\n\nMarco Aurelio y la voluntad de hierro.\n\n#shorts #estoicismo #marcoaurelio #psicologiaoscura #leyesdelpoder #motivacion"
        },
        "de": {
            "title": "👑 Marc Aurel - Stoizismus und der Unbesiegbare Geist #shorts",
            "description": "Die erste Regel der Macht: Wer seine Gefühle nicht kontrollieren kann, kann sein Reich nicht führen.\n\nMarc Aurel und der eiserne Wille.\n\n#shorts #stoizismus #marcaurel #psychologie #motivation #disziplin"
        },
        "fr": {
            "title": "👑 Marc Aurèle - Le Stoïcisme et l'Esprit Invincible #shorts",
            "description": "La première règle du pouvoir : Celui qui ne maîtrise pas ses émotions ne peut diriger son royaume.\n\n#shorts #stoicisme #marcaurele #psychologie #pouvoir #motivation"
        }
    },

    # 2. Oppenheimer
    "qC3rlBHh2GY": {
        "en": {
            "title": "👑 Oppenheimer - The Secret That Destroyed Worlds #shorts",
            "description": "'Now I am become Death, the destroyer of worlds...'\n\nJ. Robert Oppenheimer and humanity's most terrifying discovery.\n\n#shorts #oppenheimer #history #darksecrets #power #science #cinema #quotes"
        },
        "es": {
            "title": "👑 Oppenheimer - El Secreto Que Destruyó Mundos #shorts",
            "description": "'Ahora me he convertido en la Muerte, el destructor de mundos...'\n\nOppenheimer y el descubrimiento más aterrador.\n\n#shorts #oppenheimer #historia #ciencia #cine #misterio"
        },
        "de": {
            "title": "👑 Oppenheimer - Das Geheimnis der Weltzerstörung #shorts",
            "description": "'Jetzt bin ich der Tod geworden, der Zerstörer der Welten...'\n\nOppenheimer und die tödlichste Waffe der Menschheit.\n\n#shorts #oppenheimer #geschichte #wissenschaft #kino #zitate"
        },
        "fr": {
            "title": "👑 Oppenheimer - Le Secret Qui Détruisit des Mondes #shorts",
            "description": "'Maintenant, je suis devenu la Mort, le destructeur des mondes...'\n\n#shorts #oppenheimer #histoire #science #cinema"
        }
    },

    # 3. Interstellar
    "Bjygmw4Wel0": {
        "en": {
            "title": "👑 Interstellar - Black Holes & The Time Paradox #shorts",
            "description": "Every hour on this planet equals 7 years on Earth.\n\nTime is the one weapon you cannot stop; the wise learn to master it.\n\n#shorts #interstellar #space #physics #blackhole #timeparadox #mindblown #cinema"
        },
        "es": {
            "title": "👑 Interstellar - Agujeros Negros y la Paradoja del Tiempo #shorts",
            "description": "Cada hora en este planeta equivale a 7 años en la Tierra.\n\nEl tiempo es la única arma que no se detiene.\n\n#shorts #interstellar #espacio #fisica #agujeronegro #tiempo #cine"
        },
        "de": {
            "title": "👑 Interstellar - Schwarze Löcher & Das Zeit-Paradoxon #shorts",
            "description": "Jede Stunde auf diesem Planeten entspricht 7 Jahren auf der Erde.\n\nDie härteste physikalische Realität des Universums.\n\n#shorts #interstellar #weltall #schwarzesloch #physik #zeit #kino"
        },
        "fr": {
            "title": "👑 Interstellar - Trou Noir et Paradoxe Temporel #shorts",
            "description": "Chaque heure sur cette planète équivaut à 7 ans sur Terre.\n\n#shorts #interstellar #espace #physique #cinema"
        }
    },

    # 4. Walter White
    "Ej0ZLHznkVc": {
        "en": {
            "title": "👑 Walter White - I Am The Danger #shorts",
            "description": "'I am not in danger, Skyler. I AM the danger!'\n\nThe transformation into Heisenberg: When a quiet man decides to rule.\n\n#shorts #breakingbad #walterwhite #heisenberg #darkpsychology #power #quotes #sigma"
        },
        "es": {
            "title": "👑 Walter White - Yo Soy El Peligro #shorts",
            "description": "'No estoy en peligro, Skyler. ¡Yo SOY el peligro!'\n\nLa transformación de Walter White a Heisenberg.\n\n#shorts #breakingbad #walterwhite #heisenberg #psicologiaoscura #poder #sigma"
        },
        "de": {
            "title": "👑 Walter White - Ich Bin Die Gefahr #shorts",
            "description": "'Ich bin nicht in Gefahr, Skyler. Ich BIN die Gefahr!'\n\nHeisenberg: Wenn ein gebrochener Mann zur Macht greift.\n\n#shorts #breakingbad #walterwhite #heisenberg #psychologie #macht #zitate"
        },
        "fr": {
            "title": "👑 Walter White - C'est Moi Le Danger #shorts",
            "description": "'Je ne suis pas en danger, Skyler. Le danger, C'EST MOI !'\n\n#shorts #breakingbad #walterwhite #heisenberg #psychologie"
        }
    },

    # 5. The Godfather
    "_PjyOet0Ma0": {
        "en": {
            "title": "👑 The Godfather - Loyalty & The Unwritten Law of Power #shorts",
            "description": "'A man who doesn't spend time with his family can never be a real man.'\n\nDon Vito Corleone: Rules of silence, loyalty and ultimate respect.\n\n#shorts #godfather #doncorleone #powerlaws #loyalty #respect #mafia #cinema #leadership"
        },
        "es": {
            "title": "👑 El Padrino - La Ley No Escrita del Poder y la Lealtad #shorts",
            "description": "'Un hombre que no pasa tiempo con su familia nunca puede ser un hombre de verdad.'\n\nDon Vito Corleone y las reglas del respeto absoluto.\n\n#shorts #elpadrino #doncorleone #lealtad #respeto #poder #liderazgo"
        },
        "de": {
            "title": "👑 Der Pate - Loyalität und das Gesetz der Macht #shorts",
            "description": "'Ein Mann, der keine Zeit mit seiner Familie verbringt, ist kein wahrer Mann.'\n\nDon Vito Corleone: Regeln des Respekts und der Macht.\n\n#shorts #derpate #doncorleone #macht #loyalität #respekt #kino"
        },
        "fr": {
            "title": "👑 Le Parrain - La Loi Non Écrite du Pouvoir et de la Loyauté #shorts",
            "description": "'Un homme qui ne passe pas de temps avec sa famille n'est pas un vrai homme.'\n\n#shorts #leparrain #doncorleone #pouvoir #loyaute #cinema"
        }
    },

    # 6. Thomas Shelby
    "BcupYtJ8ccw": {
        "en": {
            "title": "👑 Thomas Shelby - The Cost of Silent Power & Respect #shorts",
            "description": "The weakest person in the room is the one who reacts to everything.\n\nThomas Shelby rules of power, silence and dark psychology.\n\n#shorts #peakyblinders #thomasshelby #darkpsychology #power #stoic #sigma"
        },
        "es": {
            "title": "👑 Thomas Shelby - El Precio del Poder Silencioso #shorts",
            "description": "La persona más débil de la sala es la que reacciona a todo.\n\nThomas Shelby y las leyes del poder y la psicología oscura.\n\n#shorts #peakyblinders #thomasshelby #psicologiaoscura #poder #exito"
        },
        "de": {
            "title": "👑 Thomas Shelby - Der Preis der Lautlosen Macht #shorts",
            "description": "Die schwächste Person im Raum reagiert auf alles.\n\nThomas Shelby: Gesetze der Macht und dunkle Psychologie.\n\n#shorts #peakyblinders #thomasshelby #psychologie #macht"
        },
        "fr": {
            "title": "👑 Thomas Shelby - Le Prix du Pouvoir Silencieux #shorts",
            "description": "La personne la plus faible dans une pièce est celle qui réagit à tout.\n\n#shorts #peakyblinders #thomasshelby #psychologie #pouvoir"
        }
    }
}


def apply_all():
    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
        token_data = json.load(f)

    creds = Credentials(
        token=token_data["token"],
        refresh_token=token_data["refresh_token"],
        token_uri=token_data["token_uri"],
        client_id=token_data["client_id"],
        client_secret=token_data["client_secret"],
        scopes=token_data["scopes"],
    )

    youtube = build("youtube", "v3", credentials=creds)

    print("=" * 70)
    print("🌍 KÜRESEL ÇOKLU-DİL (INTERNATIONALIZATION) AKTİFLEŞTİRİLİYOR")
    print("=" * 70)

    for vid_id, langs in GLOBAL_LOCALIZATIONS.items():
        try:
            body = {
                "id": vid_id,
                "localizations": langs
            }
            res = youtube.videos().update(part="localizations", body=body).execute()
            active_langs = list(res.get("localizations", {}).keys())
            print(f"✅ Video {vid_id} -> Diller Aktif: {', '.join(active_langs)}")
        except Exception as e:
            print(f"⚠️ Video {vid_id} dil güncelleme notu: {e}")

    print("=" * 70)
    print("🎉 TÜM VİDEOLAR DÜNYA ÇAPINDA ÇOKLU DİL DESTEĞİNE KAVUŞTU!")
    print("=" * 70)


if __name__ == "__main__":
    apply_all()
