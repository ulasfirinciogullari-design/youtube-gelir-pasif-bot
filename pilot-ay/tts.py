"""YA OLSAYDI? audio: ElevenLabs voices (with word timings), SFX and music -> timings.json + mix.mp3.

Reads script.js (window.SCRIPT / CUES / VOICES). Generated audio is cached under audio/ by a hash of
its inputs, so re-running after a visual-only change spends no ElevenLabs credits.
"""
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import requests

HERE = Path(__file__).resolve().parent
CACHE = HERE / "audio"
SR = 44100
API = "https://api.elevenlabs.io/v1"
KEY = os.environ["ELEVENLABS_API_KEY"]
HDR = {"xi-api-key": KEY}

MUSIC_PROMPT = ("Light, curious and playful science explainer background music. Pizzicato strings, soft marimba, "
                "warm synth pads, gentle percussion, around 100 bpm, optimistic, no vocals, even energy, loopable.")
SFX = {
    "poof": "Magical cartoon poof with a short sparkle shimmer, an object disappearing, clean, no voice",
    "pop": "Short soft cartoon UI pop, bubbly and clean, no echo",
}


def js_value(src, name):
    m = re.search(rf"window\.{name} = (.*?);\n", src, re.S)
    return json.loads(m.group(1))


def norm(word):
    return re.sub(r"[.,!?]", "", word.lower().replace("İ", "i").replace("I", "ı"))


def sha(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def decode(path, filters=None):
    cmd = ["ffmpeg", "-loglevel", "error", "-i", str(path)]
    if filters:
        cmd += ["-af", filters]
    cmd += ["-f", "f32le", "-ac", "1", "-ar", str(SR), "-"]
    return np.frombuffer(subprocess.run(cmd, check=True, capture_output=True).stdout, dtype=np.float32).copy()


def loudnorm(x, target):
    """Normalise a mono buffer to an integrated loudness target (LUFS) with ffmpeg."""
    raw = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "-",
         "-af", f"loudnorm=I={target}:TP=-1.5:LRA=11", "-ar", str(SR), "-f", "f32le", "-"],
        input=x.astype(np.float32).tobytes(), check=True, capture_output=True).stdout
    y = np.frombuffer(raw, dtype=np.float32).copy()
    return y[: len(x)] if len(y) >= len(x) else np.pad(y, (0, len(x) - len(y)))


def tts(text, v):
    h = sha("tts", text, v)
    mp3, js = CACHE / f"line_{h}.mp3", CACHE / f"line_{h}.json"
    if not mp3.exists():
        body = {"text": text, "model_id": v["model"], "voice_settings": {
            "stability": v["stability"], "similarity_boost": v["similarity_boost"], "style": v["style"],
            "use_speaker_boost": True, "speed": v["speed"]}}
        r = requests.post(f"{API}/text-to-speech/{v['voice_id']}/with-timestamps?output_format=mp3_44100_128",
                          headers=HDR, json=body, timeout=120)
        r.raise_for_status()
        d = r.json()
        mp3.write_bytes(base64.b64decode(d["audio_base64"]))
        js.write_text(json.dumps(d.get("alignment") or d.get("normalized_alignment"), ensure_ascii=False))
        print(f"  ElevenLabs: {len(text)} karakter, ses {v['name']}")
    return mp3, json.loads(js.read_text())


def words_from_alignment(text, al):
    words = text.split(" ")
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
    if len(spans) != len(words):  # fall back to proportional timing
        total = en[-1] if en else 1.0
        n = sum(len(w) + 1 for w in words)
        acc, spans = 0.0, []
        for w in words:
            d = total * (len(w) + 1) / n
            spans.append([acc, acc + d])
            acc += d
    return [{"w": w, "t0": s[0], "t1": s[1]} for w, s in zip(words, spans)]


def sfx(name):
    path = CACHE / f"sfx_{name}_{sha(SFX[name])}.mp3"
    if not path.exists():
        r = requests.post(f"{API}/sound-generation", headers=HDR, timeout=120,
                          json={"text": SFX[name], "duration_seconds": 1.2 if name == "pop" else 1.8, "prompt_influence": 0.6})
        r.raise_for_status()
        path.write_bytes(r.content)
        print(f"  ElevenLabs SFX: {name}")
    return path


def music(seconds):
    ms = int(min(max(seconds + 2, 15), 120) * 1000)
    path = CACHE / f"music_{sha(MUSIC_PROMPT, ms)}.mp3"
    if not path.exists():
        r = requests.post(f"{API}/music", headers=HDR, timeout=600,
                          json={"prompt": MUSIC_PROMPT, "music_length_ms": ms})
        r.raise_for_status()
        path.write_bytes(r.content)
        print(f"  ElevenLabs müzik: {ms / 1000:.0f} sn")
    return path


def main():
    global OUT
    OUT = Path(sys.argv[1] if len(sys.argv) > 1 else HERE / "out")
    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(exist_ok=True)
    src = (HERE / "script.js").read_text(encoding="utf-8")
    script, cues, voices = js_value(src, "SCRIPT"), js_value(src, "CUES"), js_value(src, "VOICES")

    timings, clips, t = [], [], 0.35
    for i, line in enumerate(script):
        v = voices[line["who"]]
        mp3, al = tts(line["text"], v)
        p = v.get("pitch", 1.0)
        # Bit: pitched up, same length (timestamps stay valid), a touch of metallic chorus
        filt = (f"asetrate={SR}*{p},aresample={SR},atempo={1 / p:.5f},chorus=0.6:0.8:18:0.25:0.3:2"
                if p != 1.0 else None)
        audio = decode(mp3, filt)
        words = words_from_alignment(line["text"], al)
        dur = max(len(audio) / SR, words[-1]["t1"])
        timings.append({"t0": t, "t1": t + words[-1]["t1"],
                        "words": [{"w": w["w"], "t0": t + w["t0"], "t1": t + w["t1"]} for w in words]})
        clips.append((t, audio))
        nxt = script[i + 1]["who"] if i + 1 < len(script) else None
        t += dur + (0.38 if nxt and nxt != line["who"] else 0.2)

    total = timings[-1]["t1"] + 1.6
    n = int(total * SR) + SR
    voice = np.zeros(n, np.float32)
    for start, a in clips:
        s = int(start * SR)
        voice[s:s + len(a)] += a[: n - s]
    voice = loudnorm(voice, -16)

    fx = np.zeros(n, np.float32)
    for c in cues:
        L = timings[c["line"]]
        if "word" in c:
            w = next((w for w in L["words"] if norm(w["w"]).startswith(norm(c["word"]))), None)
            at = w["t0"] if w else L["t0"]
        else:
            at = L["t0"] - 0.15 + c["scene"]
        a = decode(sfx(c["sfx"]))
        a = a / (np.max(np.abs(a)) + 1e-6) * (0.32 if c["sfx"] == "pop" else 0.45)
        s = int(at * SR)
        fx[s:s + len(a)] += a[: n - s]

    m = decode(music(total))
    if len(m) < n:
        m = np.tile(m, int(np.ceil(n / len(m))))
    m = loudnorm(m[:n], -30)
    fade = int(1.5 * SR)
    m[-fade:] *= np.linspace(1, 0, fade)
    m[:int(0.5 * SR)] *= np.linspace(0, 1, int(0.5 * SR))

    mix = voice + fx + m
    mix = np.clip(mix / max(1.0, np.max(np.abs(mix)) / 0.97), -1, 1)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "-",
                    "-af", "loudnorm=I=-14:TP=-1.0:LRA=11,alimiter=limit=0.95", "-c:a", "libmp3lame", "-b:a", "192k", str(OUT / "mix.mp3")],
                   input=mix.astype(np.float32).tobytes(), check=True)
    (OUT / "timings.json").write_text(json.dumps(timings, ensure_ascii=False, indent=1))
    print(f"Ses hazır: {total:.1f} sn, {sum(len(l['text']) for l in script)} karakter anlatım")

    # voices on the account, to pick better narrators later
    r = requests.get(f"{API}/voices", headers=HDR, timeout=60)
    if r.ok:
        vs = [{"name": v["name"], "voice_id": v["voice_id"], "category": v.get("category"),
               "labels": v.get("labels"), "description": v.get("description")} for v in r.json().get("voices", [])]
        (OUT / "voices.json").write_text(json.dumps(vs, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
