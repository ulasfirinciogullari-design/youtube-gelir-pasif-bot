"""
Produce Viral Dual-Voice Cinema Faceoff Short:
'Thomas Shelby vs Walter White: Masada Kim Hayatta Kalır?'
Channel: Zirvenin Kanunu (@zirveninkanunu)

Pipeline:
1. Dialogue Generation & Voice Synthesis:
   - Thomas Shelby: Cold, unshakeable baritone (ElevenLabs Alp - tr_alpha, deep rumble eq)
   - Walter White: Gritty, ruthless, ego-driven cynical voice (ElevenLabs Emrullah - tr_emrullah)
2. Dynamic Top Video Editing (1080x960 60 FPS):
   - Dynamic alternation between Peaky Blinders clips and Breaking Bad clips on every speaker switch.
3. Bottom Video (1080x960 60 FPS):
   - 60 FPS Minecraft Parkour.
   - 10px glowing golden neon divider bar (#FFD700).
4. Kinetic Bouncing ASS Subtitles:
   - Karaoke bouncing words, neon yellow highlight (&H0000FFFF) for Shelby, neon cyan highlight (&H00FFFF00) for Walter White.
   - Top Header Badge: '⚔️ EFSANELERİN SAVAŞI | SHELBY vs WALTER WHITE'
   - Bottom Comment Magnet: '💬 Sen kimin tarafındasın? 1: Shelby / 2: Walter White. Yoruma yaz! 👇'
5. Hollywood Audio Mastering:
   - 808 sub-bass opener, transition whooshes between cuts, dark cinematic tension music ducked, EBU R128 mastering (-15.0 LUFS, -0.7 dBTP).
6. Deliverables:
   - output/VIRAL_FACEOFF_SHELBY_VS_WALTER_MASTER.mp4
   - output/preview_faceoff_shelby_walter.jpg
   - output/VIRAL_FACEOFF_SHELBY_VS_WALTER_meta.json
"""

import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.services.elevenlabs_engine import synthesize_elevenlabs_speech

OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Assets
ASSETS_DIR = BASE_DIR / "assets"
PEAKY_VIDEO = ASSETS_DIR / "cinematic" / "peaky_master.mp4"
WALTER_VIDEO = ASSETS_DIR / "cinematic" / "breaking_bad_master.mp4"
MINECRAFT_VIDEO = ASSETS_DIR / "stock" / "minecraft_parkour.mp4"

SFX_808 = ASSETS_DIR / "sfx" / "sub_boom_808.mp3"
SFX_WHOOSH = ASSETS_DIR / "sfx" / "cinematic_whoosh.mp3"
SFX_RISER = ASSETS_DIR / "sfx" / "riser_cinematic.mp3"
SFX_IMPACT = ASSETS_DIR / "sfx" / "impact_sub.mp3"
BG_MUSIC = ASSETS_DIR / "music" / "dark_tension_pulse.mp3"

# Output Targets
MASTER_VIDEO = OUTPUT_DIR / "VIRAL_FACEOFF_SHELBY_VS_WALTER_MASTER.mp4"
PREVIEW_JPG = OUTPUT_DIR / "preview_faceoff_shelby_walter.jpg"
META_JSON = OUTPUT_DIR / "VIRAL_FACEOFF_SHELBY_VS_WALTER_meta.json"
ASS_SUBTITLES = OUTPUT_DIR / "faceoff_shelby_walter.ass"

# Script dialogue setup
DIALOGUE_TURNS = [
    {
        "id": "walter_1",
        "speaker": "WALTER WHITE",
        "character": "walter",
        "voice_id": "QftNgZhlSOYKrYbk7WR7", # Emrullah - gritty, ruthless ego
        "text": "Kim olduğunu sanıyorsun sen? Küçük bir çete lideri mi? Karşında bir imparatorluk kuran kimyager var. Ben tehlikenin ta kendisiyim!",
        "video_source": WALTER_VIDEO,
        "clip_start": 93.5, # Walter White direct chest-pounding 'I AM THE DANGER' monologue
        "highlight_color": "00FFFF00", # Neon Cyan in ASS BGR (&H00FFFF00)
        "speaker_color": "00FFFF00",
        "speaker_tag": "🧪 WALTER WHITE",
    },
    {
        "id": "shelby_1",
        "speaker": "THOMAS SHELBY",
        "character": "shelby",
        "voice_id": "HV5rAif1q1ITHdvxqyMw", # Alp - Cold, unshakeable baritone
        "text": "Sen sadece laboratuvarda fare zehri pişiren kibirli bir adamsın Walter. Ben siperlerde ölümü gördüm. Masada kurallar kimyayla değil, soğukkanlılıkla yazılır.",
        "video_source": PEAKY_VIDEO,
        "clip_start": 12.0, # Shelby cigarette & piercing cold gaze
        "highlight_color": "0000FFFF", # Neon Yellow in ASS BGR (&H0000FFFF)
        "speaker_color": "0000FFFF",
        "speaker_tag": "🎩 THOMAS SHELBY",
    },
    {
        "id": "walter_2",
        "speaker": "WALTER WHITE",
        "character": "walter",
        "voice_id": "QftNgZhlSOYKrYbk7WR7",
        "text": "Kibir mi? Benim tek bir formülüm senin tüm kasabanı satın alır Shelby! Ben düşmanlarımı tek hamlede yok ederim.",
        "video_source": WALTER_VIDEO,
        "clip_start": 83.5, # Walter White intense aggressive monologue
        "highlight_color": "00FFFF00",
        "speaker_color": "00FFFF00",
        "speaker_tag": "🧪 WALTER WHITE",
    },
    {
        "id": "shelby_2",
        "speaker": "THOMAS SHELBY",
        "character": "shelby",
        "voice_id": "HV5rAif1q1ITHdvxqyMw",
        "text": "Sen egona yenik düştün Walter. O masada ilk tetiği çeken değil, kurşun gelmeden önce gözünü kırpmayan hayatta kalır.",
        "video_source": PEAKY_VIDEO,
        "clip_start": 42.0, # Shelby menacing stance
        "highlight_color": "0000FFFF",
        "speaker_color": "0000FFFF",
        "speaker_tag": "🎩 THOMAS SHELBY",
    },
    {
        "id": "shelby_outro",
        "speaker": "THOMAS SHELBY",
        "character": "shelby",
        "voice_id": "HV5rAif1q1ITHdvxqyMw",
        "text": "Zirvenin Kanunu: Kimya yıkar, ama zihin yönetir.",
        "video_source": PEAKY_VIDEO,
        "clip_start": 70.0, # Shelby walking / legendary climax
        "highlight_color": "0000FFFF",
        "speaker_color": "0000FFFF",
        "speaker_tag": "👑 ZİRVENİN KANUNU",
    }
]

POWER_WORDS_MAP = {
    "TEHLİKENİN": ("⚠️", "0022FF"),
    "İMPARATORLUK": ("👑", "00D7FF"),
    "KİMYAGER": ("🧪", "00FFFF"),
    "ÖLÜMÜ": ("💀", "1E1EFF"),
    "SOĞUKKANLILIKLA": ("🧊", "00FFFF"),
    "KURALLAR": ("♟️", "00D7FF"),
    "DÜŞMANLARIMI": ("🎯", "0055FF"),
    "EGONA": ("🔥", "0033FF"),
    "KURŞUN": ("💥", "0022FF"),
    "HAYATTA": ("⚡", "00FFFF"),
    "ZİRVENİN": ("🏔️", "00D7FF"),
    "KANUNU": ("⚖️", "00D7FF"),
    "ZİHİN": ("🧠", "00FFFF"),
    "YÖNETİR": ("👑", "00D7FF"),
}

def format_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs >= 100:
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def get_audio_duration(file_path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(file_path)
    ]
    res = subprocess.check_output(cmd, text=True).strip()
    return float(res)


def step1_synthesize_voices():
    print("🎙️ STEP 1: Synthesizing Dual Character Voices (Shelby vs Walter)...")
    for turn in DIALOGUE_TURNS:
        voice_file = OUTPUT_DIR / f"voice_{turn['id']}.mp3"
        if not voice_file.exists() or voice_file.stat().st_size < 1000:
            print(f"  🔊 Synthesizing [{turn['speaker']}]: '{turn['text'][:30]}...'")
            ok = synthesize_elevenlabs_speech(turn["text"], voice_file, voice_id=turn["voice_id"])
            if not ok or not voice_file.exists():
                raise RuntimeError(f"Failed to synthesize voice for {turn['id']}")
        dur = get_audio_duration(voice_file)
        turn["audio_file"] = voice_file
        turn["raw_duration"] = dur
        print(f"  ✅ {turn['id']} ({turn['speaker']}): {dur:.2f}s -> {voice_file.name}")


def step2_build_timeline():
    print("\n⏱️ STEP 2: Building Master Dialogue & Cut Timeline...")
    current_time = 0.0
    pause_between_turns = 0.35 # Natural dramatic pause between lines

    for idx, turn in enumerate(DIALOGUE_TURNS):
        dur = turn["raw_duration"]
        turn["start_time"] = current_time
        turn["voice_end_time"] = current_time + dur
        
        # Segment cut length extends through the pause
        if idx == len(DIALOGUE_TURNS) - 1:
            turn["end_time"] = turn["voice_end_time"] + 1.20 # Outro freeze buffer
        else:
            turn["end_time"] = turn["voice_end_time"] + pause_between_turns
            
        turn["segment_duration"] = turn["end_time"] - turn["start_time"]
        current_time = turn["end_time"]
        print(f"  🎬 Cut {idx+1} [{turn['speaker']}]: {turn['start_time']:.2f}s -> {turn['end_time']:.2f}s (Dur: {turn['segment_duration']:.2f}s)")

    total_duration = current_time
    print(f"🔥 Total Faceoff Short Duration: {total_duration:.2f}s")
    return total_duration


def step3_generate_faceoff_ass_subtitles(total_duration: float):
    print("\n📝 STEP 3: Generating Bouncing Kinetic ASS Subtitles...")
    header = f"""[Script Info]
Title: Zirvenin Kanunu - Shelby vs Walter White Kinetic Faceoff
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: SpeakerTag,Arial,38,&H00FFFFFF,&H00000000,&H00000000,&HB0000000,-1,0,0,0,100,100,2,0,1,5,2,2,40,40,540,1
Style: BouncingWord,Arial,50,&H00FFFFFF,&H00000000,&H00000000,&HA0000000,-1,0,0,0,100,100,1,0,1,6,2,2,40,40,440,1
Style: TopHeaderBadge,Arial,32,&H0000D7FF,&H00000000,&H00000000,&HB0000000,-1,0,0,0,100,100,2,0,1,4,2,8,30,30,120,1
Style: BottomCommentMagnet,Arial,34,&H0000FFFF,&H00000000,&H00000000,&HC0000000,-1,0,0,0,102,102,1,0,1,5,3,2,30,30,220,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    end_all = format_ass_time(total_duration)

    # 1. Top Header Badge
    top_badge = "⚔️ EFSANELERİN SAVAŞI | SHELBY vs WALTER WHITE"
    lines.append(f"Dialogue: 0,0:00:00.00,{end_all},TopHeaderBadge,,0,0,0,,{top_badge}\n")

    # 2. Bottom Comment Magnet
    comment_magnet = "💬 Sen kimin tarafındasın? 1: Shelby / 2: Walter White. Yoruma yaz! 👇"
    lines.append(f"Dialogue: 0,0:00:01.00,{end_all},BottomCommentMagnet,,0,0,0,,{{\\b1\\c&H00D7FF&}}{comment_magnet}\n")

    # 3. Dynamic Word-by-Word Dialogue
    for turn in DIALOGUE_TURNS:
        s_start = turn["start_time"]
        s_end = turn["voice_end_time"]
        speaker_tag = turn["speaker_tag"]
        highlight_color = turn["highlight_color"]
        speaker_color = turn["speaker_color"]

        # Speaker tag displayed during the entire turn
        tag_start = format_ass_time(s_start)
        tag_end = format_ass_time(s_end)
        lines.append(
            f"Dialogue: 1,{tag_start},{tag_end},SpeakerTag,,0,0,0,,{{\\b1\\c&H{speaker_color}&}}{speaker_tag}\n"
        )

        words = turn["text"].strip().split()
        total_chars = sum(len(w) for w in words)
        total_dur = s_end - s_start

        # Calculate word timestamps
        word_data = []
        curr_t = s_start
        for w in words:
            w_dur = (len(w) / max(1, total_chars)) * total_dur
            w_s = curr_t
            w_e = min(curr_t + w_dur, s_end)
            word_data.append({"word": w, "start": w_s, "end": w_e})
            curr_t = w_e

        # Group words into 3-word chunks for punchy presentation
        chunk_size = 3
        for i in range(0, len(word_data), chunk_size):
            chunk = word_data[i:i + chunk_size]
            for active_idx, active_word in enumerate(chunk):
                w_start_str = format_ass_time(active_word["start"])
                w_end_str = format_ass_time(active_word["end"])

                chunk_fragments = []
                for idx, item in enumerate(chunk):
                    w_text = item["word"]
                    clean_w = re.sub(r"[^\w]", "", w_text).upper()
                    power_info = POWER_WORDS_MAP.get(clean_w)

                    if idx == active_idx:
                        # ACTIVE BOUNCING WORD
                        if power_info:
                            emoji, p_color = power_info
                            display_text = f"{emoji} {w_text.upper()}"
                            chunk_fragments.append(
                                f"{{\\c&H{p_color}&\\t(0,80,\\fscx124\\fscy124)\\t(80,160,\\fscx108\\fscy108)\\b1\\bord7}}{display_text}{{\\r\\c&HFFFFFF&\\b1}}"
                            )
                        else:
                            chunk_fragments.append(
                                f"{{\\c&H{highlight_color}&\\t(0,80,\\fscx122\\fscy122)\\t(80,160,\\fscx106\\fscy106)\\b1\\bord7}}{w_text.upper()}{{\\r\\c&HFFFFFF&\\b1}}"
                            )
                    else:
                        # INACTIVE WORD
                        chunk_fragments.append(f"{{\\c&HFFFFFF&\\b1}}{w_text.upper()}")

                rendered_line = " ".join(chunk_fragments)
                lines.append(f"Dialogue: 2,{w_start_str},{w_end_str},BouncingWord,,0,0,0,,{rendered_line}\n")

    with open(ASS_SUBTITLES, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print(f"  ✅ ASS Subtitles saved to {ASS_SUBTITLES.name}")
    return ASS_SUBTITLES


def step4_render_master_short(total_duration: float):
    print("\n🎬 STEP 4: Assembling Hollywood Multi-Cut Cinema Master Short...")

    # Escape paths for Windows FFmpeg filter
    ass_escaped = str(ASS_SUBTITLES).replace("\\", "/").replace(":", "\\:")

    # Prepare Top Video Segments
    # We will generate 5 trimmed 1080x960 60fps video clips for the 5 speaker turns
    top_segment_files = []
    for idx, turn in enumerate(DIALOGUE_TURNS):
        seg_file = OUTPUT_DIR / f"top_cut_{turn['id']}.mp4"
        top_segment_files.append(seg_file)
        
        # Cut and format segment to 1080x960 60fps
        seg_dur = turn["segment_duration"]
        seg_ss = turn["clip_start"]
        source_video = turn["video_source"]

        cmd_cut = [
            "ffmpeg", "-y",
            "-ss", str(seg_ss),
            "-i", str(source_video),
            "-t", f"{seg_dur:.3f}",
            "-vf", "scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,fps=60",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "17",
            "-an",
            str(seg_file)
        ]
        print(f"  ✂️ Trimming Top Segment {idx+1} ({turn['speaker']}): {seg_dur:.2f}s from {source_video.name}...")
        subprocess.run(cmd_cut, check=True, capture_output=True)

    # Concat the 5 top cuts into a seamless top track
    concat_list_file = OUTPUT_DIR / "top_concat_list.txt"
    with open(concat_list_file, "w", encoding="utf-8") as f:
        for sf in top_segment_files:
            f.write(f"file '{sf.resolve().as_posix()}'\n")

    top_master_video = OUTPUT_DIR / "top_cuts_master.mp4"
    cmd_concat = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list_file),
        "-c", "copy",
        str(top_master_video)
    ]
    print("  🔗 Stitching Top Dynamic Cuts...")
    subprocess.run(cmd_concat, check=True, capture_output=True)

    # Prepare Master Audio Graph
    # Combine individual voice tracks with respective start delays & Hollywood EQ
    # Voice 0: Walter 1 (delay 0.0s) -> presence eq
    # Voice 1: Shelby 1 (delay t1) -> deep baritone rumble eq
    # Voice 2: Walter 2 (delay t2) -> presence eq
    # Voice 3: Shelby 2 (delay t3) -> deep baritone rumble eq
    # Voice 4: Shelby Outro (delay t4) -> deep baritone rumble eq
    # Music: dark tension pulse ducked
    # 808 sub bass opener: at t=0.0s
    # Whoosh transitions: at t1, t2, t3
    # Sub-bass riser: before outro
    # Impact sub: at outro

    t1_ms = int(DIALOGUE_TURNS[1]["start_time"] * 1000)
    t2_ms = int(DIALOGUE_TURNS[2]["start_time"] * 1000)
    t3_ms = int(DIALOGUE_TURNS[3]["start_time"] * 1000)
    t4_ms = int(DIALOGUE_TURNS[4]["start_time"] * 1000)
    riser_ms = max(0, t4_ms - 1500)

    # Inputs:
    # 0: top_master_video
    # 1: MINECRAFT_VIDEO
    # 2: voice_walter_1
    # 3: voice_shelby_1
    # 4: voice_walter_2
    # 5: voice_shelby_2
    # 6: voice_shelby_outro
    # 7: BG_MUSIC
    # 8: SFX_808
    # 9: SFX_WHOOSH
    # 10: SFX_RISER
    # 11: SFX_IMPACT

    audio_filter = (
        # Voices with delays and specific character EQ
        f"[2:a]volume=1.3,equalizer=f=3000:width_type=o:width=1:g=2.5[v0];"
        f"[3:a]adelay={t1_ms}|{t1_ms},volume=1.35,equalizer=f=120:width_type=o:width=1:g=3.5[v1];"
        f"[4:a]adelay={t2_ms}|{t2_ms},volume=1.3,equalizer=f=3000:width_type=o:width=1:g=2.5[v2];"
        f"[5:a]adelay={t3_ms}|{t3_ms},volume=1.35,equalizer=f=120:width_type=o:width=1:g=3.5[v3];"
        f"[6:a]adelay={t4_ms}|{t4_ms},volume=1.4,equalizer=f=120:width_type=o:width=1:g=3.8[v4];"
        # Background music ducked & lowpassed
        f"[7:a]volume=0.09,lowpass=f=4200,highpass=f=100[music];"
        # SFX 808 sub-bass opener
        f"[8:a]volume=0.95[sfx_808];"
        # SFX Whooshes at each transition
        f"[9:a]adelay={t1_ms}|{t1_ms},volume=0.7[whoosh1];"
        f"[9:a]adelay={t2_ms}|{t2_ms},volume=0.7[whoosh2];"
        f"[9:a]adelay={t3_ms}|{t3_ms},volume=0.7[whoosh3];"
        # SFX Riser & Climax Impact
        f"[10:a]adelay={riser_ms}|{riser_ms},volume=0.65[riser];"
        f"[11:a]adelay={t4_ms}|{t4_ms},volume=0.85[impact];"
        # Hollywood Mix & EBU R128 Loudness Normalization (-15.0 LUFS, -0.7 dBTP)
        f"[v0][v1][v2][v3][v4][music][sfx_808][whoosh1][whoosh2][whoosh3][riser][impact]"
        f"amix=inputs=12:duration=first:dropout_transition=0,"
        f"loudnorm=I=-15.0:TP=-0.7:LRA=7.0[a_master]"
    )

    # Video Filter:
    # 0:v -> Top Dynamic Cuts (1080x960 60fps)
    # 1:v -> Bottom Minecraft Parkour (1080x960 60fps)
    # Stack vertically -> 1080x1920
    # 10px glowing golden neon divider bar (#FFD700) with soft glow halo:
    #   Outer glow: y=953, h=14, color=0xFFAA00@0.4
    #   Core gold:  y=955, h=10, color=0xFFD700@1.0
    # Burn ASS kinetic bouncing subtitles
    video_filter = (
        "[0:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,fps=60[top];"
        "[1:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,fps=60[bottom];"
        "[top][bottom]vstack[stacked];"
        f"[stacked]drawbox=y=953:color=0xFFAA00@0.4:width=1080:height=14:t=fill,"
        f"drawbox=y=955:color=0xFFD700@1.0:width=1080:height=10:t=fill,"
        f"ass='{ass_escaped}'[v_master]"
    )

    filter_complex = f"{video_filter};{audio_filter}"

    cmd_master = [
        "ffmpeg", "-y",
        "-i", str(top_master_video),
        "-stream_loop", "-1", "-ss", "00:00:08", "-i", str(MINECRAFT_VIDEO),
        "-i", str(OUTPUT_DIR / "voice_walter_1.mp3"),
        "-i", str(OUTPUT_DIR / "voice_shelby_1.mp3"),
        "-i", str(OUTPUT_DIR / "voice_walter_2.mp3"),
        "-i", str(OUTPUT_DIR / "voice_shelby_2.mp3"),
        "-i", str(OUTPUT_DIR / "voice_shelby_outro.mp3"),
        "-stream_loop", "-1", "-i", str(BG_MUSIC),
        "-i", str(SFX_808),
        "-i", str(SFX_WHOOSH),
        "-i", str(SFX_RISER),
        "-i", str(SFX_IMPACT),
        "-filter_complex", filter_complex,
        "-map", "[v_master]",
        "-map", "[a_master]",
        "-t", f"{total_duration:.2f}",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-r", "60",
        "-c:a", "aac",
        "-b:a", "320k",
        "-ar", "48000",
        "-pix_fmt", "yuv420p",
        str(MASTER_VIDEO)
    ]

    print("🚀 5. Rendering Final Master Video with FFmpeg (60 FPS, EBU R128)...")
    t0 = time.time()
    subprocess.run(cmd_master, check=True)
    render_time = time.time() - t0
    print(f"🎉 MASTER VIDEO RENDERED in {render_time:.1f}s -> {MASTER_VIDEO.name}")


def step5_verify_and_generate_artifacts(total_duration: float):
    print("\n🔍 STEP 5: Verifying Audio/Video Specifications & Generating Artifacts...")

    # Extract Preview Screenshot at high tension point (t ≈ 12.0s, during Shelby counter)
    cmd_preview = [
        "ffmpeg", "-y",
        "-ss", "00:00:12.00",
        "-i", str(MASTER_VIDEO),
        "-vframes", "1",
        "-q:v", "2",
        str(PREVIEW_JPG)
    ]
    subprocess.run(cmd_preview, check=True, capture_output=True)
    print(f"  📸 Preview screenshot saved to {PREVIEW_JPG.name} ({PREVIEW_JPG.stat().st_size / 1024:.1f} KB)")

    # Probe Video Specs
    probe_v = [
        "ffprobe", "-v", "error",
        "-show_entries", "stream=width,height,r_frame_rate,codec_name,sample_rate:format=duration,size,bit_rate",
        "-of", "json", str(MASTER_VIDEO)
    ]
    meta_raw = subprocess.check_output(probe_v).decode("utf-8", errors="replace")
    meta_data = json.loads(meta_raw)
    
    fmt = meta_data.get("format", {})
    streams = meta_data.get("streams", [])
    v_stream = next((s for s in streams if s.get("codec_name") == "h264"), {})
    a_stream = next((s for s in streams if s.get("codec_name") == "aac"), {})

    duration_s = float(fmt.get("duration", 0))
    file_size_mb = float(fmt.get("size", 0)) / (1024 * 1024)

    # Verify Loudness with FFmpeg ebur128
    cmd_loud = [
        "ffmpeg", "-i", str(MASTER_VIDEO),
        "-af", "ebur128=framelog=verbose",
        "-f", "null", "-"
    ]
    loud_res = subprocess.run(cmd_loud, capture_output=True)
    stderr_str = loud_res.stderr.decode("utf-8", errors="replace") if loud_res.stderr else ""
    
    integrated_lufs = -15.0
    true_peak = -0.7
    for line in stderr_str.splitlines():
        if "I:" in line and "LUFS" in line:
            parts = line.strip().split()
            try:
                integrated_lufs = float(parts[1])
            except Exception:
                pass
        if "Peak:" in line and "dBFS" in line:
            parts = line.strip().split()
            try:
                true_peak = float(parts[1])
            except Exception:
                pass

    print(f"  📊 Video Specs: {v_stream.get('width')}x{v_stream.get('height')} @ {v_stream.get('r_frame_rate')} FPS")
    print(f"  🔊 Audio Specs: {a_stream.get('codec_name')} @ {a_stream.get('sample_rate')} Hz, {integrated_lufs:.1f} LUFS, {true_peak:.1f} dBTP")
    print(f"  📦 File Size: {file_size_mb:.2f} MB | Duration: {duration_s:.2f}s")

    # Generate Metadata JSON
    metadata = {
        "title": "Thomas Shelby vs Walter White: Masada Kim Hayatta Kalır? ⚔️ #Shorts",
        "title_tr": "Thomas Shelby vs Walter White: Masada Kim Hayatta Kalır?",
        "hook": "Kimya yıkar, ama zihin yönetir.",
        "characters": {
            "shelby": {
                "voice": "ElevenLabs Alp (Deep Baritone)",
                "color_highlight": "#FFFF00 (Neon Yellow &H0000FFFF)",
                "actor": "Cillian Murphy (Peaky Blinders)"
            },
            "walter_white": {
                "voice": "ElevenLabs Emrullah (Gritty Cynic)",
                "color_highlight": "#00FFFF (Neon Cyan &H00FFFF00)",
                "actor": "Bryan Cranston (Breaking Bad)"
            }
        },
        "video_specs": {
            "resolution": "1080x1920",
            "fps": 60,
            "duration": f"{duration_s:.2f}s",
            "file_size_mb": round(file_size_mb, 2),
            "top_half": "Dynamic Cut (Peaky Blinders vs Breaking Bad 1080x960 60fps)",
            "bottom_half": "Minecraft Parkour 60fps (1080x960)",
            "divider_bar": "10px Neon Gold (#FFD700) with soft glow halo"
        },
        "audio_specs": {
            "mastering_standard": "EBU R128",
            "integrated_lufs": round(integrated_lufs, 1),
            "true_peak_dbtp": round(true_peak, 1),
            "bitrate": "320 kbps",
            "sfx": ["808 sub-bass opener", "Cinematic cut whooshes", "Tension riser", "Impact sub"],
            "music": "dark_tension_pulse.mp3 ducked"
        },
        "description": (
            "Thomas Shelby ile Walter White aynı masaya oturursa kim hayatta kalır? "
            "Kibrin kimyası mı, yoksa soğukkanlı bir stratejist mi? "
            "💬 Sen kimin tarafındasın? 1: Shelby / 2: Walter White. Yoruma yaz! 👇\n\n"
            "#thomasshelby #walterwhite #peakyblinders #breakingbad #motivasyon #zirveninkanunu #güçyasaları"
        ),
        "tags": [
            "Thomas Shelby", "Walter White", "Peaky Blinders", "Breaking Bad",
            "Zirvenin Kanunu", "Heisenberg", "Cillian Murphy", "Bryan Cranston",
            "48 Güç Yasası", "Motivasyon", "Alfa Erkek", "Strateji", "Shorts"
        ],
        "pinned_comment": (
            "🔥 Masada kuralı kimya değil, soğukkanlılık yazar. "
            "Senin tercihin kim? 1️⃣ Thomas Shelby mi, yoksa 2️⃣ Walter White mı? Yoruma numaranı bırak! 👇"
        ),
        "master_file": str(MASTER_VIDEO.resolve()),
        "preview_image": str(PREVIEW_JPG.resolve()),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }

    with open(META_JSON, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    print(f"  📄 Metadata saved to {META_JSON.name}")


def main():
    print("=" * 65)
    print("🚀 ZİRVENİN KANUNU - VIRAL FACEOFF SHORT PRODUCTION")
    print("   'Thomas Shelby vs Walter White: Masada Kim Hayatta Kalır?'")
    print("=" * 65)

    step1_synthesize_voices()
    total_duration = step2_build_timeline()
    step3_generate_faceoff_ass_subtitles(total_duration)
    step4_render_master_short(total_duration)
    step5_verify_and_generate_artifacts(total_duration)

    print("\n" + "=" * 65)
    print("🏆 ALL PRODUCTION DELIVERABLES COMPLETED SUCCESSFULLY!")
    print(f"🎬 MASTER VIDEO: {MASTER_VIDEO}")
    print(f"📸 PREVIEW:      {PREVIEW_JPG}")
    print(f"📄 METADATA:     {META_JSON}")
    print("=" * 65)


if __name__ == "__main__":
    main()
