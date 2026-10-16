"""Episode audio: ElevenLabs voices with word timings, sound effects on the visual beats, background music.

ses.make(ep, workdir) -> (timings, mix.mp3 path). Timings are per line: {t0, t1, words: [{w, t0, t1}]} where
the words are the DISPLAY words (digits kept), mapped from the spoken words via sayi.spoken().
Sound effects are generated once and kept in fabrika/ses/ (committed), so they cost credits only once.
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
MUSIC = [HERE.parent / "pilot-ay" / "audio" / "music_3abbe67a34c3e074.mp3"]
SR = 44100
API = "https://api.elevenlabs.io/v1"
MODEL = os.environ.get("YO_TTS_MODEL") or "eleven_flash_v2_5"  # half the credits of multilingual v2
VOICES = {
    "pi": {"voice_id": "QftNgZhlSOYKrYbk7WR7", "name": "Emrullah Tuna", "stability": 0.4, "similarity_boost": 0.8,
           "style": 0.45, "speed": 1.08, "pitch": 1.0},
    "bit": {"voice_id": "A2XgcJ6lQVEFeIaIUyrc", "name": "Damla", "stability": 0.4, "similarity_boost": 0.8,
            "style": 0.5, "speed": 1.1, "pitch": 1.1},
}
SFX = {
    "pop": ("Short soft cartoon UI pop, bubbly and clean, no echo", 1.0),
    "whoosh": ("Quick soft airy whoosh transition, light and clean, no voice", 1.0),
    "poof": ("Magical cartoon poof with a short sparkle shimmer, an object disappearing, clean, no voice", 1.6),
    "boom": ("Soft cartoon explosion boom, punchy but not harsh, short tail, no voice", 1.6),
    "ding": ("Bright friendly notification ding, single bell chime, clean", 1.2),
}


def hdr():
    return {"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}


def sha(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def norm(word):
    return "".join(c for c in word.replace("İ", "i").replace("I", "ı").lower() if c.isalnum())


def decode(path, filters=None):
    cmd = ["ffmpeg", "-loglevel", "error", "-i", str(path)]
    if filters:
        cmd += ["-af", filters]
    cmd += ["-f", "f32le", "-ac", "1", "-ar", str(SR), "-"]
    return np.frombuffer(subprocess.run(cmd, check=True, capture_output=True).stdout, dtype=np.float32).copy()


def loudnorm(x, target):
    raw = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "-",
         "-af", f"loudnorm=I={target}:TP=-1.5:LRA=11", "-ar", str(SR), "-f", "f32le", "-"],
        input=x.astype(np.float32).tobytes(), check=True, capture_output=True).stdout
    y = np.frombuffer(raw, dtype=np.float32).copy()
    return y[: len(x)] if len(y) >= len(x) else np.pad(y, (0, len(x) - len(y)))


def credits():
    """(used, limit) characters of the ElevenLabs plan, or None when the key may not read it."""
    try:
        r = requests.get(f"{API}/user/subscription", headers=hdr(), timeout=30)
        if r.ok:
            d = r.json()
            return d.get("character_count"), d.get("character_limit")
    except requests.RequestException:
        pass
    return None


def tts(text, v, cache):
    h = sha("tts", text, v, MODEL)
    mp3, js = cache / f"line_{h}.mp3", cache / f"line_{h}.json"
    if not mp3.exists():
        body = {"text": text, "model_id": MODEL, "language_code": "tr", "voice_settings": {
            "stability": v["stability"], "similarity_boost": v["similarity_boost"], "style": v["style"],
            "use_speaker_boost": True, "speed": v["speed"]}}
        r = requests.post(f"{API}/text-to-speech/{v['voice_id']}/with-timestamps?output_format=mp3_44100_128",
                          headers=hdr(), json=body, timeout=120)
        if r.status_code == 400 and "language_code" in r.text:  # model without language_code support
            body.pop("language_code")
            r = requests.post(f"{API}/text-to-speech/{v['voice_id']}/with-timestamps?output_format=mp3_44100_128",
                              headers=hdr(), json=body, timeout=120)
        if not r.ok:
            raise RuntimeError(f"ElevenLabs ses üretmedi ({r.status_code}): {r.text[:300]}")
        d = r.json()
        mp3.write_bytes(base64.b64decode(d["audio_base64"]))
        js.write_text(json.dumps(d.get("alignment") or d.get("normalized_alignment"), ensure_ascii=False))
    return mp3, json.loads(js.read_text())


def spoken_spans(text, al):
    """Start/end of each space-separated word of `text` from the character alignment."""
    n = len(text.split())
    chars, st, en = al["characters"], al["character_start_times_seconds"], al["character_end_times_seconds"]
    spans, cur = [], None
    for c, a, b in zip(chars, st, en):
        if c.isspace():
            if cur:
                spans.append(cur)
                cur = None
            continue
        cur = [a, b] if cur is None else [cur[0], b]
    if cur:
        spans.append(cur)
    if len(spans) != n:  # proportional fallback
        total = en[-1] if en else 1.0
        ws = text.split()
        size = sum(len(w) + 1 for w in ws)
        acc, spans = 0.0, []
        for w in ws:
            d = total * (len(w) + 1) / size
            spans.append([acc, acc + d])
            acc += d
    return spans


def sfx(name):
    LIB.mkdir(exist_ok=True)
    prompt, dur = SFX[name]
    path = LIB / f"sfx_{name}_{sha(prompt, dur)}.mp3"
    if not path.exists():
        r = requests.post(f"{API}/sound-generation", headers=hdr(), timeout=120,
                          json={"text": prompt, "duration_seconds": dur, "prompt_influence": 0.6})
        r.raise_for_status()
        path.write_bytes(r.content)
        print(f"  ElevenLabs efekt üretildi: {name}")
    return path


def word_time(line, key, share=0.35):
    if key:
        k = norm(str(key))
        for w in line["words"]:
            nw = norm(w["w"])
            if nw.startswith(k) or (len(k) > 3 and k in nw):
                return w["t0"]
    return line["t0"] + (line["t1"] - line["t0"]) * share


def cues(ep, timings):
    """Sound effects that follow the pictures: whoosh on scene changes, pop when a label or number lands."""
    out, prev = [], None
    for i, (L, tm) in enumerate(zip(ep["lines"], timings)):
        v = L.get("v") or {}
        if i and v != prev:
            out.append((tm["t0"] - 0.18, "whoosh", 0.22))
        kind = v.get("t")
        if kind == "outro":
            out.append((tm["t0"] + 0.4, "ding", 0.35))
        elif kind == "space" and v.get("do") in ("vanish", "explode"):
            out.append((word_time(tm, v.get("key"), .3), "boom" if v["do"] == "explode" else "poof", 0.45))
        elif kind == "icon" and v.get("do") in ("vanish", "multiply", "burn", "freeze"):
            out.append((word_time(tm, v.get("key"), .3), "poof", 0.4))
        if kind == "number" or v.get("label"):
            out.append((word_time(tm, v.get("key") or v.get("value"), .25 if kind == "number" else .3), "pop", 0.32))
        elif kind in ("icons", "timeline"):
            n = len(v.get("items") or v.get("steps") or [])
            for k in range(n):
                out.append((tm["t0"] + (tm["t1"] - tm["t0"]) * (.12 + .7 * k / max(n, 1)), "pop", 0.26))
        elif kind == "split":
            out.append((word_time(tm, v.get("key"), .45), "pop", 0.3))
        prev = v
    return out


def make(ep, work):
    work = Path(work)
    cache = work / "audio"
    cache.mkdir(parents=True, exist_ok=True)
    timings, clips, t = [], [], 0.35
    lines = ep["lines"]
    for i, line in enumerate(lines):
        v = VOICES[line["who"]]
        say, groups = sayi.spoken(line["text"])
        mp3, al = tts(say, v, cache)
        p = v.get("pitch", 1.0)
        filt = (f"asetrate={SR}*{p},aresample={SR},atempo={1 / p:.5f},chorus=0.6:0.8:18:0.25:0.3:2"
                if p != 1.0 else None)
        audio = decode(mp3, filt)
        spans = spoken_spans(say, al)
        words, k = [], 0
        for disp, n in groups:  # display word <- the spoken words it became
            a, b = spans[min(k, len(spans) - 1)][0], spans[min(k + n - 1, len(spans) - 1)][1]
            words.append({"w": disp, "t0": t + a, "t1": t + b})
            k += n
        dur = max(len(audio) / SR, spans[-1][1])
        timings.append({"t0": t, "t1": t + spans[-1][1], "words": words})
        clips.append((t, audio))
        nxt = lines[i + 1]["who"] if i + 1 < len(lines) else None
        t += dur + (0.38 if nxt and nxt != line["who"] else 0.2)

    total = timings[-1]["t1"] + 1.6
    n = int(total * SR) + SR
    voice = np.zeros(n, np.float32)
    for start, a in clips:
        s = int(start * SR)
        voice[s:s + len(a)] += a[: n - s]
    voice = loudnorm(voice, -16)

    fx = np.zeros(n, np.float32)
    for at, name, gain in cues(ep, timings):
        try:
            a = decode(sfx(name))
        except (requests.RequestException, subprocess.CalledProcessError) as e:
            print(f"  efekt atlandı ({name}): {e}")
            continue
        a = a / (np.max(np.abs(a)) + 1e-6) * gain
        s = max(0, int(at * SR))
        fx[s:s + len(a)] += a[: n - s]

    m = decode(MUSIC[int(sha(ep["title"]), 16) % len(MUSIC)])
    if len(m) < n:
        m = np.tile(m, int(np.ceil(n / len(m))))
    m = loudnorm(m[:n], -30)
    fade = int(1.5 * SR)
    m[-fade:] *= np.linspace(1, 0, fade)
    m[:int(0.5 * SR)] *= np.linspace(0, 1, int(0.5 * SR))

    mix = voice + fx + m
    mix = np.clip(mix / max(1.0, np.max(np.abs(mix)) / 0.97), -1, 1)
    out = work / "mix.mp3"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "-",
                    "-af", "loudnorm=I=-14:TP=-1.0:LRA=11,alimiter=limit=0.95", "-c:a", "libmp3lame", "-b:a", "192k",
                    str(out)], input=mix.astype(np.float32).tobytes(), check=True)
    (work / "timings.json").write_text(json.dumps(timings, ensure_ascii=False, indent=1))
    chars = sum(len(sayi.spoken(l["text"])[0]) for l in lines)
    print(f"Ses hazır: {total:.1f} sn, {chars} karakter, model {MODEL}")
    return timings, out, chars
