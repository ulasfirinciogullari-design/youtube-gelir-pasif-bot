"""
Production Pipeline for '30 GÜNLÜK ZİRVE MEYDAN OKUMASI' Serial Episodes.
Target: High-Retention Split-Screen Shorts for @zirveninkanunu.
Features:
- Header Badge: "🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI | GÜN X/30"
- Top half (1080x960): Cinematic Marcus Aurelius / Gladiator / Shelby clip.
- Bottom half (1080x960): Butter-smooth 60 FPS Minecraft Parkour / BeamNG loop.
- Glowing gold split divider bar.
- Kinetic animated ASS subtitles with karaoke word highlights.
- High-authority Turkish voiceover (-2Hz deep tone).
- Mastered audio with sound design (impact riser, cinematic thud sub-bass, music bed).
- Cliffhanger hook CTA.
- Automatic preview screenshot generation.
"""

import os
import sys
import argparse
import asyncio
import json
import re
import subprocess
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import edge_tts
from app.services.serial_challenge_engine import get_day_curriculum, save_curriculum_json
from app.services.voice_edge import _get_audio_duration


def format_ass_timestamp(seconds: float) -> str:
    """Formats float seconds into ASS timestamp H:MM:SS.cs"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs >= 100:
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def generate_challenge_ass_subtitles(
    sentences: list,
    output_path: Path,
    badge_text: str,
    total_duration: float,
    highlight_color: str = "&H0000FFFF",  # Bright Yellow
    primary_color: str = "&H00FFFFFF",    # Pure White
) -> Path:
    """
    Generates tailored ASS subtitles featuring:
    1. Static Header Badge at the top.
    2. Subtle channel watermark at the bottom.
    3. Kinetic word-by-word karaoke subtitles in the bottom half.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    end_time_str = format_ass_timestamp(total_duration + 0.5)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: HeaderBadge,Arial Black,34,&H0000D7FF,&H00000000,&H00000000,&H80000000,-1,0,0,0,100,100,1,0,1,4,2,8,20,20,105,1
Style: SubtitleStyle,Arial Black,64,{primary_color},&H000000FF,&H00000000,&H90000000,-1,0,0,0,100,100,2,0,1,7,3,2,50,50,480,1
Style: WatermarkStyle,Arial,28,&H70FFFFFF,&H000000FF,&H40000000,&H60000000,-1,0,0,0,100,100,2,0,1,2,1,2,40,40,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events = []
    # 1. Header Badge Event (Upper center)
    events.append(f"Dialogue: 1,0:00:00.00,{end_time_str},HeaderBadge,,0,0,0,,{{\\an8\\pos(540,105)}}{badge_text}")

    # 2. Watermark Event (Bottom safe area)
    events.append(f"Dialogue: 1,0:00:00.00,{end_time_str},WatermarkStyle,,0,0,0,,{{\\an2\\pos(540,1860)}}👑 @zirveninkanunu")

    # 3. Kinetic Karaoke Subtitles (Center of bottom screen)
    for sentence in sentences:
        text = sentence.get("text", "").strip()
        start = float(sentence.get("start", 0.0))
        end = float(sentence.get("end", 0.0))

        if not text or end <= start:
            continue

        words = text.split()
        if not words:
            continue

        # Chunk into 3-4 words for high readability and fast-paced mobile engagement
        max_chunk = 4
        chunks = [words[i:i + max_chunk] for i in range(0, len(words), max_chunk)]
        total_words = len(words)
        total_time = end - start

        chunk_start = start
        for chunk in chunks:
            chunk_duration = total_time * (len(chunk) / total_words)
            chunk_end = chunk_start + chunk_duration
            word_time = chunk_duration / len(chunk)

            for active_idx, _ in enumerate(chunk):
                w_start = chunk_start + (active_idx * word_time)
                w_end = w_start + word_time

                # Highlight active word
                highlighted_words = []
                for i, w in enumerate(chunk):
                    if i == active_idx:
                        highlighted_words.append(f"{{\\c{highlight_color}\\t(0,100,\\fscx110\\fscy110)}}{w}{{\\c{primary_color}\\fscx100\\fscy100}}")
                    else:
                        highlighted_words.append(w)

                chunk_text = " ".join(highlighted_words)
                start_str = format_ass_timestamp(w_start)
                end_str = format_ass_timestamp(w_end)
                events.append(f"Dialogue: 2,{start_str},{end_str},SubtitleStyle,,0,0,0,,{chunk_text}")

            chunk_start = chunk_end

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(events) + "\n")

    return output_path


import base64
import urllib.request
from app.services.elevenlabs_engine import get_elevenlabs_api_key, DEFAULT_VOICE_ID, normalize_turkish_tts_text


def synthesize_voice_with_timestamps(
    script_text: str,
    output_audio_path: Path,
    voice_id: str = DEFAULT_VOICE_ID,
    model_id: str = "eleven_multilingual_v2"
) -> list:
    """
    Generates Hollywood-grade voice audio and extracts word timestamps.
    Uses ElevenLabs with-timestamps API for millimeter synchronization,
    with fallback to edge-tts if unavailable.
    """
    output_audio_path.parent.mkdir(parents=True, exist_ok=True)
    clean_text = normalize_turkish_tts_text(script_text)
    api_key = get_elevenlabs_api_key()

    if api_key:
        print(f"       🎙️ ElevenLabs Bariton Sentezleniyor (Model: {model_id})...")
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"
        payload = {
            "text": clean_text,
            "model_id": model_id,
            "voice_settings": {
                "stability": 0.40,
                "similarity_boost": 0.80,
                "style": 0.40,
                "use_speaker_boost": True
            }
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
                "User-Agent": "Antigravity/1.0"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                audio_bytes = base64.b64decode(data["audio_base64"])
                with open(output_audio_path, "wb") as f:
                    f.write(audio_bytes)

                align = data.get("alignment", {})
                chars = align.get("characters", [])
                starts = align.get("character_start_times_seconds", [])
                ends = align.get("character_end_times_seconds", [])

                words = []
                curr_chars = []
                curr_start = None
                curr_end = None
                for c, s, e in zip(chars, starts, ends):
                    if c.isspace():
                        if curr_chars:
                            words.append({
                                "word": "".join(curr_chars),
                                "start": curr_start,
                                "end": curr_end
                            })
                            curr_chars = []
                            curr_start = None
                    else:
                        if curr_start is None:
                            curr_start = s
                        curr_end = e
                        curr_chars.append(c)
                if curr_chars:
                    words.append({
                        "word": "".join(curr_chars),
                        "start": curr_start,
                        "end": curr_end
                    })

                # Group words into sentence-like rhythmic chunks (3-4 words)
                sentences = []
                chunk_size = 4
                for i in range(0, len(words), chunk_size):
                    chunk = words[i:i + chunk_size]
                    if chunk:
                        text_chunk = " ".join(w["word"] for w in chunk)
                        sentences.append({
                            "text": text_chunk,
                            "start": chunk[0]["start"],
                            "end": chunk[-1]["end"]
                        })

                if sentences:
                    return sentences
        except Exception as e:
            print(f"       ⚠️ ElevenLabs uyarısı: {e}. Edge-TTS fallback deneniyor...")

    # Edge-TTS Fallback
    try:
        import edge_tts
        comm = edge_tts.Communicate(clean_text, "tr-TR-AhmetNeural", rate="+3%", pitch="-2Hz")
        submaker = edge_tts.SubMaker()

        async def _run_edge():
            with open(output_audio_path, "wb") as f:
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])
                    elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                        submaker.feed(chunk)

        asyncio.run(_run_edge())
        raw_srt = submaker.get_srt()
        sentences = []
        blocks = re.split(r"\n\s*\n", raw_srt.strip())
        for b in blocks:
            lines = b.strip().split("\n")
            if len(lines) >= 3:
                m = re.match(r"(\d+:\d+:\d+,\d+)\s*-->\s*(\d+:\d+:\d+,\d+)", lines[1])
                if m:
                    def parse_t(t_str):
                        h, mn, rest = t_str.split(":")
                        s, ms = rest.split(",")
                        return int(h) * 3600 + int(mn) * 60 + int(s) + int(ms) / 1000.0

                    sentence_text = " ".join(lines[2:]).strip()
                    sentences.append({
                        "text": sentence_text,
                        "start": parse_t(m.group(1)),
                        "end": parse_t(m.group(2))
                    })
        if sentences:
            return sentences
    except Exception as e:
        print(f"       ⚠️ Edge-TTS uyarısı: {e}")

    dur = _get_audio_duration(output_audio_path)
    return [{"text": script_text, "start": 0.0, "end": dur}]


def produce_challenge_episode(
    day: int = 1,
    custom_output: Path = None,
    custom_top_video: Path = None,
    custom_bottom_video: Path = None,
    voice_name: str = "tr-TR-AhmetNeural",
    target_fps: int = 60
) -> Path:
    """Main production function for generating a 30-Day Peak Challenge episode."""
    save_curriculum_json()
    curriculum = get_day_curriculum(day)

    output_dir = BASE_DIR / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = int(time.time())
    work_dir = output_dir / f"CHALLENGE_DAY_{day:02d}_BUILD_{timestamp}"
    work_dir.mkdir(parents=True, exist_ok=True)

    final_output = custom_output if custom_output else output_dir / f"CHALLENGE_DAY_{day:02d}_MASTER.mp4"

    print("\n" + "=" * 76)
    print(f"  🔥 30 GÜNLÜK ZİRVE MEYDAN OKUMASI - GÜN {day:02d}/30 ÜRETİM HATTI")
    print(f"  👑 Başlık: {curriculum['title']} ({curriculum['subtitle']})")
    print(f"  📜 Kategori: {curriculum['category']} | Söz: {curriculum['quote_author']}")
    print(f"  🎯 Çıktı: {final_output.name}")
    print("=" * 76 + "\n")

    # 1. Voiceover Synthesis
    print(f"[1/5] 🎙️ Karizmatik Seslendirme Hazırlanıyor ({voice_name}, rate=+3%, pitch=-2Hz)...")
    full_script = " ".join(curriculum["script_lines"])
    voice_audio_path = work_dir / "voiceover.mp3"

    sentences = synthesize_voice_with_timestamps(
        script_text=full_script,
        output_audio_path=voice_audio_path
    )
    total_audio_dur = _get_audio_duration(voice_audio_path)
    # Add 1.2s padding at the end for CTA cliffhanger retention
    video_duration = total_audio_dur + 1.2
    print(f"       ✅ Seslendirme tamamlandı: {total_audio_dur:.2f}s (Toplam Video: {video_duration:.2f}s)")

    # 2. Kinetic Subtitle Generation
    print("[2/5] ✍️ Kinetik ASS Altyazı ve Başlık Rozeti İnşa Ediliyor...")
    ass_subtitle_path = work_dir / "challenge_kinetic_subs.ass"
    generate_challenge_ass_subtitles(
        sentences=sentences,
        output_path=ass_subtitle_path,
        badge_text=curriculum["badge_text"],
        total_duration=video_duration,
        highlight_color="&H0000FFFF",  # Bright Gold/Yellow highlight
        primary_color="&H00FFFFFF",
    )
    print(f"       ✅ Altyazı dosyası hazır: {ass_subtitle_path.name}")

    # 3. Video Assets Selection
    cinematic_dir = BASE_DIR / "assets" / "cinematic"
    stock_dir = BASE_DIR / "assets" / "stock"
    music_dir = BASE_DIR / "assets" / "music"
    sfx_dir = BASE_DIR / "assets" / "sfx"

    # Top video
    top_video_candidate = custom_top_video or (cinematic_dir / curriculum.get("primary_cinematic", "gladiator_master.mp4"))
    if not top_video_candidate.exists():
        top_video_candidate = cinematic_dir / "peaky_master.mp4"
    if not top_video_candidate.exists():
        top_video_candidate = cinematic_dir / "batman_master.mp4"

    # Bottom video
    bottom_video_candidate = custom_bottom_video or (stock_dir / curriculum.get("bottom_stock", "minecraft_parkour.mp4"))
    if not bottom_video_candidate.exists():
        bottom_video_candidate = stock_dir / "minecraft_parkour.mp4"

    # Audio design assets
    bg_music = music_dir / "epic_hollywood_braam.mp3"
    if not bg_music.exists():
        bg_music = music_dir / "dark_tension_pulse.mp3"

    impact_sfx = sfx_dir / "impact_sub.mp3"
    riser_sfx = sfx_dir / "tension_riser.mp3"
    if not riser_sfx.exists():
        riser_sfx = sfx_dir / "riser_cinematic.mp3"

    print(f"[3/5] 🎬 Varlıklar Bağlanıyor:")
    print(f"       - Üst Sahne: {top_video_candidate.name}")
    print(f"       - Alt Sahne: {bottom_video_candidate.name} ({target_fps} FPS)")
    print(f"       - Müzik: {bg_music.name}")
    print(f"       - SFX: {impact_sfx.name} & {riser_sfx.name}")

    # 4. Multi-Track Composite & Render
    print(f"[4/5] 🚀 FFmpeg 60 FPS Bölünmüş Ekran & Ses Tasarımı Sentezleniyor...")

    # Calculate riser delay: place riser 4.5 seconds before video end
    riser_delay_ms = max(0, int((video_duration - 4.5) * 1000))

    # Escape path for FFmpeg subtitles filter
    safe_ass_path = str(ass_subtitle_path.resolve()).replace("\\", "/").replace(":", "\\:")

    filter_complex = (
        # 1. Top video: 1080x960, 60fps, film grade
        f"[0:v]scale=1080:960:force_original_aspect_ratio=increase,"
        f"crop=1080:960:x=(in_w-1080)/2:y=(in_h-960)/2,"
        f"setsar=1,fps={target_fps},"
        f"eq=contrast=1.18:saturation=1.08:brightness=-0.02,"
        f"setpts=PTS-STARTPTS[top];"
        
        # 2. Bottom video: 1080x960, 60fps
        f"[1:v]scale=1080:960:force_original_aspect_ratio=increase,"
        f"crop=1080:960:x=(in_w-1080)/2:y=(in_h-960)/2,"
        f"setsar=1,fps={target_fps},"
        f"setpts=PTS-STARTPTS[bottom];"
        
        # 3. Stack vertically -> 1080x1920
        f"[top][bottom]vstack=inputs=2[stacked];"
        
        # 4. Neon gold divider & Header banner container & ASS Subtitles
        f"[stacked]drawbox=y=956:color=gold@0.95:width=1080:height=8:t=fill,"
        f"drawbox=y=954:color=yellow@0.5:width=1080:height=12:t=fill,"
        f"drawbox=x=30:y=65:w=1020:h=85:color=black@0.75:t=fill,"
        f"drawbox=x=30:y=65:w=1020:h=85:color=gold@0.85:t=3,"
        f"subtitles='{safe_ass_path}'[vout];"
        
        # 5. Audio Mastering: Voice + Music Bed + Impact Sub + Cliffhanger Riser
        f"[2:a]volume=1.0[v_voice];"
        f"[3:a]volume=0.16[v_music];"
        f"[4:a]volume=0.65[v_impact];"
        f"[5:a]adelay={riser_delay_ms}|{riser_delay_ms},volume=0.45[v_riser];"
        f"[v_voice][v_impact][v_riser]amix=inputs=3:duration=first:dropout_transition=0[v_voice_sfx];"
        f"[v_voice_sfx][v_music]amix=inputs=2:duration=first:dropout_transition=2[aout]"
    )

    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", str(top_video_candidate),
        "-stream_loop", "-1", "-i", str(bottom_video_candidate),
        "-i", str(voice_audio_path),
        "-stream_loop", "-1", "-i", str(bg_music),
        "-i", str(impact_sfx),
        "-i", str(riser_sfx),
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "[aout]",
        "-t", f"{video_duration:.2f}",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-r", str(target_fps),
        "-c:a", "aac",
        "-b:a", "256k",
        "-pix_fmt", "yuv420p",
        str(final_output)
    ]

    subprocess.run(ffmpeg_cmd, check=True)
    print(f"       ✅ Video render edildi: {final_output.name}")

    # 5. Probe and Screenshot Preview
    print("[5/5] 📸 Video Doğrulaması ve Önizleme Ekran Görüntüsü Alınıyor...")
    preview_img = output_dir / f"preview_challenge_day{day:02d}.jpg"

    # Screenshot at second 4.0
    screenshot_cmd = [
        "ffmpeg", "-y",
        "-ss", "00:00:04.00",
        "-i", str(final_output),
        "-vframes", "1",
        "-q:v", "2",
        str(preview_img)
    ]
    subprocess.run(screenshot_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Probe specs
    probe_cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration,size:stream=width,height,r_frame_rate",
        "-of", "json",
        str(final_output)
    ]
    probe_raw = subprocess.check_output(probe_cmd)
    probe_info = json.loads(probe_raw)

    dur = float(probe_info.get("format", {}).get("duration", 0))
    file_size_mb = float(probe_info.get("format", {}).get("size", 0)) / (1024 * 1024)
    v_stream = next((s for s in probe_info.get("streams", []) if "width" in s), {})

    print("\n" + "=" * 76)
    print(f"  🏆 MASTER ÇIKTI HAZIR: {final_output.name}")
    print(f"  📁 Dosya Yolu: {final_output}")
    print(f"  🖼️ Önizleme: {preview_img}")
    print(f"  ⏱️ Süre: {dur:.2f} saniye")
    print(f"  📐 Çözünürlük: {v_stream.get('width')}x{v_stream.get('height')} @ {v_stream.get('r_frame_rate')} FPS")
    print(f"  💾 Dosya Boyutu: {file_size_mb:.2f} MB")
    print("=" * 76 + "\n")

    return final_output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Produce a 30-Day Peak Challenge Episode")
    parser.add_argument("--day", type=int, default=1, help="Day number (1-30)")
    parser.add_argument("--output", type=str, default=None, help="Custom output video path")
    parser.add_argument("--voice", type=str, default="tr-TR-AhmetNeural", help="Voice model")
    parser.add_argument("--fps", type=int, default=60, help="Target FPS (default 60)")
    args = parser.parse_args()

    custom_out = Path(args.output) if args.output else None
    produce_challenge_episode(
        day=args.day,
        custom_output=custom_out,
        voice_name=args.voice,
        target_fps=args.fps
    )
