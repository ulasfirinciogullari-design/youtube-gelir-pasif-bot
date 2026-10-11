"""
Produce Viral 8D Audio & Easter Egg Master Short.
Theme: 'Zihnini Ele Geçiren Sessizlik (Thomas Shelby 8D Hipnoz)'

Features:
1. Fresh Cinema Baritone Voiceover (ElevenLabs Alp / Edge-TTS Ahmet)
2. 8D Binaural Spatial Audio Engine (360° rotation around listener's head + sub-bass resonance)
3. 9:16 Split Screen: 4K Peaky Blinders Shelby (top) + 60 FPS Hypnotic Tunnel (bottom)
4. 0.05s (3 frames @ 60 FPS) Golden Cipher Flash Frame Easter Egg at 54% duration
5. High-CTR Viral HUDs:
   - Top: "🎧 KULAKLIKLA DİNLE: 8D SES"
   - Bottom: "👁️ GİZLİ ŞİFREYİ BULUP DAKİKASINI YAZAN İLK KİŞİ SABİTLENECEK 👇"
6. Word-by-Word Kinetic Bouncing ASS Subtitles
7. Proof screenshot extraction & ffprobe / astats verification.
"""

import os
import sys
import json
import subprocess
from pathlib import Path

# Force UTF-8 stdout on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.services.binaural_8d_audio_engine import get_8d_audio_filter, verify_stereo_modulation
from app.services.easter_egg_injector import (
    generate_golden_cipher_glyph,
    get_easter_egg_timing,
    generate_8d_retention_ass,
    extract_easter_egg_proof
)
from app.services.elevenlabs_engine import synthesize_elevenlabs_speech, DEFAULT_VOICE_ID
from app.services.voice_edge import synthesize_edge_tts

OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Master Video Paths
TOP_VIDEO = BASE_DIR / "assets" / "cinematic" / "peaky_master.mp4"
BOTTOM_VIDEO = BASE_DIR / "assets" / "stock" / "minecraft_parkour.mp4"
BG_MUSIC = BASE_DIR / "assets" / "music" / "mystery_documentary.mp3"

RAW_VOICE_PATH = OUTPUT_DIR / "voice_shelby_hypno_raw.mp3"
EASTER_EGG_GLYPH = OUTPUT_DIR / "golden_cipher_glyph.jpg"
ASS_SUBTITLES = OUTPUT_DIR / "subtitles_8d_easter_egg.ass"
FINAL_MASTER_VIDEO = OUTPUT_DIR / "VIRAL_8D_EASTER_EGG_MASTER.mp4"
PROOF_SCREENSHOT = OUTPUT_DIR / "easter_egg_proof.jpg"

# Viral Script Lines: "Zihnini Ele Geçiren Sessizlik (Thomas Shelby 8D Hipnoz)"
SCRIPT_SEGMENTS = [
    ("Kulaklığını tak ve sesin başının etrafında dönüşünü hisset.", 0.0, 4.6),
    ("Zayıf insanlar güç kazanmak için bağırır, gürültü yapar.", 4.6, 9.2),
    ("Fakat zirvedeki zihinler... sadece ölümcül sessizliğin içinde planlar.", 9.2, 14.8),
    ("Bir fırtına koparmadan önce bütün dünya susar.", 14.8, 19.2),
    ("Gözlerini açık tut ve kafanın içindeki bu sesi takip et.", 19.2, 23.8),
    ("Zirvenin Kanunu: En tehlikeli adam, sustuğu an hamlesini yapandır.", 23.8, 29.5)
]

FULL_NARRATION_TEXT = " ".join(seg[0] for seg in SCRIPT_SEGMENTS)


def step1_synthesize_voice() -> float:
    print("\n🎙️ [STEP 1/5] Synthesizing Baritone Voiceover...")
    print(f"Narrating text: '{FULL_NARRATION_TEXT[:70]}...'")
    
    # Try ElevenLabs Alp Baritone first
    success = False
    try:
        success = synthesize_elevenlabs_speech(
            text=FULL_NARRATION_TEXT,
            output_path=RAW_VOICE_PATH,
            voice_id=DEFAULT_VOICE_ID
        )
    except Exception as e:
        print(f"⚠️ ElevenLabs attempt error: {e}")
        success = False

    if not success or not RAW_VOICE_PATH.exists() or RAW_VOICE_PATH.stat().st_size < 1000:
        print("🔄 Falling back to Edge-TTS Turkish Baritone (AhmetNeural)...")
        res = synthesize_edge_tts(
            text=FULL_NARRATION_TEXT,
            output_path=RAW_VOICE_PATH,
            voice="tr-TR-AhmetNeural",
            rate="+2%"
        )
        print(f"Edge-TTS synthesized successfully: {res['duration']:.2f}s")
    else:
        print(f"✅ ElevenLabs Alp Baritone synthesized: {RAW_VOICE_PATH.stat().st_size} bytes")

    # Probe duration
    probe_cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(RAW_VOICE_PATH)
    ]
    res = subprocess.run(probe_cmd, capture_output=True, encoding="utf-8", errors="replace", check=True)
    voice_dur = float(res.stdout.strip())
    print(f"⏱️ Voiceover Duration: {voice_dur:.2f} seconds")
    return voice_dur


def step2_generate_easter_egg_assets(total_dur: float):
    print("\n👁️ [STEP 2/5] Creating 0.05s Golden Glyph & Easter Egg Cipher...")
    generate_golden_cipher_glyph(EASTER_EGG_GLYPH, cipher_code="#ZIRVE777-ALPHA")
    print(f"✅ Golden Cipher Card created at: {EASTER_EGG_GLYPH}")

    # Compute flash timing (0.05s = 3 frames at 60 FPS) around 54% of runtime
    start_t, end_t = get_easter_egg_timing(total_dur, pct_position=0.54, flash_duration=0.05)
    print(f"🎯 Easter Egg Flash Window: {start_t:.3f}s -> {end_t:.3f}s (Duration: {(end_t - start_t):.3f}s)")
    return start_t, end_t


def step3_generate_kinetic_subtitles(voice_dur: float, total_dur: float):
    print("\n💬 [STEP 3/5] Generating Kinetic Bouncing ASS Subtitles & HUD Overlays...")
    
    # Scale segment timings to match actual audio duration
    recorded_end = SCRIPT_SEGMENTS[-1][2]
    scale_factor = voice_dur / recorded_end
    scaled_segments = []
    for text, s_start, s_end in SCRIPT_SEGMENTS:
        scaled_segments.append((text, round(s_start * scale_factor, 2), round(s_end * scale_factor, 2)))
        
    generate_8d_retention_ass(
        timed_sentences=scaled_segments,
        output_path=ASS_SUBTITLES,
        total_duration=total_dur,
        top_cta="🎧 KULAKLIKLA DİNLE: 8D SES",
        bottom_cta="👁️ GİZLİ ŞİFREYİ BULUP DAKİKASINI YAZAN İLK KİŞİ SABİTLENECEK 👇"
    )
    print(f"✅ Kinetic Subtitles & HUD Banner generated: {ASS_SUBTITLES}")


def step4_render_master_video(start_flash: float, end_flash: float, total_dur: float):
    print("\n🎬 [STEP 4/5] Rendering 60 FPS 8D Master Video via FFmpeg...")
    
    # Convert ASS path to relative forward-slash format for libass
    rel_ass = str(ASS_SUBTITLES.resolve().relative_to(BASE_DIR)).replace("\\", "/")

    # Build 8D filter string
    filter_8d_voice = (
        "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
        "equalizer=f=80:width_type=h:width=50:g=5.0,"
        "equalizer=f=220:width_type=h:width=120:g=2.5,"
        "aecho=0.8:0.75:35|50:0.22|0.15,"
        "apulsator=mode=sine:hz=0.20:amount=0.92:offset_l=0:offset_r=0.5,"
        "loudnorm=I=-14:TP=-1.0:LRA=7"
    )

    # Filter complex
    filter_complex = (
        # 1. Top video: Peaky Blinders Shelby scaled to 1080x960 @ 60 FPS
        "[0:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,fps=60[v_top];"
        # 2. Bottom video: Hypnotic tunnel scaled to 1080x960 @ 60 FPS
        "[1:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,fps=60[v_bot];"
        # 3. Stack vertically into 1080x1920
        "[v_top][v_bot]vstack[v_stack];"
        # 4. Golden separator line at y=956
        "[v_stack]drawbox=y=956:color=gold@0.95:width=1080:height=8:t=fill[v_base];"
        # 5. Easter Egg Golden Glyph Overlay (exactly between start_flash and end_flash)
        "[2:v]scale=1080:1920,fps=60[v_glyph];"
        f"[v_base][v_glyph]overlay=0:0:enable='between(t,{start_flash:.3f},{end_flash:.3f})'[v_ov];"
        # 6. Burn kinetic bouncing subtitles + Top/Bottom HUDs
        f"[v_ov]ass=filename='{rel_ass}'[v_out];"
        # 7. 8D Voice Spatialization
        f"[3:a]{filter_8d_voice}[a_voice_8d];"
        # 8. Subtle Atmospheric Background Music
        "[4:a]volume=0.08,lowpass=f=4000,highpass=f=100[a_bg];"
        # 9. Audio Mix: Dominant 8D Voice + Soft Music Bed
        "[a_voice_8d][a_bg]amix=inputs=2:duration=first:weights=6 1:dropout_transition=0,loudnorm=I=-14:TP=-1.0:LRA=7[a_out]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", str(TOP_VIDEO),
        "-stream_loop", "-1", "-i", str(BOTTOM_VIDEO),
        "-i", str(EASTER_EGG_GLYPH),
        "-i", str(RAW_VOICE_PATH),
        "-stream_loop", "-1", "-i", str(BG_MUSIC),
        "-filter_complex", filter_complex,
        "-map", "[v_out]",
        "-map", "[a_out]",
        "-t", f"{total_dur:.2f}",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",
        "-r", "60",
        "-c:a", "aac",
        "-b:a", "320k",
        "-ar", "48000",
        "-pix_fmt", "yuv420p",
        str(FINAL_MASTER_VIDEO)
    ]

    print("🚀 Running FFmpeg Master Production...")
    subprocess.run(cmd, check=True)
    size_mb = FINAL_MASTER_VIDEO.stat().st_size / (1024 * 1024)
    print(f"🎉 MASTER VIDEO GENERATED: {FINAL_MASTER_VIDEO.name} ({size_mb:.2f} MB)")


def step5_verify_and_screenshot(start_flash: float):
    print("\n🔍 [STEP 5/5] Verification & Proof Extraction...")
    
    # 1. Probe Video Specs
    probe_cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "stream=width,height,r_frame_rate,codec_name,channels",
        "-show_entries", "format=duration,size,bit_rate",
        "-of", "json",
        str(FINAL_MASTER_VIDEO)
    ]
    res = subprocess.run(probe_cmd, capture_output=True, encoding="utf-8", errors="replace", check=True)
    probe_data = json.loads(res.stdout)
    print("📊 Video Streams Info:")
    for s in probe_data.get("streams", []):
        print(f"  - Type: {s.get('codec_type', 'unknown')}, Codec: {s.get('codec_name')}, "
              f"Resolution: {s.get('width')}x{s.get('height')}, FPS: {s.get('r_frame_rate')}, Channels: {s.get('channels')}")

    # 2. Verify 8D Audio Modulation
    print("\n🎧 Audio Stereo Modulation Analysis:")
    audio_stats = verify_stereo_modulation(FINAL_MASTER_VIDEO)
    print(f"  - Channel 1 (Left) RMS: {audio_stats.get('channel_1_rms_db')} dB")
    print(f"  - Channel 2 (Right) RMS: {audio_stats.get('channel_2_rms_db')} dB")
    print(f"  - Overall RMS: {audio_stats.get('overall_rms_db')} dB")
    print(f"  - Result: {audio_stats.get('status')}")

    # 3. Extract Proof Screenshot
    # Capture right in the middle of the 0.05s flash frame
    proof_timestamp = start_flash + 0.025
    print(f"\n📸 Extracting Easter Egg Proof Screenshot at t={proof_timestamp:.3f}s...")
    extract_easter_egg_proof(FINAL_MASTER_VIDEO, PROOF_SCREENSHOT, proof_timestamp)
    print(f"✅ Proof screenshot saved: {PROOF_SCREENSHOT} ({PROOF_SCREENSHOT.stat().st_size} bytes)")


def main():
    print("=" * 65)
    print("🔥 ZIRVENİN KANUNU: 8D BINAURAL & EASTER EGG RETENTION MASTER 🔥")
    print("=" * 65)
    
    voice_dur = step1_synthesize_voice()
    total_dur = round(voice_dur + 0.6, 2)
    print(f"🎯 Total Master Video Duration: {total_dur:.2f}s")
    
    start_flash, end_flash = step2_generate_easter_egg_assets(total_dur)
    step3_generate_kinetic_subtitles(voice_dur, total_dur)
    step4_render_master_video(start_flash, end_flash, total_dur)
    step5_verify_and_screenshot(start_flash)
    
    print("\n" + "=" * 65)
    print("🏆 ALL MANDATE DELIVERABLES SUCCESSFULLY COMPLETED!")
    print(f"📁 Master Video: {FINAL_MASTER_VIDEO}")
    print(f"🖼️ Proof Screenshot: {PROOF_SCREENSHOT}")
    print("=" * 65)


if __name__ == "__main__":
    main()
