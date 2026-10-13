"""
One-time music and sound-effect library for Zirvenin Kanunu.

The old assets/sfx files are sine-wave synths and assets/music holds four
generic tracks, which made the Shorts sound cheap. This script generates
instrumental tracks per series and three real sound effects with the
ElevenLabs plan's credits (the Creator plan allows commercial use) and writes
them to channel-data/audio/, which the workflow commits to the channel-data
branch. Files that already exist are kept, so a re-run only fills gaps.

  python scripts/build_audio_library.py
"""

import json
from pathlib import Path
import sys
import urllib.error
import urllib.request

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

LIBRARY = BASE_DIR / "channel-data" / "audio"
API = "https://api.elevenlabs.io/v1"
MUSIC_SECONDS = 50  # longer than any Short; the mix loops and fades it

MUSIC = {
    "merak_1": "Instrumental light curious electronic background for a fun science fact video: plucky synth arpeggio, "
               "soft claps, gentle bass, playful and bright, low energy so a voice stays on top, no vocals, 110 bpm",
    "merak_2": "Instrumental intriguing discovery underscore: ticking light percussion, airy pads, soft marimba motif, "
               "slight suspense that opens into wonder, no vocals, 100 bpm",
    "merak_3": "Instrumental upbeat light pizzicato strings and glockenspiel, quirky and cheerful, sparse arrangement "
               "for narration, no vocals, 115 bpm",
    "story_1": "Instrumental cinematic documentary underscore for a true historical story: low cello ostinato, "
               "soft taiko pulse, distant wordless choir pad, slowly building tension, no vocals, 85 bpm",
    "story_2": "Instrumental mysterious ancient world ambience: breathy duduk melody, frame drum, deep drones, "
               "suspenseful and atmospheric, no vocals",
    "story_3": "Instrumental restrained epic history bed: warm string swells, deep drums, rising tension that "
               "settles into calm resolve, no vocals",
    "karakter_1": "Instrumental dark crime drama underscore: brooding cello, muted reverb electric guitar, slow heavy "
                  "drums, 1920s noir tension, no vocals, 80 bpm",
    "karakter_2": "Instrumental modern dark cinematic beat: deep sub bass, sparse haunting piano, ticking hi-hats, "
                  "menacing atmosphere, no vocals",
    "karakter_3": "Instrumental psychological thriller underscore: pulsing synth bass, ticking clock texture, eerie "
                  "high strings, steadily growing suspense, no vocals",
}
SFX = {
    "whoosh": ("Fast clean cinematic whoosh transition, airy swish, short", 1.0),
    "impact": ("Deep cinematic boom impact with a long sub bass tail", 2.0),
    "riser": ("Short cinematic tension riser building up, ends abruptly", 3.0),
}


def _request(path: str, key: str, payload: dict | None = None) -> bytes:
    req = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={"xi-api-key": key, "Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return resp.read()


def _is_audio(data: bytes) -> bool:
    return data[:3] == b"ID3" or (len(data) > 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0)


def credits_used(key: str) -> int | None:
    try:
        return int(json.loads(_request("/user/subscription", key)).get("character_count"))
    except Exception:
        return None


def generate(path: str, key: str, payloads: list[dict], dest: Path) -> str:
    """Tries each payload in turn (the first may use optional fields an older API rejects)."""
    last = "denenmedi"
    for payload in payloads:
        try:
            data = _request(path, key, payload)
        except urllib.error.HTTPError as e:
            detail = " ".join(e.read().decode("utf-8", "replace").split())[:300]
            last = f"HTTP {e.code}: {detail}"
            if e.code in (400, 422):
                continue
            return f"hata ({last})"
        except Exception as e:
            return f"hata ({str(e)[:200]})"
        if not _is_audio(data):
            last = f"ses dosyası dönmedi ({data[:120]!r})"
            continue
        dest.write_bytes(data)
        return f"yazıldı ({len(data) // 1024} KB)"
    return f"hata ({last})"


def main() -> int:
    from app.services.elevenlabs_engine import get_elevenlabs_api_key

    key = get_elevenlabs_api_key()
    if not key:
        print("ELEVENLABS_API_KEY yok; kütüphane üretilemedi.")
        return 1
    (LIBRARY / "music").mkdir(parents=True, exist_ok=True)
    (LIBRARY / "sfx").mkdir(parents=True, exist_ok=True)
    before = credits_used(key)
    results = []

    for name, prompt in MUSIC.items():
        dest = LIBRARY / "music" / f"{name}.mp3"
        if dest.exists():
            results.append(f"müzik {name}: zaten var")
            continue
        status = generate("/music?output_format=mp3_44100_128", key, [
            {"prompt": prompt, "music_length_ms": MUSIC_SECONDS * 1000, "model_id": "music_v1", "force_instrumental": True},
            {"prompt": prompt, "music_length_ms": MUSIC_SECONDS * 1000},
        ], dest)
        results.append(f"müzik {name}: {status}")
        print(results[-1], flush=True)

    for name, (prompt, seconds) in SFX.items():
        dest = LIBRARY / "sfx" / f"{name}.mp3"
        if dest.exists():
            results.append(f"efekt {name}: zaten var")
            continue
        status = generate("/sound-generation?output_format=mp3_44100_128", key, [
            {"text": prompt, "duration_seconds": seconds, "prompt_influence": 0.6},
            {"text": prompt},
        ], dest)
        results.append(f"efekt {name}: {status}")
        print(results[-1], flush=True)

    after = credits_used(key)
    spent = f"{after - before} kredi" if before is not None and after is not None else "okunamadı"
    print("\n### Ses kütüphanesi\n\n" + "\n".join(f"- {r}" for r in results) + f"\n\nHarcanan ElevenLabs kredisi: {spent}\n")
    return 0 if any("yazıldı" in r or "zaten var" in r for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
