"""Episode audio for the Zirvenin Kanunu animation: the narrator (ElevenLabs, ZK's own voice), sound effects on
the visual beats and a dark music bed.

make(ep, work, data) -> (timings, mix.mp3, characters). The whole script goes to the voice in one request, so the
delivery flows like one take; the character alignment gives each display word its time (digits stay digits on
screen, sayi.spoken() maps them to the spoken words). Music and the stock effects come from channel-data/audio;
effects this format adds are generated once and kept in animasyon/ses/.
"""
import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path

import numpy as np
import requests

import sayi

HERE = Path(__file__).resolve().parent
LIB = HERE / "ses"
SR = 44100
API = "https://api.elevenlabs.io/v1"
LEAD = 0.12  # the hook starts at once: the first frame of a Short decides the swipe
# The channel's narrator (repo variable ELEVENLABS_VOICE_ID, Ulaş's "Alp"), a little slower and darker than the
# curiosity Shorts' 1.15: this series is told, not announced.
ALP = "HV5rAif1q1ITHdvxqyMw"  # app/services/elevenlabs_engine.py DEFAULT_TURKISH_VOICE_ID, "Alp - Deep & Cinematic Baritone"
VOICE = {"voice_id": (os.environ.get("ELEVENLABS_VOICE_ID") or "").strip() or ALP,
         "model": os.environ.get("ZK_ANIM_MODEL") or os.environ.get("ELEVENLABS_MODEL") or "eleven_flash_v2_5",
         "speed": float(os.environ.get("ZK_ANIM_SPEED") or 1.1), "stability": float(os.environ.get("ZK_ANIM_STABILITY") or 0.42),
         "style": float(os.environ.get("ZK_ANIM_STYLE") or 0.5), "similarity_boost": 0.8}
STOCK = {"impact": "sfx/impact.mp3", "whoosh": "sfx/whoosh.mp3", "riser": "sfx/riser.mp3", "pop": "sfx/pop.mp3"}
MADE = {
    "stamp": ("Heavy rubber stamp slammed onto a wooden desk, single dull thud, close and dry, no voice", 1.0),
    "paper": ("Old parchment letter sliding across a wooden table, short soft paper rustle, no voice", 1.2),
    "shimmer": ("Soft dark magical shimmer, low warm chime with a short sparkle tail, cinematic, no voice", 1.6),
}
MUSIC = "music/karakter_2.mp3"  # "modern dark cinematic beat: deep sub bass, sparse haunting piano, ticking hi-hats"


def hdr():
    return {"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}


def sha(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def decode(path):
    cmd = ["ffmpeg", "-loglevel", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"]
    return np.frombuffer(subprocess.run(cmd, check=True, capture_output=True).stdout, dtype=np.float32).copy()


def loudnorm(x, target):
    raw = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "-",
         "-af", f"loudnorm=I={target}:TP=-1.5:LRA=11", "-ar", str(SR), "-f", "f32le", "-"],
        input=x.astype(np.float32).tobytes(), check=True, capture_output=True).stdout
    y = np.frombuffer(raw, dtype=np.float32).copy()
    return y[: len(x)] if len(y) >= len(x) else np.pad(y, (0, len(x) - len(y)))


def tts(text, cache):
    if not VOICE["voice_id"]:
        raise RuntimeError("ELEVENLABS_VOICE_ID yok (kanalın anlatıcı sesi)")
    h = sha("tts", text, VOICE)
    mp3, js = cache / f"ses_{h}.mp3", cache / f"ses_{h}.json"
    if not mp3.exists():
        body = {"text": text, "model_id": VOICE["model"], "language_code": "tr", "voice_settings": {
            "stability": VOICE["stability"], "similarity_boost": VOICE["similarity_boost"], "style": VOICE["style"],
            "use_speaker_boost": True, "speed": VOICE["speed"]}}
        url = f"{API}/text-to-speech/{VOICE['voice_id']}/with-timestamps?output_format=mp3_44100_128"
        r = requests.post(url, headers=hdr(), json=body, timeout=180)
        if r.status_code == 400 and "language_code" in r.text:  # a model without language_code
            body.pop("language_code")
            r = requests.post(url, headers=hdr(), json=body, timeout=180)
        if not r.ok:
            raise RuntimeError(f"ElevenLabs ses üretmedi ({r.status_code}): {r.text[:300]}")
        d = r.json()
        mp3.write_bytes(base64.b64decode(d["audio_base64"]))
        js.write_text(json.dumps(d.get("alignment") or d.get("normalized_alignment"), ensure_ascii=False))
    return mp3, json.loads(js.read_text())


def spans(text, al):
    """[start, end] of each space-separated word of `text`, from the character alignment."""
    out, cur = [], None
    for c, a, b in zip(al["characters"], al["character_start_times_seconds"], al["character_end_times_seconds"]):
        if c.isspace():
            if cur:
                out.append(cur)
                cur = None
            continue
        cur = [a, b] if cur is None else [cur[0], b]
    if cur:
        out.append(cur)
    ws = text.split()
    if len(out) != len(ws):  # proportional fallback
        total = al["character_end_times_seconds"][-1]
        size = sum(len(w) + 1 for w in ws)
        acc, out = 0.0, []
        for w in ws:
            d = total * (len(w) + 1) / size
            out.append([acc, acc + d])
            acc += d
    return out


def effect(name, data):
    if name in STOCK:
        return data / "audio" / STOCK[name]
    LIB.mkdir(exist_ok=True)
    prompt, dur = MADE[name]
    path = LIB / f"{name}_{sha(prompt, dur)}.mp3"
    if not path.exists():
        r = requests.post(f"{API}/sound-generation", headers=hdr(), timeout=120,
                          json={"text": prompt, "duration_seconds": dur, "prompt_influence": 0.6})
        r.raise_for_status()
        path.write_bytes(r.content)
        print(f"  ElevenLabs efekt üretildi: {name}")
    return path


def word_time(line, key, share=0.35):
    if key:
        k = sayi_norm(key)
        for w in line["words"]:
            nw = sayi_norm(w["w"])
            if nw.startswith(k) or (len(k) > 3 and k in nw):
                return w["t0"]
    return line["t0"] + (line["t1"] - line["t0"]) * share


def sayi_norm(word):
    return "".join(c for c in str(word).replace("İ", "i").replace("I", "ı").lower() if c.isalnum())


def cues(ep, timings):
    """(time, effect, gain): the same moments motor.html animates."""
    out, prev = [(0.0, "impact", 0.55)], None
    for i, (L, tm) in enumerate(zip(ep["lines"], timings)):
        v = L.get("v") or {}
        if i and v != prev:
            out.append((tm["t0"] - 0.16, "whoosh", 0.28))
        kind = v.get("t")
        if kind == "usta" and v.get("prop"):
            out.append((word_time(tm, v.get("key"), .6), "shimmer", 0.4))
        elif kind == "usta" and v.get("end"):
            out.append((tm["t0"], "impact", 0.35))
        elif kind == "meclis":
            out.append((word_time(tm, v.get("key"), .5), "impact", 0.3))
        elif kind == "yasak":
            for k, it in enumerate(v.get("items") or []):
                out.append((word_time(tm, it.get("key"), .3 + .4 * k), "stamp", 0.55))
        elif kind == "kitaplik":
            out.append((word_time(tm, v.get("key"), .35), "shimmer", 0.35))
            out.append((word_time(tm, v.get("note"), .8), "paper", 0.45))
        elif kind == "dost":
            out.append((word_time(tm, v.get("give"), .3), "whoosh", 0.22))
            out.append((word_time(tm, v.get("key"), .7), "shimmer", 0.45))
        elif kind == "beyin":
            out.append((tm["t0"] - 2.6, "riser", 0.25))
            for k, s in enumerate(v.get("steps") or []):
                out.append((word_time(tm, s.get("key"), .3 + .4 * k), "pop", 0.3))
        prev = v
    return out


def make(ep, work, data):
    work, data = Path(work), Path(data)
    cache = work / "audio"
    cache.mkdir(parents=True, exist_ok=True)
    said, groups = [], []
    for line in ep["lines"]:
        s, g = sayi.spoken(line["text"])
        said.append(s)
        groups.append(g)
    text = " ".join(said)
    mp3, al = tts(text, cache)
    sp = spans(text, al)
    timings, k = [], 0
    for g in groups:
        words = []
        for disp, n in g:  # display word <- the spoken words it became
            a, b = sp[min(k, len(sp) - 1)][0], sp[min(k + n - 1, len(sp) - 1)][1]
            words.append({"w": disp, "t0": LEAD + a, "t1": LEAD + b})
            k += n
        timings.append({"t0": words[0]["t0"], "t1": words[-1]["t1"], "words": words})

    voice_raw = decode(mp3)
    total = timings[-1]["t1"] + 0.45
    n = int(total * SR)
    voice = np.zeros(n, np.float32)
    s = int(LEAD * SR)
    voice[s:s + len(voice_raw)] += voice_raw[: max(0, n - s)]
    voice = loudnorm(voice, -16)

    fx = np.zeros(n, np.float32)
    for at, name, gain in cues(ep, timings):
        try:
            a = decode(effect(name, data))
        except (requests.RequestException, subprocess.CalledProcessError, OSError) as e:
            print(f"  efekt atlandı ({name}): {e}")
            continue
        a = a / (np.max(np.abs(a)) + 1e-6) * gain
        st = int(at * SR)
        if st < 0:
            a, st = a[-st:], 0
        fx[st:st + len(a)] += a[: max(0, n - st)]

    m = decode(data / "audio" / MUSIC)
    if len(m) < n:
        m = np.tile(m, int(np.ceil(n / len(m))))
    m = loudnorm(m[:n], -29)
    fade = int(0.35 * SR)  # short ends: the video loops straight back into the hook
    m[-fade:] *= np.linspace(1, 0.3, fade)

    mix = voice + fx + m
    mix = np.clip(mix / max(1.0, np.max(np.abs(mix)) / 0.97), -1, 1)
    out = work / "mix.mp3"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "-",
                    "-af", "loudnorm=I=-14:TP=-1.0:LRA=11,alimiter=limit=0.95", "-c:a", "libmp3lame", "-b:a", "192k",
                    str(out)], input=mix.astype(np.float32).tobytes(), check=True)
    (work / "timings.json").write_text(json.dumps(timings, ensure_ascii=False, indent=1))
    print(f"Ses hazır: {total:.1f} sn, {len(text)} karakter, model {VOICE['model']}, hız {VOICE['speed']}")
    return timings, out, len(text)
