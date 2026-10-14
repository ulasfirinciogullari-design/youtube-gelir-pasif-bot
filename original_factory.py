"""
ORIGINAL LOW-COST SHORTS FACTORY (Zirvenin Kanunu).

One run = one original Short, end to end:
topic queue -> LLM script -> free edge-tts narration -> Pexels/Pixabay stock / Flux images
-> kinetic subtitles + sound design -> 1080x1920 60 FPS render -> YouTube upload.

Three series: curiosity facts over real stock footage (mk-NNN, the style of
the reference Shorts Ulaş picked), history stories (zk-NNN) and film/series
character commentary (fk-NNN). None uses film footage; character videos are our
own narration over faceless generated images. Curiosity is the default (Ulaş's
pick, 2026-10-13); "karisik" rotates the three.
Typical paid spend is a few Flux images (~$0.003 each) plus one short LLM call;
curiosity videos use free stock footage only. Rendering runs on free GitHub Actions.

The channel itself is the queue state: every upload carries "Seri: <topic id>"
in its description, and the next run picks the first topic not seen there.
"""

import argparse
import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.services.channel_metrics import list_series_videos, report_channel
from app.services.channel_memory import load_uploads, record_upload, used_topic_ids
from app.services.competitor_scan import dna_age_days, load_dna, run_scan
from app.services.seo_pack import build_seo_pack
from app.services.original_script import write_script
from app.services.original_visuals import build_visual_track
from app.services.video_critic import critique, format_critique

OUTPUT_DIR = BASE_DIR / "output"
TOPICS_FILE = BASE_DIR / "app" / "services" / "original_topics_tr.json"
CHARACTER_TOPICS_FILE = BASE_DIR / "app" / "services" / "character_topics_tr.json"
MERAK_TOPICS_FILE = BASE_DIR / "app" / "services" / "merak_topics_tr.json"
# The workflow checks out the channel-data branch here: metrics, competitor DNA, generated topics, history.
DATA_DIR = BASE_DIR / "channel-data"
GENERATED_TOPICS_FILE = "generated_topics.json"
DNA_REFRESH_DAYS = 7  # one competitor scan (~650 API units) a week
MUSIC_DIR = BASE_DIR / "assets" / "music"
SFX_DIR = BASE_DIR / "assets" / "sfx"
# Music and sound effects generated once with ElevenLabs (scripts/build_audio_library.py) live on the
# channel-data branch. The old assets/sfx files are sine-wave synths and are no longer mixed in.
AUDIO_LIBRARY = DATA_DIR / "audio"

VOICE = {"name": "tr-TR-AhmetNeural", "rate": "+6%", "pitch": "-2Hz"}
MARKER_PREFIX = "Seri: "
MARKER_RE = re.compile(r"Seri: ([a-z]{2}-\d{3,})")
BASE_TAGS = ["shorts", "psikoloji", "tarih", "güç", "karar verme", "zirvenin kanunu"]
BASE_HASHTAGS = "#shorts #psikoloji #tarih #keşfet"
# cut = longest shot in seconds. The reference Shorts cut every ~1.5 s; the AI-image series stay at 2.5 s
# because every extra shot is one more paid image.
SERIES = {
    "merak": {"topics": MERAK_TOPICS_FILE, "prefix": "mk", "format": "merak",
              "tags": ["shorts", "bilim", "merak", "bilgi", "ilginç bilgiler", "zirvenin kanunu"],
              "hashtags": "#shorts #bilim #merak #keşfet", "ai_only": False, "stock_only": True, "cut": 1.6,
              "tempo": 1.1,  # the reference explainers talk fast; Ulaş still found the sample "basit" at 1.0
              "style": "photorealistic documentary photograph, natural light, sharp detail, true to life colors"},
    "story": {"topics": TOPICS_FILE, "prefix": "zk", "format": "story",
              "tags": BASE_TAGS, "hashtags": BASE_HASHTAGS, "ai_only": False,
              "style": "cinematic historical drama film still, 35mm, dramatic chiaroscuro lighting, rich textures, "
                       "muted earth tones and deep shadows, photorealistic, highly detailed"},
    "karakter": {"topics": CHARACTER_TOPICS_FILE, "prefix": "fk", "format": "character",
                 "tags": ["shorts", "film", "dizi", "karakter analizi", "psikoloji", "zirvenin kanunu"],
                 "hashtags": "#shorts #film #dizi #psikoloji", "ai_only": True,
                 "style": "cinematic crime drama film still, anamorphic 35mm, moody low-key lighting, volumetric haze, "
                          "teal and amber color grade, photorealistic, highly detailed, faces turned away or in shadow"},
}
ROTATION = ["merak", "karakter", "story"]  # order "karisik" walks through
# Levels are set from measured loudness. The ElevenLabs narration comes out near -38 LUFS (peaks at -18 dBFS), so at
# the old fixed 9% the music was as loud as the narration and every effect far louder; Ulaş found the music "biraz
# yüksek" (2026-10-14). The narration is now brought to VOICE_LUFS, the music set under it, and the effect levels
# below are relative to that narration (loudest 400 ms of each effect against the narration's typical 400 ms).
VOICE_LUFS = -16.0
VOICE_COMPRESSOR = "acompressor=threshold=-16dB:ratio=3:attack=5:release=60"
MUSIC_UNDER_VOICE = 12  # LU; the reference Shorts keep music low and steady under the narrator
MUSIC_VOLUME = 0.09  # only when the loudness cannot be measured
VOICE_BAND_DIP = "equalizer=f=2500:t=o:w=2:g=-4"  # the music steps back where speech is clearest
MUSIC_INTRO_LU = 8  # a track is playing once its 3 s loudness is within this of its loud parts
# The curiosity script names a mood and gets that mood's track; the owl Short had drawn the bouncy marimba one.
MOOD_TRACKS = {"merak": {"belgesel": "merak_3", "eglenceli": "merak_2", "sakin": "merak_1"}}
# Sound effects stay accents: Ulaş found a whoosh on every scene change loud and tiring.
WHOOSH_VOLUME = 0.1  # about 14 LU under the narration
LINE_PAUSE_SECONDS = 0.15  # breath between sentences once each line's own leading/trailing silence is cut
END_PAUSE_SECONDS = 0.6  # the last sentence rings out under the music
IMPACT_VOLUME = 0.18  # the hit on the hook, just under the narration
RISER_VOLUME = 0.13  # about 6 LU under the narration, into the ending
POP_VOLUME = 0.5  # a soft pop under each on-screen key fact, about 8 LU under the narration
CALLOUT_DELAY = 0.25  # a key fact appears just after its sentence starts
CALLOUT_SECONDS = 1.6
CALLOUT_SIZE = 118
CALLOUT_LINE_CHARS = 14  # characters that fit one line at CALLOUT_SIZE; longer key facts get a smaller font
SKIPPED_TOPICS_FILE = "skipped_topics.json"  # curiosity topics free footage could not show
SUBTITLE_MARGIN_V = 620  # lower third, above the Shorts title/channel overlay
# The heavy rounded face of popular Shorts captions; the workflow installs fonts-montserrat. Arial Black was never on
# the runner, so the text fell back to a plain sans font, as it still would without Montserrat.
FONT = "Montserrat Black"
STORYBOARD_SECONDS = 1.0  # one frame per second: a key fact (1.6 s on screen) always shows up
voice_usage = {"elevenlabs": 0, "edge-tts": 0}


# ---------------------------------------------------------------------------
# Topic queue
# ---------------------------------------------------------------------------

def load_topics(path: Path = TOPICS_FILE) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def published_topic_ids(youtube, max_pages: int = 10) -> set[str]:
    """Reads 'Seri: <id>' markers from the channel's uploaded video descriptions."""
    return {v["topic_id"] for v in list_series_videos(youtube, MARKER_RE, max_pages)}


def pick_series(requested: str, data_dir: Path = DATA_DIR, topic_id: str | None = None) -> str:
    """The series asked for; karisik takes the one after the last upload's in ROTATION."""
    if topic_id:
        prefix = topic_id.split("-")[0]
        return next((name for name, cfg in SERIES.items() if cfg["prefix"] == prefix), "story")
    if requested in SERIES:
        return requested
    by_format = {SERIES[name]["format"]: name for name in ROTATION}
    last = [by_format[u["format"]] for u in load_uploads(data_dir) if u.get("format") in by_format]
    if not last:
        return ROTATION[0]
    return ROTATION[(ROTATION.index(last[-1]) + 1) % len(ROTATION)]


def film_title_examples(data_dir: Path = DATA_DIR, limit: int = 5) -> list[str]:
    """The old film-clip uploads were the channel's most watched; their titles guide the character series."""
    from app.services.channel_audit import is_film_clip
    try:
        videos = json.loads((data_dir / "channel_audit.json").read_text(encoding="utf-8")).get("videos", [])
    except (OSError, ValueError, AttributeError):
        return []
    titles = []
    for v in sorted(videos, key=lambda v: -int(v.get("views") or 0)):
        title = " ".join(re.sub(r"#\w+", "", str(v.get("title", ""))).replace("👑", "").split())
        if is_film_clip(title) and title not in titles:
            titles.append(title)
    return titles[:limit]


def pick_topic(topics: list[dict], published: set[str], topic_id: str | None = None) -> dict:
    if topic_id:
        match = next((t for t in topics if t["id"] == topic_id), None)
        if not match:
            raise SystemExit(f"Konu bulunamadı: {topic_id}")
        return match
    for topic in topics:
        if topic["id"] not in published:
            return topic
    raise SystemExit("Konu listesi bitti: app/services/original_topics_tr.json dosyasına yeni konular ekleyin.")


def load_generated_topics(data_dir: Path = DATA_DIR) -> list[dict]:
    path = data_dir / GENERATED_TOPICS_FILE
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return []


def next_topic_id(topics: list[dict], published: set[str], prefix: str = "zk") -> str:
    numbers = [int(i.split("-")[1]) for i in [t["id"] for t in topics] + list(published)
               if re.fullmatch(rf"{prefix}-\d+", i)]
    return f"{prefix}-{max(numbers, default=0) + 1:03d}"


INVENT_PROMPT = """"Zirvenin Kanunu" Türkçe Shorts kanalı için daha önce işlenmemiş TEK yeni konu öner.
Kanal tarihten gerçek insanların gerilimli anlarını, dahice hamlelerini ve psikoloji deneylerini anlatıyor.
Konu tek cümlelik merak uyandıran bir soru olsun, kaynaklarda iyi bilinen gerçek bir olaya dayansın.
Daha önce işlenenler (bunları ve benzerlerini tekrar etme):
{used}
Sadece şu JSON'u döndür: {{"topic": "..."}}"""


def invent_topic(used: list[str]) -> str:
    from app.services.original_script import _call_gemini, _extract_json
    raw = _call_gemini(INVENT_PROMPT.format(used="\n".join(f"- {u}" for u in used[-80:])))
    topic = str(_extract_json(raw).get("topic") or "").strip() if raw else ""
    if len(topic) < 15:
        raise SystemExit("Konu listesi bitti ve yeni konu üretilemedi (GEMINI_API_KEY gerekli).")
    return topic


INVENT_CHARACTER_PROMPT = """"Zirvenin Kanunu" Türkçe Shorts kanalının film/dizi karakteri serisi için TEK yeni konu öner.
Seri, Türkiye'de çok izlenen film ve dizilerdeki ünlü karakterlerin bir kararını, kuralını veya psikolojisini yorumluyor.
Konu tek cümlelik merak uyandıran bir soru olsun ve filmde/dizide gerçekten olan, iyi bilinen bir şeye dayansın.
Daha önce işlenenler (bunları tekrar etme):
{used}
Sadece şu JSON'u döndür: {{"topic": "...", "character": "karakterin adı", "work": "film veya dizinin Türkçe adı"}}"""


def invent_character_topic(used: list[str]) -> dict:
    from app.services.original_script import _call_gemini, _extract_json
    raw = _call_gemini(INVENT_CHARACTER_PROMPT.format(used="\n".join(f"- {u}" for u in used[-80:])))
    data = _extract_json(raw) if raw else {}
    topic, character = str(data.get("topic") or "").strip(), str(data.get("character") or "").strip()
    if len(topic) < 15 or not character:
        raise SystemExit("Karakter konuları bitti ve yeni konu üretilemedi (GEMINI_API_KEY gerekli).")
    return {"topic": topic, "character": character, "work": str(data.get("work") or "").strip(), "names": [character]}


INVENT_MERAK_PROMPT = """"Zirvenin Kanunu" Türkçe Shorts kanalının merak serisi için TEK yeni konu öner.
Seri bilim, hayvanlar, insan vücudu, günlük hayattaki nesneler ve insan psikolojisi hakkında herkesin merak ettiği
soruları gerçek stok videolarla anlatıyor. Konu tek cümlelik bir soru olsun, cevabı bilimsel olarak net bilinsin
ve kameraya çekilebilecek somut bir şeyle ilgili olsun (soyut kavram değil).
Daha önce işlenenler (bunları ve benzerlerini tekrar etme):
{used}
Sadece şu JSON'u döndür: {{"topic": "...", "stock_hint": "Pixabay'de bulunacak 2-3 basit İngilizce kelime",
"subject": ["videoda görünecek ana özne için 1-4 tekil İngilizce isim, ör. cat, kitten"]}}"""


def invent_merak_topic(used: list[str]) -> dict:
    from app.services.original_script import _call_gemini, _extract_json
    raw = _call_gemini(INVENT_MERAK_PROMPT.format(used="\n".join(f"- {u}" for u in used[-80:])))
    data = _extract_json(raw) if raw else {}
    topic = str(data.get("topic") or "").strip()
    if len(topic) < 15:
        raise SystemExit("Merak konuları bitti ve yeni konu üretilemedi (GEMINI_API_KEY gerekli).")
    hint = " ".join(str(data.get("stock_hint") or "").split())[:60]
    subject = [str(w).strip().lower() for w in data.get("subject") or [] if str(w).strip()][:4]
    return {"topic": topic, "stock_hint": hint, "subject": subject or hint.split()[:1]}


def topic_subject(topic: dict) -> list[str] | None:
    """English nouns every curiosity clip must be tagged with (the first stock hint word if none were given)."""
    return topic.get("subject") or (topic.get("stock_hint") or "").split()[:1] or None


def skipped_topic_ids(data_dir: Path = DATA_DIR) -> set[str]:
    try:
        return {t["id"] for t in json.loads((data_dir / SKIPPED_TOPICS_FILE).read_text(encoding="utf-8"))}
    except (OSError, ValueError, KeyError, TypeError):
        return set()


def skip_topic(data_dir: Path, topic: dict, coverage: int) -> None:
    path = data_dir / SKIPPED_TOPICS_FILE
    try:
        skipped = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        skipped = []
    skipped.append({"id": topic["id"], "topic": topic["topic"], "coverage": coverage,
                    "date": time.strftime("%Y-%m-%d")})
    data_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(skipped, ensure_ascii=False, indent=2), encoding="utf-8")


def merak_script(topic: dict, published: set[str], explicit: bool, dna: dict | None = None,
                 data_dir: Path = DATA_DIR, footage=None, fallback=None, max_skips: int = 2):
    """Footage-first script for a curiosity topic: (topic, script, provider, clip_pool).

    A topic the run picked itself is skipped (and remembered) when free footage covers it poorly;
    one asked for by id is made anyway. Without a footage script the script-first flow is used."""
    from app.services import footage_script

    footage = footage or footage_script.write_footage_script
    for _ in range(max_skips + 1):
        try:
            result = footage(topic, OUTPUT_DIR / f"previews_{topic['id']}")
        except Exception as e:  # never lose the run over the upgrade
            print(f"ℹ️ Görüntüye göre senaryo yazılamadı: {str(e)[:200]}")
            result = None
        if result is None:
            break
        coverage = result["coverage"]
        print(f"🎞️ Görüntü kapsamı: {coverage}/10")
        if coverage >= footage_script.MIN_COVERAGE or explicit:
            return topic, result["script"], "gemini (görüntüye göre)", result["pool"]
        print(f"⏭️ {topic['id']} atlandı: stok görüntü konuyu yeterince göstermiyor.")
        skip_topic(data_dir, topic, coverage)
        published = published | {topic["id"]}
        topic = choose_topic(published, None, dna, data_dir, series="merak")
    character = {**topic, "stock_hint": topic.get("stock_hint", ""), "subject": topic_subject(topic)}
    script, provider = (fallback or write_script)(topic["topic"], style_examples=[], rules=None, character=character)
    return topic, script, provider, None


INVENTORS = {"story": "invent_topic", "karakter": "invent_character_topic", "merak": "invent_merak_topic"}


def choose_topic(published: set[str], topic_id: str | None = None, dna: dict | None = None,
                 data_dir: Path = DATA_DIR, invent=None, series: str = "story") -> dict:
    """Next unused topic of the series; when its list runs out, a competitor gap idea (history only) or a Gemini topic."""
    cfg = SERIES[series]
    generated = load_generated_topics(data_dir)
    topics = load_topics(cfg["topics"]) + [t for t in generated if t["id"].startswith(cfg["prefix"] + "-")]
    try:
        return pick_topic(topics, published, topic_id)
    except SystemExit:
        if topic_id:
            raise
    used = [t["topic"] for t in topics]
    ideas = (dna or {}).get("topic_ideas", []) if series == "story" else []
    idea = next((i for i in ideas if i not in used), None)
    invent = invent or globals()[INVENTORS[series]]
    fresh = idea or invent(used)
    topic = {"id": next_topic_id(topics, published, cfg["prefix"]),
             **(fresh if isinstance(fresh, dict) else {"topic": fresh}),
             "source": "rakip boşluğu" if idea else "gemini"}
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / GENERATED_TOPICS_FILE).write_text(
        json.dumps(generated + [topic], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"🆕 Konu listesi bitti, yeni konu üretildi ({topic['source']}).")
    return topic


# ---------------------------------------------------------------------------
# Narration
# ---------------------------------------------------------------------------

def _probe_duration(path: Path) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        text=True,
    ).strip()
    return float(out)


async def _edge_tts(text: str, out: Path, voice: dict | None = None):
    import edge_tts
    voice = voice or VOICE
    comm = edge_tts.Communicate(text, voice["name"], rate=voice["rate"], pitch=voice["pitch"])
    await comm.save(str(out))


def synthesize_line(text: str, out: Path, previous_text: str = "", next_text: str = "", attempts: int = 3,
                    voice_id: str | None = None, edge_voice: dict | None = None) -> Path:
    """ElevenLabs (default: Turkish baritone "Alp") when ELEVENLABS_API_KEY is set, otherwise or on failure edge-tts."""
    try:
        from app.services.elevenlabs_engine import (
            DEFAULT_TURKISH_VOICE_ID, get_elevenlabs_api_key, synthesize_elevenlabs_speech,
        )
        if get_elevenlabs_api_key() and synthesize_elevenlabs_speech(
            text, out, voice_id=voice_id or DEFAULT_TURKISH_VOICE_ID, previous_text=previous_text, next_text=next_text
        ) and out.exists() and out.stat().st_size > 1000:
            voice_usage["elevenlabs"] += 1
            return out
    except Exception as e:
        print(f"ℹ️ ElevenLabs notu: {e}")
    try:
        from app.services.turkish_phonetics import normalize_turkish_tts_text
        text = normalize_turkish_tts_text(text)
    except Exception:
        pass
    last_error = None
    for attempt in range(attempts):
        try:
            asyncio.run(_edge_tts(text, out, edge_voice))
            if out.exists() and out.stat().st_size > 1000:
                voice_usage["edge-tts"] += 1
                return out
        except Exception as e:
            last_error = e
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"seslendirme başarısız: {last_error}")


def tighten_line(src: Path, out: Path, pause: float, tempo: float = 1.0) -> Path:
    """The line as 44.1 kHz mono WAV without the silence the TTS engine leaves before and after it, sped up by
    `tempo` (pitch kept) and followed by a fixed `pause`. Each sentence is voiced separately, and those silences
    made the narration stop and restart at every scene change (Ulaş, 2026-10-14: "geçişler arasında kopma").
    If trimming fails the line is kept whole."""
    trim = "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.03"
    speed = f",atempo={tempo:.3f}" if abs(tempo - 1.0) > 1e-3 else ""
    for chain in (f"{trim},areverse,{trim},areverse{speed},apad=pad_dur={pause:.2f}", f"anull{speed}"):
        try:
            subprocess.run(["ffmpeg", "-y", "-i", str(src), "-af", chain, "-ar", "44100", "-ac", "1",
                            "-c:a", "pcm_s16le", str(out)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if chain == "anull" or _probe_duration(out) >= pause + 0.3:
                return out
        except (subprocess.CalledProcessError, ValueError):
            continue
    raise RuntimeError(f"seslendirme dosyası okunamadı: {src.name}")


def build_narration(scenes: list[dict], work_dir: Path, synthesize=synthesize_line,
                    tempo: float = 1.0) -> tuple[Path, list[float], list[dict]]:
    """Returns (voice_track, per_scene_durations, subtitle_events)."""
    from app.services.multi_voice_engine import build_micro_timed_subtitles

    voice_dir = work_dir / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    files, durations, subtitles, timeline = [], [], [], 0.0
    for idx, scene in enumerate(scenes):
        raw = synthesize(
            scene["text"], voice_dir / f"line_{idx:02d}.mp3",
            previous_text=scenes[idx - 1]["text"] if idx else "",
            next_text=scenes[idx + 1]["text"] if idx + 1 < len(scenes) else "",
        )
        last = idx + 1 == len(scenes)
        seg = tighten_line(raw, voice_dir / f"line_{idx:02d}.wav", END_PAUSE_SECONDS if last else LINE_PAUSE_SECONDS,
                           tempo)
        dur = _probe_duration(seg)
        subtitles += build_micro_timed_subtitles(scene["text"], timeline, dur, speaker="male_alpha")
        files.append(seg)
        durations.append(dur)
        timeline += dur

    concat_list = voice_dir / "concat.txt"
    concat_list.write_text("".join(f"file '{f.resolve()}'\n" for f in files), encoding="utf-8")
    voice = work_dir / "voice.m4a"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c:a", "aac", "-b:a", "192k", str(voice)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return voice, durations, subtitles


# ---------------------------------------------------------------------------
# Sound design and render
# ---------------------------------------------------------------------------

def pick_music(seed: str, series: str = "story", library: Path | None = None, mood: str = "") -> Path | None:
    """The track of the script's mood; else one picked by topic from the series' tracks, any library track or the
    old assets."""
    music = (library or AUDIO_LIBRARY) / "music"
    fitting = MOOD_TRACKS.get(series, {}).get(mood)
    if fitting and (music / f"{fitting}.mp3").exists():
        return music / f"{fitting}.mp3"
    tracks = sorted(music.glob(f"{series}_*.mp3")) or sorted(music.glob("*.mp3")) or sorted(MUSIC_DIR.glob("*.mp3"))
    if not tracks:
        return None
    return tracks[int(hashlib.md5(seed.encode("utf-8")).hexdigest(), 16) % len(tracks)]


def loudness(path: Path, seconds: float | None = None, chain: str = "", start: float = 0.0) -> float | None:
    """Integrated loudness in LUFS of `seconds` from `start` after `chain` (ffmpeg ebur128); None for silence or
    errors."""
    window = (["-ss", f"{start:.2f}"] if start else []) + (["-t", f"{seconds:.3f}"] if seconds else [])
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", *window, "-i", str(path), "-af",
             ",".join(filter(None, [chain, "ebur128"])), "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    found = re.findall(r"I:\s+(-?\d+(?:\.\d+)?) LUFS", getattr(result, "stderr", None) or "")
    return float(found[-1]) if found and float(found[-1]) > -70 else None


def music_start(music: Path, duration: float) -> float:
    """Where in the track the Short starts: past a quiet intro (merak_3 opens near -34 LUFS and takes about 8 s to
    arrive, which left the hook without music), yet early enough that the track never runs out mid-Short."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-v", "verbose", "-i", str(music), "-af", "ebur128=framelog=verbose",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
        frames = [(float(t), float(s)) for t, s in
                  re.findall(r"t:\s*(\d+(?:\.\d+)?)\s.*?S:\s*(-?\d+(?:\.\d+)?)", result.stderr) if float(t) >= 3.0]
        latest = _probe_duration(music) - duration - 1.0
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError, TypeError):
        return 0.0
    if not frames or latest <= 0:
        return 0.0
    loud = sorted(level for _, level in frames)[int(len(frames) * 0.9)]
    first = next(t for t, level in frames if level >= loud - MUSIC_INTRO_LU)  # end of the first 3 s window that plays
    return round(min(latest, 0.0 if first == frames[0][0] else first - 1.5), 1)


def voice_chain(lift: float, makeup: float = 1.1) -> str:
    """Narration: lifted into the compressor's range, compressed, then made up to its final level."""
    return f"highpass=f=70,volume={lift},{VOICE_COMPRESSOR},volume={makeup}"


def _gain(target: float, measured: float, low: float, high: float, digits: int = 3) -> float:
    return round(min(high, max(low, 10 ** ((target - measured) / 20))), digits)


def mix_levels(voice: Path, music: Path | None, duration: float, start: float = 0.0) -> tuple[str, float]:
    """(narration filter chain, music gain): the narration at VOICE_LUFS after compression, and the part of the
    track that plays (from `start`) MUSIC_UNDER_VOICE LU under it. What cannot be measured keeps the old fixed
    levels."""
    spoken = loudness(voice)
    if spoken is None:
        return voice_chain(1.0), MUSIC_VOLUME
    lift = _gain(VOICE_LUFS, spoken, 0.1, 30.0)
    compressed = loudness(voice, chain=voice_chain(lift, 1.0))
    chain = voice_chain(lift, 1.0 if compressed is None else _gain(VOICE_LUFS, compressed, 0.25, 4.0))
    bed = loudness(music, duration, start=start) if music and music.exists() and compressed is not None else None
    if bed is None:
        return chain, MUSIC_VOLUME
    level = _gain(VOICE_LUFS - MUSIC_UNDER_VOICE, bed, 0.001, 0.5, 4)
    print(f"       Ses seviyesi: anlatım {spoken:.1f} LUFS ölçüldü, {VOICE_LUFS:.0f} LUFS'a getirildi; müzik "
          f"{VOICE_LUFS - bed - 20 * math.log10(level):.1f} LU altında (eski ayarda "
          f"{spoken - bed - 20 * math.log10(MUSIC_VOLUME):.1f} LU altındaydı)")
    return chain, level


def sfx(name: str, library: Path | None = None) -> Path | None:
    path = (library or AUDIO_LIBRARY) / "sfx" / f"{name}.mp3"
    return path if path.exists() else None


def mix_soundtrack(voice: Path, out: Path, duration: float, scene_cuts: list[float], music: Path | None,
                   accents: list[float] | None = None) -> Path:
    """Voice + music well under it + a soft hit on the hook, a quiet whoosh only after the hook and into the last
    scene, a soft pop under each on-screen key fact (`accents`) and a riser into the ending."""
    start = music_start(music, duration) if music and music.exists() else 0.0
    narration, level = mix_levels(voice, music, duration, start)
    inputs = ["-i", str(voice)]
    filters = [f"[0:a]{narration}[v]"]
    labels = ["[v]"]

    def add(path: Path, chain: str, label: str, loop: bool = False):
        if loop:
            inputs.extend(["-stream_loop", "-1"])
        inputs.extend(["-i", str(path)])
        n = sum(1 for a in inputs if a == "-i") - 1
        filters.append(f"[{n}:a]{chain}[{label}]")
        labels.append(f"[{label}]")

    if music and music.exists():
        fade = max(0.5, duration - 1.5)
        skip = f"atrim=start={start},asetpts=PTS-STARTPTS," if start else ""
        add(music, f"{skip}volume={level},{VOICE_BAND_DIP},afade=t=in:d=0.4,afade=t=out:st={fade:.2f}:d=1.5", "m",
            loop=True)
        if start:
            print(f"       Müzik {start:.1f}. saniyesinden başlıyor (sessiz giriş atlandı)")
    impact, whoosh, riser = sfx("impact"), sfx("whoosh"), sfx("riser")
    if impact:
        add(impact, f"volume={IMPACT_VOLUME}", "imp")
    if whoosh:
        for i, t in enumerate(sorted({scene_cuts[0], scene_cuts[-1]}) if scene_cuts else []):
            ms = max(0, int((t - 0.2) * 1000))
            add(whoosh, f"volume={WHOOSH_VOLUME},adelay={ms}|{ms}", f"w{i}")
    pop = sfx("pop")
    for i, t in enumerate((accents or []) if pop else []):
        ms = max(0, int(t * 1000))
        add(pop, f"volume={POP_VOLUME},adelay={ms}|{ms}", f"p{i}")
    if riser and duration > 8:
        ms = int((duration - 2.5) * 1000)
        add(riser, f"volume={RISER_VOLUME},adelay={ms}|{ms}", "ris")

    mix = f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0,loudnorm=I=-14:TP=-1.0:LRA=9[aout]"
    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(filters + [mix]),
        "-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{duration:.3f}", str(out),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out


def add_hook_overlay(ass_path: Path, hook_text: str, seconds: float) -> None:
    """The hook question stays at the top of the frame for the whole video, like a title banner:
    every reference Short does this, so a viewer who joins mid-video still knows the question."""
    from app.services.subtitle_engine import _format_ass_time, _upper_tr

    text = re.sub(r"[{}\\]", "", hook_text or "").strip()
    if not text:
        return
    content = ass_path.read_text(encoding="utf-8")
    style = (
        f"Style: HookStyle,{FONT},78,&H00FFFFFF,&H000000FF,&H00000000,&H90000000,"
        "-1,0,0,0,100,100,1,0,1,8,3,8,60,60,250,1\n"
    )
    content = content.replace("\n[Events]", f"{style}\n[Events]", 1)
    pop = r"{\fad(0,250)\fscx125\fscy125\t(0,180,\fscx100\fscy100)}"
    content += f"Dialogue: 1,0:00:00.00,{_format_ass_time(seconds)},HookStyle,,0,0,0,,{pop}{_upper_tr(text)}\n"
    ass_path.write_text(content, encoding="utf-8")


def add_callouts(ass_path: Path, scenes: list[dict], durations: list[float]) -> list[float]:
    """Each scene's key fact ("270 DERECE") pops up big in the middle of the frame for a moment, like the numbers
    and keywords the reference explainers put on screen. Returns when each appears, for the pop sound."""
    from app.services.subtitle_engine import _format_ass_time, _upper_tr

    events, times, start = [], [], 0.0
    pop = r"\pos(540,860)\frz-3\fad(60,160)\fscx30\fscy30\t(0,140,\fscx112\fscy112)\t(140,240,\fscx100\fscy100)"
    for scene, dur in zip(scenes, durations):
        text = re.sub(r"[{}\\]", "", scene.get("callout") or "").strip()
        if text and dur > 1.0:
            begin = start + min(CALLOUT_DELAY, dur / 4)
            end = min(start + dur - 0.1, begin + CALLOUT_SECONDS)
            size = CALLOUT_SIZE if len(text) <= CALLOUT_LINE_CHARS else max(
                64, round(CALLOUT_SIZE * CALLOUT_LINE_CHARS / len(text)))
            events.append(f"Dialogue: 2,{_format_ass_time(begin)},{_format_ass_time(end)},CalloutStyle,,0,0,0,,"
                          f"{{\\fs{size}{pop}}}{_upper_tr(text)}\n")
            times.append(round(begin, 3))
        start += dur
    if events:
        style = (f"Style: CalloutStyle,{FONT},{CALLOUT_SIZE},&H0000D7FF,&H000000FF,&H00000000,&H90000000,"
                 "-1,0,0,0,100,100,1,0,1,10,4,5,60,60,0,1\n")
        content = ass_path.read_text(encoding="utf-8").replace("\n[Events]", f"{style}\n[Events]", 1)
        ass_path.write_text(content + "".join(events), encoding="utf-8")
    return times


def storyboard(video: Path, out: Path, duration: float, every: float = STORYBOARD_SECONDS) -> Path | None:
    """One small frame per `every` seconds, tiled eight to a row into a JPEG that goes to the channel history.
    The cloud sessions that build these Shorts can open neither YouTube nor the run's artifacts; this is how
    they see a finished video."""
    cols = 8
    rows = max(1, math.ceil(math.ceil(duration / every) / cols))
    try:
        subprocess.run(["ffmpeg", "-y", "-i", str(video), "-vf", f"fps=1/{every},scale=160:284,tile={cols}x{rows}",
                        "-frames:v", "1", "-q:v", "5", str(out)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"ℹ️ Kare özeti çıkarılamadı: {e}")
        return None
    return out


def render_final(segments: list[Path], audio: Path, subtitles_file: Path, duration: float, work_dir: Path,
                 final_mp4: Path, slots: list[float] | None = None) -> Path:
    """With `slots` (each segment's place on the timeline) the shots dissolve into each other;
    without them they are joined with hard cuts."""
    from app.services.original_visuals import crossfade_graph
    from app.services.visual_fx_engine import generate_neon_progress_bar_filter

    overlay = f"subtitles={subtitles_file.name},{generate_neon_progress_bar_filter(duration)}"
    if slots and len(slots) == len(segments) > 1:
        inputs = [arg for s in segments for arg in ("-i", str(s.resolve()))]
        graph, label = crossfade_graph(slots)
        audio_index = len(segments)
    else:
        concat_list = work_dir / "segments.txt"
        concat_list.write_text("".join(f"file '{s.resolve()}'\n" for s in segments), encoding="utf-8")
        raw = work_dir / "visual_raw.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(raw)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        inputs, graph, label, audio_index = ["-i", str(raw.resolve())], "", "[0:v]", 1
    graph = f"{graph};{label}{overlay}[vout]" if graph else f"{label}{overlay}[vout]"
    subprocess.run(
        [
            "ffmpeg", "-y", *inputs, "-i", str(audio.resolve()), "-filter_complex", graph,
            "-map", "[vout]", "-map", f"{audio_index}:a:0",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-r", "60", "-fps_mode", "cfr",
            "-pix_fmt", "yuv420p", "-c:a", "copy", "-t", f"{duration:.3f}", "-movflags", "+faststart",
            str(final_mp4.resolve()),
        ],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(subtitles_file.parent),
    )
    return final_mp4


# ---------------------------------------------------------------------------
# Production
# ---------------------------------------------------------------------------

def produce(topic: dict, script: dict, provider: str, synthesize=synthesize_line, series: str = "story",
            clip_pool: list[dict] | None = None) -> dict:
    from app.services.subtitle_engine import generate_shorts_ass_subtitles

    stamp = int(time.time())
    work_dir = OUTPUT_DIR / f"work_original_{topic['id']}_{stamp}"
    work_dir.mkdir(parents=True, exist_ok=True)
    scenes = script["scenes"]

    cfg = SERIES[series]
    print(f"[1/4] 🎙️ Seslendirme: {len(scenes)} sahne")
    voice, durations, subtitle_events = build_narration(scenes, work_dir, synthesize=synthesize,
                                                        tempo=cfg.get("tempo", 1.0))
    duration = sum(durations)
    engines = ", ".join(f"{name} x{n}" for name, n in voice_usage.items() if n) or "test"
    print(f"       Ses motoru: {engines}")
    scene_cuts, t = [], 0.0
    for d in durations[:-1]:
        t += d
        scene_cuts.append(round(t, 3))

    print(f"[2/4] 🎞️ Görseller ({duration:.1f}s)")
    visuals = build_visual_track(scenes, durations, work_dir, cfg["style"], cfg["ai_only"],
                                 stock_only=cfg.get("stock_only", False), segment_seconds=cfg.get("cut", 2.5),
                                 fallback_query=topic.get("stock_hint", ""),
                                 subject=topic_subject(topic) if cfg.get("stock_only") else None,
                                 topic=topic["topic"], clip_pool=clip_pool)

    print("[3/4] 🎵 Ses tasarımı ve altyazı")
    subs = work_dir / "subtitles.ass"
    generate_shorts_ass_subtitles(
        sentences=subtitle_events, output_path=subs, font_name=FONT, font_size=74,
        highlight_color="&H0000D7FF", outline_width=8, margin_v=SUBTITLE_MARGIN_V,
    )
    add_hook_overlay(subs, script.get("hook_text", ""), duration)
    accents = add_callouts(subs, scenes, durations)
    mood = script.get("mood", "")
    music = pick_music(topic["id"], series, mood=mood)
    print(f"       Müzik: {(music or Path('-')).name}{f' ({mood})' if mood else ''}")
    audio = mix_soundtrack(voice, work_dir / "soundtrack.m4a", duration, scene_cuts, music, accents)
    if os.environ.get("KEEP_AUDIO"):
        # No-upload samples keep the narration and the mix next to the storyboard, so a session that cannot open
        # YouTube or the run's files can still measure what the Short sounds like.
        for source, part in ((voice, "voice"), (audio, "mix")):
            subprocess.run(["ffmpeg", "-y", "-i", str(source), "-ac", "1", "-c:a", "libmp3lame", "-b:a", "64k",
                            str(OUTPUT_DIR / f"ORIGINAL_{topic['id']}_{stamp}_{part}.mp3")],
                           check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print("[4/4] 🚀 Render")
    final_mp4 = OUTPUT_DIR / f"ORIGINAL_{topic['id']}_{stamp}.mp4"
    render_final(visuals["segments"], audio, subs, duration, work_dir, final_mp4, visuals.get("durations"))

    thumb = final_mp4.with_name(f"{final_mp4.stem}_thumb.jpg")
    if visuals["thumbnail"]:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(visuals["thumbnail"]), "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920", "-q:v", "2", str(thumb)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    else:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", "0.8", "-i", str(final_mp4), "-vframes", "1", "-q:v", "2", str(thumb)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    storyboard(final_mp4, final_mp4.with_name(f"{final_mp4.stem}_story.jpg"), duration)

    title = script["title"]
    full_title = f"{title} #shorts" if len(title) <= 92 else title[:100]
    seo = build_seo_pack(
        title, topic["topic"], " ".join(s["text"] for s in script["scenes"]), script,
        SERIES[series]["tags"], SERIES[series]["hashtags"], f"{MARKER_PREFIX}{topic['id']}",
    )
    print(f"       SEO: {seo['seo_engine']} | {len(seo['tags'])} etiket | {len(seo['localizations'])} dil")
    meta = {
        "topic_id": topic["id"],
        "topic": topic["topic"],
        "series": series,
        "script_provider": provider,
        "voice_engine": engines,
        "title": full_title,
        "hook_text": script.get("hook_text", ""),
        "description": seo["description"],
        "tags": seo["tags"],
        "localizations": seo["localizations"],
        "seo_engine": seo["seo_engine"],
        "pinned_comment": script["pinned_comment"],
        "scenes": scenes,
        "duration_seconds": round(duration, 2),
        "image_model": os.environ.get("IMAGE_MODEL") or "fal-ai/flux/schnell",
        "music": (music or Path("-")).name,
        "music_mood": mood,
        "ai_images": visuals["ai_images"],
        "stock_videos": visuals["stock_videos"],
        "clip_match": visuals.get("clip_match"),
        "contains_synthetic_media": visuals["ai_images"] > 0,
    }
    meta_path = final_mp4.with_name(f"{final_mp4.stem}_meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        from app.services.video_quality_analyst import audit_short_quality, print_audit_report
        print_audit_report(audit_short_quality(final_mp4))
    except Exception as e:
        print(f"ℹ️ Kalite raporu notu: {e}")

    if series == "merak" and os.environ.get("GEMINI_API_KEY"):
        # A Gemini editor compares the finished Short with a reference Short; it never blocks the upload.
        verdict = critique(final_mp4, work_dir)
        if verdict:
            print(format_critique(verdict))
            meta["critique"] = verdict
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ Hazır: {final_mp4.name} | {duration:.1f}s | AI görsel: {visuals['ai_images']} | stok video: {visuals['stock_videos']}")
    return {"video": final_mp4, "thumbnail": thumb, "meta": meta}


def upload(youtube, result: dict, privacy: str) -> str:
    from googleapiclient.http import MediaFileUpload

    meta = result["meta"]
    body = {
        "snippet": {
            "title": meta["title"][:100],
            "description": meta["description"][:5000],
            "tags": meta["tags"],
            "categoryId": meta.get("category_id", "27"),
            "defaultLanguage": "tr",
            "defaultAudioLanguage": "tr",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": bool(meta["contains_synthetic_media"]),
        },
    }
    parts = "snippet,status"
    if meta.get("localizations"):
        body["localizations"] = meta["localizations"]
        parts += ",localizations"

    media = MediaFileUpload(str(result["video"]), chunksize=4 * 1024 * 1024, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part=parts, body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"   Yükleniyor: %{int(status.progress() * 100)}")
    video_id = response["id"]
    print(f"🎉 Yüklendi ({privacy}): https://youtube.com/shorts/{video_id}")

    try:
        youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(result["thumbnail"]), mimetype="image/jpeg")).execute()
    except Exception as e:
        print(f"ℹ️ Kapak görseli notu: {e}")
    if meta.get("pinned_comment") and privacy == "private":
        # Commenting on a private video returns 403 and still spends 50 quota units.
        print("ℹ️ Video gizli, yorum atlandı")
    elif meta.get("pinned_comment"):
        try:
            youtube.commentThreads().insert(part="snippet", body={"snippet": {
                "videoId": video_id,
                "topLevelComment": {"snippet": {"textOriginal": meta["pinned_comment"]}},
            }}).execute()
        except Exception as e:
            print(f"ℹ️ Yorum notu: {e}")
    return video_id


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Original low-cost Shorts factory")
    parser.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    parser.add_argument("--topic-id", default=None, help="Belirli bir konu (ör. zk-003 veya fk-002)")
    parser.add_argument("--series", default="merak", choices=[*SERIES, "karisik"],
                        help="merak = gerçek görüntülü bilgi, story = tarih hikâyesi, karakter = film/dizi karakteri, "
                             "karisik = sırayla üçü")
    parser.add_argument("--no-upload", action="store_true", help="Sadece üret, YouTube'a yükleme")
    args = parser.parse_args(argv)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    youtube = None
    published: set[str] = set()
    if not args.no_upload:
        from publish_to_youtube import get_authenticated_service
        youtube = get_authenticated_service()
        published, examples = report_channel(youtube, MARKER_RE, OUTPUT_DIR)
        published |= used_topic_ids(DATA_DIR) | skipped_topic_ids(DATA_DIR)
        print(f"📋 Kanalda yayınlanmış konu sayısı: {len(published)}")
    else:
        examples = []

    dna = load_dna(DATA_DIR, OUTPUT_DIR)
    dna_age = dna_age_days(dna)
    if youtube is not None and (dna_age is None or dna_age >= DNA_REFRESH_DAYS):
        try:
            dna = run_scan(youtube, OUTPUT_DIR, examples)
        except Exception as e:  # a stale analysis is better than a skipped upload
            print(f"ℹ️ Rakip analizi yenilenemedi: {str(e)[:200]}")
    series = pick_series(args.series, DATA_DIR, args.topic_id)
    topic = choose_topic(published, args.topic_id, dna, series=series)
    print(f"🎯 Seri {series} | konu {topic['id']}: {topic['topic']}")

    clip_pool = None
    if series == "merak":
        # Channel top titles and competitor rules come from history/film videos, so the merak prompts use their own.
        topic, script, provider, clip_pool = merak_script(topic, published, bool(args.topic_id), dna)
    else:
        character = None
        if series == "karakter":
            character = topic
            examples = film_title_examples(DATA_DIR) or examples
        script, provider = write_script(topic["topic"], style_examples=examples, rules=dna.get("our_rules"),
                                        character=character)
    print(f"🧠 Senaryo ({provider}): {script['title']}")

    result = produce(topic, script, provider, series=series, clip_pool=clip_pool)
    if youtube is not None:
        video_id = upload(youtube, result, args.privacy)
        record_upload(DATA_DIR, video_id, topic["id"], result["meta"]["title"], SERIES[series]["format"],
                      privacy=args.privacy)
    return 0


if __name__ == "__main__":
    sys.exit(main())
