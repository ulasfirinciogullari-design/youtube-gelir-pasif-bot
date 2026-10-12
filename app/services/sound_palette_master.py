"""
Sound Palette Master for 'Zirvenin Kanunu' (@zirveninkanunu).
Provides broadcast-grade music mood retrieval and one-line viral SFX delay filters
for FFmpeg multi-track audio graphs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import List, Tuple, Dict, Any, Optional, Union

BASE_DIR = Path(__file__).resolve().parent.parent.parent
MUSIC_DIR = BASE_DIR / "assets" / "music"
SFX_DIR = BASE_DIR / "assets" / "sfx"

# ---------------------------------------------------------
# Music Mood Palette
# ---------------------------------------------------------
PRIMARY_MOOD_MAP: Dict[str, str] = {
    "phonk": "brazilian_phonk_energy.mp3",
    "tension": "dramatic_suspense_strings.mp3",
    "cyberpunk": "cyberpunk_trap_dark.mp3",
    "comedy": "quirky_comedy_groove.mp3",
    "chill": "lofi_midnight_chill.mp3",
}

MUSIC_ALIASES: Dict[str, str] = {
    # Phonk variants
    "drift": "phonk",
    "drift_phonk": "phonk",
    "brazilian": "phonk",
    "brazilian_phonk": "phonk",
    "aggressive": "phonk",
    "hype": "phonk",
    "gym": "phonk",
    # Tension / Suspense variants
    "suspense": "tension",
    "strings": "tension",
    "violin": "tension",
    "hans_zimmer": "tension",
    "zimmer": "tension",
    "dramatic": "tension",
    "thriller": "tension",
    # Cyberpunk variants
    "trap": "cyberpunk",
    "cyber": "cyberpunk",
    "dark": "cyberpunk",
    "dark_trap": "cyberpunk",
    "future": "cyberpunk",
    "dystopia": "cyberpunk",
    # Comedy variants
    "quirky": "comedy",
    "witty": "comedy",
    "humor": "comedy",
    "funny": "comedy",
    "groove": "comedy",
    "meme": "comedy",
    # Chill variants
    "lofi": "chill",
    "lo-fi": "chill",
    "focus": "chill",
    "midnight": "chill",
    "study": "chill",
    "relax": "chill",
    # Additional channel library
    "mystery": "mystery_documentary.mp3",
    "documentary": "mystery_documentary.mp3",
    "ambient": "cinematic_ambient_drive.mp3",
    "drive": "cinematic_ambient_drive.mp3",
    "epic": "epic_hollywood_braam.mp3",
    "braam": "epic_hollywood_braam.mp3",
    "synthwave": "synthwave_cyber_drive.mp3",
    "retro": "retro_game_chiptune.mp3",
    "chiptune": "retro_game_chiptune.mp3",
    "arcade": "retro_game_chiptune.mp3",
    "electronic": "electronic_high_energy.mp3",
    "energy": "electronic_high_energy.mp3",
    "dark_pulse": "dark_tension_pulse.mp3",
    "pulse": "dark_tension_pulse.mp3",
}

# ---------------------------------------------------------
# Viral SFX Palette
# ---------------------------------------------------------
PRIMARY_SFX_MAP: Dict[str, str] = {
    "vine_boom": "vine_boom.mp3",
    "cash_register": "cash_register.mp3",
    "record_scratch": "record_scratch.mp3",
    "ui_pop": "ui_pop.mp3",
    "error_buzzer": "error_buzzer.mp3",
    "dramatic_screech": "dramatic_screech.mp3",
    # Broadcast cinematic effects
    "cinematic_whoosh": "cinematic_whoosh.mp3",
    "sub_boom_808": "sub_boom_808.mp3",
    "tension_riser": "tension_riser.mp3",
    "impact_sub": "impact_sub.mp3",
    "riser_cinematic": "riser_cinematic.mp3",
    "whoosh_synth": "whoosh_synth.mp3",
}

SFX_ALIASES: Dict[str, str] = {
    "boom": "vine_boom",
    "sub_thud": "vine_boom",
    "thud": "vine_boom",
    "vine": "vine_boom",
    "kaching": "cash_register",
    "cha_ching": "cash_register",
    "chaching": "cash_register",
    "cash": "cash_register",
    "money": "cash_register",
    "coins": "cash_register",
    "scratch": "record_scratch",
    "disc_scratch": "record_scratch",
    "vinyl_scratch": "record_scratch",
    "pop": "ui_pop",
    "bubble": "ui_pop",
    "bubble_pop": "ui_pop",
    "click": "ui_pop",
    "bubble_click": "ui_pop",
    "buzzer": "error_buzzer",
    "error": "error_buzzer",
    "wrong": "error_buzzer",
    "wrong_answer": "error_buzzer",
    "fail": "error_buzzer",
    "screech": "dramatic_screech",
    "psycho": "dramatic_screech",
    "violin_screech": "dramatic_screech",
    "discord": "dramatic_screech",
    "horror": "dramatic_screech",
    "whoosh": "cinematic_whoosh",
    "swoosh": "cinematic_whoosh",
    "riser": "tension_riser",
    "808": "sub_boom_808",
    "impact": "impact_sub",
}


def get_music_by_mood(mood: str = "phonk") -> Path:
    """
    Returns the absolute path to a background music track based on mood.
    Supported moods: 'phonk', 'tension', 'cyberpunk', 'comedy', 'chill' (and aliases).
    Falls back to brazilian_phonk_energy.mp3 if unknown.
    """
    cleaned = mood.strip().lower().replace(" ", "_").replace("-", "_")
    
    # Resolve alias or primary
    if cleaned in PRIMARY_MOOD_MAP:
        track_filename = PRIMARY_MOOD_MAP[cleaned]
    elif cleaned in MUSIC_ALIASES:
        resolved = MUSIC_ALIASES[cleaned]
        track_filename = PRIMARY_MOOD_MAP.get(resolved, resolved)
    else:
        # Fallback search by substring
        match = None
        for key in PRIMARY_MOOD_MAP:
            if key in cleaned:
                match = PRIMARY_MOOD_MAP[key]
                break
        track_filename = match or PRIMARY_MOOD_MAP["phonk"]

    track_path = MUSIC_DIR / track_filename
    if not track_path.exists():
        # Fallback to any existing mp3
        available = list(MUSIC_DIR.glob("*.mp3"))
        if available:
            return available[0]
        raise FileNotFoundError(f"No music files found in {MUSIC_DIR}")
    return track_path


def list_music_moods() -> Dict[str, str]:
    """Returns mapping of available music moods to track filenames."""
    result = {}
    for mood, filename in PRIMARY_MOOD_MAP.items():
        p = MUSIC_DIR / filename
        result[mood] = str(p) if p.exists() else f"[MISSING] {filename}"
    return result


def get_sfx_path(sfx_name: str) -> Path:
    """
    Returns the absolute path to a viral SFX file by name or alias.
    """
    cleaned = sfx_name.strip().lower().replace(" ", "_").replace("-", "_")
    
    if cleaned in PRIMARY_SFX_MAP:
        filename = PRIMARY_SFX_MAP[cleaned]
    elif cleaned in SFX_ALIASES:
        canonical = SFX_ALIASES[cleaned]
        filename = PRIMARY_SFX_MAP[canonical]
    else:
        # Substring search
        match = None
        for key in PRIMARY_SFX_MAP:
            if key in cleaned:
                match = PRIMARY_SFX_MAP[key]
                break
        filename = match or PRIMARY_SFX_MAP["vine_boom"]

    sfx_path = SFX_DIR / filename
    if not sfx_path.exists():
        available = list(SFX_DIR.glob("*.mp3"))
        if available:
            return available[0]
        raise FileNotFoundError(f"No SFX files found in {SFX_DIR}")
    return sfx_path


def list_sfx() -> Dict[str, str]:
    """Returns mapping of available viral SFX to file paths."""
    result = {}
    for sfx_key, filename in PRIMARY_SFX_MAP.items():
        p = SFX_DIR / filename
        result[sfx_key] = str(p) if p.exists() else f"[MISSING] {filename}"
    return result


# ---------------------------------------------------------
# FFmpeg SFX Insertion & Multi-Track Mix Graph
# ---------------------------------------------------------
@dataclass
class SFXFilterSpec:
    """
    Encapsulates an FFmpeg SFX delay and volume filter specification.
    Stringifying returns the audio filter graph node expression.
    """
    sfx_name: str
    sfx_path: Path
    at_seconds: float
    volume: float
    input_idx: int
    out_label: str

    @property
    def delay_ms(self) -> int:
        return max(0, int(round(self.at_seconds * 1000)))

    @property
    def filter_string(self) -> str:
        """
        Generates robust stereo delay filter for FFmpeg:
        [input_idx:a]aformat=channel_layouts=stereo,volume={vol:.2f},adelay={ms}|{ms}[out_label]
        """
        ms = self.delay_ms
        vol = max(0.01, min(self.volume, 5.0))
        return (
            f"[{self.input_idx}:a]aformat=channel_layouts=stereo,"
            f"volume={vol:.2f},adelay={ms}|{ms}[{self.out_label}]"
        )

    def __str__(self) -> str:
        return self.filter_string

    @property
    def input_args(self) -> List[str]:
        return ["-i", str(self.sfx_path)]


def insert_sfx(
    sfx_name: str,
    at_seconds: float,
    volume: float = 1.0,
    input_idx: int = 1,
    out_label: Optional[str] = None,
) -> SFXFilterSpec:
    """
    One-line SFX delay filter generator for FFmpeg audio mix graphs.
    Example:
        spec = insert_sfx('vine_boom', at_seconds=4.2)
        # str(spec) -> '[1:a]aformat=channel_layouts=stereo,volume=1.00,adelay=4200|4200[sfx_1]'
    """
    path = get_sfx_path(sfx_name)
    label = out_label or f"sfx_{input_idx}"
    return SFXFilterSpec(
        sfx_name=sfx_name,
        sfx_path=path,
        at_seconds=float(at_seconds),
        volume=float(volume),
        input_idx=int(input_idx),
        out_label=label,
    )


def build_audio_mix_command(
    voice_path: Path,
    output_path: Path,
    sfx_cues: Optional[List[Tuple[str, float]]] = None,
    mood: str = "phonk",
    music_volume: float = 0.12,
    music_track: Optional[Path] = None,
    total_duration: Optional[float] = None,
) -> List[str]:
    """
    Assembles a complete FFmpeg command array to mix:
      - 0: Voice narration (base level 1.0)
      - 1: Looped background music (ducked volume, end fade-out)
      - 2..N: Sound effect cues inserted at specified timestamps via adelay.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    bg_music = music_track or get_music_by_mood(mood)
    
    # Probe duration if not passed
    dur = total_duration
    if dur is None:
        try:
            out = subprocess.check_output([
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(voice_path)
            ], text=True).strip()
            dur = float(out)
        except Exception:
            dur = 30.0

    # Build inputs and filter graph
    cmd_inputs: List[str] = ["ffmpeg", "-y", "-i", str(voice_path)]
    filter_chains: List[str] = ["[0:a]aformat=channel_layouts=stereo,volume=1.0[v]"]
    mix_labels: List[str] = ["[v]"]
    
    current_idx = 1
    # 1. Background Music input (stream_loop -1)
    if bg_music and bg_music.exists():
        cmd_inputs.extend(["-stream_loop", "-1", "-i", str(bg_music)])
        fade_start = max(1.0, dur - 2.0)
        vol = max(0.02, min(music_volume, 0.5))
        filter_chains.append(
            f"[{current_idx}:a]aformat=channel_layouts=stereo,"
            f"volume={vol:.2f},atrim=0:{dur:.2f},asetpts=N/SR/TB,"
            f"afade=t=in:st=0:d=0.5,afade=t=out:st={fade_start:.2f}:d=2.0[m]"
        )
        mix_labels.append("[m]")
        current_idx += 1

    # 2. SFX Cues
    if sfx_cues:
        for cue in sfx_cues:
            if isinstance(cue, (tuple, list)):
                sfx_name, at_sec = cue[0], cue[1]
                vol = cue[2] if len(cue) > 2 else 1.0
            elif isinstance(cue, dict):
                sfx_name = cue.get("name", "vine_boom")
                at_sec = cue.get("at_seconds", 0.0)
                vol = cue.get("volume", 1.0)
            else:
                continue

            spec = insert_sfx(
                sfx_name=sfx_name,
                at_seconds=at_sec,
                volume=vol,
                input_idx=current_idx,
                out_label=f"sfx_{current_idx}",
            )
            cmd_inputs.extend(spec.input_args)
            filter_chains.append(spec.filter_string)
            mix_labels.append(f"[{spec.out_label}]")
            current_idx += 1

    # 3. amix filter
    amix = f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=first:dropout_transition=2[aout]"
    filter_chains.append(amix)
    filter_complex = ";".join(filter_chains)

    cmd = cmd_inputs + [
        "-filter_complex", filter_complex,
        "-map", "[aout]",
        "-t", f"{dur:.2f}",
        "-c:a", "libmp3lame", "-b:a", "192k",
        str(output_path)
    ]
    return cmd


def mix_multitrack_audio(
    voice_path: Path,
    output_path: Path,
    sfx_cues: Optional[List[Tuple[str, float]]] = None,
    mood: str = "phonk",
    music_volume: float = 0.12,
    music_track: Optional[Path] = None,
    total_duration: Optional[float] = None,
) -> Path:
    """
    Executes an end-to-end multi-track mix combining voice narration,
    ambient/hype background music, and timed SFX cues.
    """
    cmd = build_audio_mix_command(
        voice_path=voice_path,
        output_path=output_path,
        sfx_cues=sfx_cues,
        mood=mood,
        music_volume=music_volume,
        music_track=music_track,
        total_duration=total_duration,
    )
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return output_path


if __name__ == "__main__":
    print("==================================================")
    print(" SOUND PALETTE MASTER FOR 'ZIRVENIN KANUNU'")
    print("==================================================")
    print("\n[AVAILABLE MUSIC MOODS]:")
    for mood, path in list_music_moods().items():
        print(f"  * {mood.upper():<12} -> {Path(path).name}")

    print("\n[AVAILABLE VIRAL SFX]:")
    for sfx, path in list_sfx().items():
        print(f"  * {sfx:<18} -> {Path(path).name}")

    print("\n[SAMPLE SFX DELAY FILTERS]:")
    print("  ", insert_sfx("vine_boom", at_seconds=4.2))
    print("  ", insert_sfx("cash_register", at_seconds=10.5, volume=0.85, input_idx=2))
    print("  ", insert_sfx("record_scratch", at_seconds=15.0, volume=0.7, input_idx=3))
    print("  ", insert_sfx("ui_pop", at_seconds=2.0, volume=0.9, input_idx=4))
    print("  ", insert_sfx("error_buzzer", at_seconds=8.0, volume=1.0, input_idx=5))
    print("  ", insert_sfx("dramatic_screech", at_seconds=22.3, volume=0.6, input_idx=6))
