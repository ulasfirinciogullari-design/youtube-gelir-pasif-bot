"""
Turn-Based Reaction & Dialogue Balancing Engine for 'Zirvenin Kanunu'.

Core Architectural Rule (Sıralı Konuşma / Diyalog Dengesi):
1. ÖNCE KLİP KONUŞUR (Clip Turn):
   - Clip audio plays at 100% full volume with speech presence EQ.
   - Narrator / voiceover is 100% SILENT (0.0 volume). Zero vocal collision.
   - Subtitles display the clip's iconic lines verbatim.
2. SONRA YORUM SESLENDİRİLİR (Commentary Turn):
   - Narrator speaks only AFTER the clip's lines conclude.
   - Clip audio is ducked or muted so clip and narrator never speak over each other.
   - Subtitles switch to the narrator's witty reaction & call-to-action.
   - Comment cards appear in sync with the reaction.
3. Hollywood Audio Mastering:
   - Whisper-quiet background music (-24 dB), sidechain ducked.
   - Subtle SFX pops (volume=0.18).
   - EBU R128 broadcast loudness compliance (-14.0 LUFS, -0.7 dBTP).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = BASE_DIR / "output"
ASSETS_DIR = BASE_DIR / "assets"
MUSIC_DIR = ASSETS_DIR / "music"
SFX_DIR = ASSETS_DIR / "sfx"


@dataclass
class DialogueTurnConfig:
    # Media paths
    video_path: Path
    commentary_voice_path: Path
    output_audio_master: Path
    music_track: Path = field(default_factory=lambda: MUSIC_DIR / "quirky_comedy_groove.mp3")
    sfx_pop: Path = field(default_factory=lambda: SFX_DIR / "ui_pop.mp3")
    
    # Timing Windows (Turn-Taking)
    clip_dialogue_end_sec: float = 14.3  # Exact second where clip dialogue finishes
    commentary_start_sec: float = 14.3   # Exact second where voiceover begins
    
    # Levels
    music_volume: float = 0.016          # Whisper-quiet comedy groove (-24 dB)
    sfx_volume: float = 0.18             # Gentle UI pops
    target_lufs: float = -14.0
    target_tp: float = -0.7
    target_lra: float = 6.0


class TurnBasedReactionEngine:
    """
    Builds bulletproof multi-track audio mixes where clip dialogue and
    narration NEVER overlap or compete.
    """

    def __init__(self, config: DialogueTurnConfig):
        self.config = config
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def probe_duration(file_path: Path) -> float:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(file_path)
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(res.stdout.strip())

    def build_turn_based_audio_master(self) -> Path:
        """
        Mixes clip audio and commentary with absolute temporal separation.
        """
        total_duration = self.probe_duration(self.config.video_path)
        vo_duration = self.probe_duration(self.config.commentary_voice_path)
        
        clip_end = self.config.clip_dialogue_end_sec
        vo_start = self.config.commentary_start_sec
        vo_start_ms = int(vo_start * 1000)

        print("🎛️ === TURN-BASED REACTION AUDIO COMPOSITION ===")
        print(f"🎬 1. KLİP KONUŞMA ARALIĞI: 0.0s -> {clip_end:.2f}s (Dış ses: TAMAMEN SESSİZ)")
        print(f"🎙️ 2. YORUM KONUŞMA ARALIĞI: {vo_start:.2f}s -> {vo_start + vo_duration:.2f}s (Klip diyaloğu çakışması: SIFIR)")
        print(f"⏱️ Toplam Sahne Süresi: {total_duration:.2f}s")

        temp_premaster = OUTPUT_DIR / "turnbased_premaster_stage1.wav"

        # FFmpeg Filter Graph:
        # [0:a] = Clip audio
        # [1:a] = Commentary voice (Alp)
        # [2:a] = Background comedy music
        # [3:a] = SFX Pop
        cmd_inputs = [
            "ffmpeg", "-y",
            "-i", str(self.config.video_path),
            "-i", str(self.config.commentary_voice_path),
            "-stream_loop", "-1", "-i", str(self.config.music_track),
            "-i", str(self.config.sfx_pop)
        ]

        filter_chains = []

        # 1. Clip Audio Track:
        # Full volume and dialogue presence EQ from 0s to clip_end.
        # After clip_end, ducked by 26dB so it never interferes with the commentary.
        filter_chains.append(
            f"[0:a]highpass=f=85,"
            f"equalizer=f=3000:t=q:w=1.2:g=3.5,"
            f"equalizer=f=5200:t=q:w=1.0:g=2.2,"
            f"volume=enable='between(t,0,{clip_end:.2f})':volume=1.0,"
            f"volume=enable='gte(t,{clip_end:.2f})':volume=0.08[clip_cleaned]"
        )

        # 2. Voiceover Track:
        # Delayed to start strictly at vo_start_ms.
        # Before vo_start_ms, it is 100% silent.
        filter_chains.append(
            f"[1:a]adelay={vo_start_ms}|{vo_start_ms},"
            f"volume=2.0,highpass=f=80,equalizer=f=3200:t=q:w=1.2:g=2.5,asplit=2[vo_main][vo_sc]"
        )

        # 3. Background Music Track:
        # Calibrated whisper-quiet, sidechain ducked under both clip and voiceover.
        filter_chains.append(
            f"[2:a]aformat=channel_layouts=stereo,volume={self.config.music_volume:.4f},"
            f"lowpass=f=2600,highpass=f=80,atrim=0:{total_duration:.2f},asetpts=PTS-STARTPTS[m_raw];"
            f"[m_raw][clip_cleaned]sidechaincompress=threshold=0.03:ratio=6:attack=15:release=300[m_duck1];"
            f"[m_duck1][vo_sc]sidechaincompress=threshold=0.03:ratio=10:attack=15:release=300[m_ducked]"
        )

        # 4. Subtle SFX Pops timed to comment card reveals:
        # 4.5s (card 1), 9.5s (card 2), 14.5s (card 3 & reaction punch), 19.0s (card 4)
        filter_chains.append(f"[3:a]adelay=4500|4500,volume={self.config.sfx_volume:.2f}[sfx1]")
        filter_chains.append(f"[3:a]adelay=9500|9500,volume={self.config.sfx_volume:.2f}[sfx2]")
        filter_chains.append(f"[3:a]adelay=14500|14500,volume={self.config.sfx_volume:.2f}[sfx3]")
        filter_chains.append(f"[3:a]adelay=19000|19000,volume={self.config.sfx_volume:.2f}[sfx4]")

        # 5. Mix all stems:
        filter_chains.append(
            "[clip_cleaned][vo_main][m_ducked][sfx1][sfx2][sfx3][sfx4]"
            "amix=inputs=7:duration=first:dropout_transition=0[pre_master]"
        )

        filter_complex = ";".join(filter_chains)

        cmd_stage1 = cmd_inputs + [
            "-filter_complex", filter_complex,
            "-map", "[pre_master]",
            "-t", f"{total_duration:.2f}",
            "-c:a", "pcm_s16le",
            "-ar", "48000",
            str(temp_premaster)
        ]

        print("🎚️ 1. Aşama: Kanallar çakışmasız olarak birleştiriliyor...")
        subprocess.run(cmd_stage1, check=True)

        # Two-pass EBU R128 Mastering
        print("📊 2. Aşama: EBU R128 yayın standartlarına göre mastering uygulanıyor...")
        cmd_pass1 = [
            "ffmpeg", "-i", str(temp_premaster),
            "-af", f"loudnorm=I={self.config.target_lufs}:TP={self.config.target_tp}:LRA={self.config.target_lra}:print_format=json",
            "-f", "null", "-"
        ]
        res_pass1 = subprocess.run(
            cmd_pass1, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True
        )
        stderr_pass1 = res_pass1.stderr
        json_start = stderr_pass1.rfind("{")
        json_end = stderr_pass1.rfind("}") + 1
        stats = json.loads(stderr_pass1[json_start:json_end])

        measured_i = stats.get("input_i", "-14.0")
        measured_tp = stats.get("input_tp", "-0.7")
        measured_lra = stats.get("input_lra", "6.0")
        measured_thresh = stats.get("input_thresh", "-24.0")
        offset = stats.get("target_offset", "0.0")

        loudnorm_pass2 = (
            f"loudnorm=I={self.config.target_lufs}:TP={self.config.target_tp}:LRA={self.config.target_lra}:"
            f"measured_I={measured_i}:measured_TP={measured_tp}:"
            f"measured_LRA={measured_lra}:measured_thresh={measured_thresh}:"
            f"offset={offset}:linear=true"
        )

        cmd_pass2 = [
            "ffmpeg", "-y",
            "-i", str(temp_premaster),
            "-af", loudnorm_pass2,
            "-c:a", "libmp3lame",
            "-b:a", "320k",
            "-ar", "48000",
            str(self.config.output_audio_master)
        ]
        subprocess.run(cmd_pass2, check=True)

        if temp_premaster.exists():
            temp_premaster.unlink(missing_ok=True)

        print(f"🏆 SIRALI DİYALOG MASTER SESİ HAZIR: {self.config.output_audio_master.name}")
        return self.config.output_audio_master
