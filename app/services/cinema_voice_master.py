"""
Cinema Voice Master Engine for Zirvenin Kanunu.
Delivers 10M+ view tier audio authenticity:
1. Original Hollywood Actor Voice Preservation (Extracts and polishes Bryan Cranston, Cillian Murphy, Brad Pitt, Al Pacino directly from 4K clips).
2. Dual-Language Kinetic Subtitles (Original English Spoken + Turkish Meaning on screen like @1dublaj).
3. Ultra-Expressive SSML Neural Acting for procedural narration (dramatic pauses, pitch drops, zero monotony).
4. Phonk & Hans Zimmer Bass-Drop Sidechain Ducking.
"""

import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

# Preset map of iconic scene audio offsets and timestamps
ICONIC_CINEMA_CLIPS = {
    "breaking_bad_danger": {
        "source": "breaking_bad_master.mp4",
        "has_original_dialogue": True,
        "character": "Walter White (Bryan Cranston)",
        "iconic_dialogue_en": [
            ("Who are you talking to right now?", 0.0, 3.2),
            ("Do you know how much I make a year?", 3.2, 5.8),
            ("I mean even if I told you, you wouldn't believe it.", 5.8, 8.5),
            ("No, you clearly don't know who you're talking to...", 8.5, 12.0),
            ("I am not in danger, Skyler.", 12.0, 14.5),
            ("I AM THE DANGER.", 14.5, 17.5),
            ("A guy opens his door and gets shot, and you think that of me?", 17.5, 21.0),
            ("No. I am the one who knocks!", 21.0, 24.5),
        ],
        "dialogue_tr_meaning": [
            "Şu an kiminle konuştuğunu sanıyorsun?",
            "Yılda ne kadar kazandığımı biliyor musun?",
            "Söylesem bile aklın almaz.",
            "Hayır, karşında kimin olduğunu kesinlikle bilmiyorsun...",
            "Ben tehlikede değilim Skyler.",
            "ASIL TEHLİKE BENİM.",
            "Bir adam kapısını açıp vurulduğunda, vurulan adam ben değilim.",
            "O kapıyı çalan adam benim!",
        ]
    },
    "shelby_power": {
        "source": "peaky_master.mp4",
        "has_original_dialogue": True,
        "character": "Thomas Shelby (Cillian Murphy)",
        "iconic_dialogue_en": [
            ("There is no rest for me in this world.", 0.0, 3.5),
            ("Perhaps in the next.", 3.5, 6.0),
            ("Everyone is a whore, Grace.", 6.0, 9.0),
            ("We just sell different parts of ourselves.", 9.0, 13.0),
            ("You can change what you do...", 13.0, 16.0),
            ("...but you can't change what you want.", 16.0, 20.0),
            ("Because in the end, fear makes you predictable.", 20.0, 24.5),
        ],
        "dialogue_tr_meaning": [
            "Bu dünyada bana huzur yok.",
            "Belki diğerinde.",
            "Herkes bir şeylerini satar, Grace.",
            "Sadece kendimizin farklı parçalarını satıyoruz.",
            "Ne yaptığını değiştirebilirsin...",
            "...ama ne istediğini asla değiştiremezsin.",
            "Çünkü sonunda, korku seni tahmin edilebilir yapar.",
        ]
    },
    "fight_club_truth": {
        "source": "fight_club_master.mp4",
        "has_original_dialogue": True,
        "character": "Tyler Durden (Brad Pitt)",
        "iconic_dialogue_en": [
            ("You're not your job.", 0.0, 2.5),
            ("You're not how much money you have in the bank.", 2.5, 6.0),
            ("You're not the car you drive.", 6.0, 8.5),
            ("The things you own end up owning you.", 8.5, 12.5),
            ("It's only after we've lost everything...", 12.5, 16.0),
            ("...that we're free to do anything.", 16.0, 20.5),
        ],
        "dialogue_tr_meaning": [
            "Sen işin değilsin.",
            "Bankadaki paran kadar değilsin.",
            "Kullandığın araba sen değilsin.",
            "Sahip olduğun şeyler sonunda sana sahip olur.",
            "Ancak her şeyi kaybettikten sonradır ki...",
            "...her şeyi yapmakta tamamen özgür oluruz.",
        ]
    }
}


def extract_original_hollywood_audio(clip_path: Path, output_audio: Path, start_sec: float = 0.0, duration: float = 25.0) -> bool:
    """
    Extracts the crystal-clear original dialogue stream from a master clip,
    applying gentle compression and vocal EQ to make the actor's voice punchy.
    """
    if not clip_path.exists():
        return False

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start_sec:.2f}",
        "-i", str(clip_path),
        "-t", f"{duration:.2f}",
        "-vn",
        # Vocal EQ: slight bass cleanup at 80Hz, boost presence at 3kHz
        "-af", "highpass=f=80,equalizer=f=3000:t=q:w=1.5:g=3,acompressor=threshold=-18dB:ratio=3:attack=10:release=100",
        "-c:a", "libmp3lame", "-b:a", "192k",
        str(output_audio)
    ]
    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return res.returncode == 0 and output_audio.exists() and output_audio.stat().st_size > 1000


def build_dual_language_ass(dialogue_en: List[Tuple[str, float, float]], dialogue_tr: List[str], output_ass: Path):
    """
    Creates Hollywood-tier dual-language kinetic subtitles:
    - English spoken words in Electric Neon (Top line)
    - Turkish meaning translation in Clean Gold/White (Bottom line)
    Exactly matching the style of 10M+ view channels (@1dublaj, @cineshot_tr).
    """
    header = (
        "[Script Info]\n"
        "Title: Zirvenin Kanunu Hollywood Dual Dub\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: EnglishOriginal,Montserrat Black,68,&H0000D7FF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,0,2,20,20,440,1\n"
        "Style: TurkishMeaning,Montserrat SemiBold,50,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,0,2,20,20,360,1\n"
        "Style: SeriesBadge,Arial,34,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,2,0,8,10,10,120,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    events = []
    def to_time(s):
        m = int(s // 60)
        sec = int(s % 60)
        cs = int(round((s - int(s)) * 100))
        return f"0:{m:02d}:{sec:02d}.{cs:02d}"

    for i, (text_en, start_s, end_s) in enumerate(dialogue_en):
        start_str = to_time(start_s)
        end_str = to_time(end_s)
        text_tr = dialogue_tr[i] if i < len(dialogue_tr) else ""

        # English punch line (upper)
        events.append(f"Dialogue: 0,{start_str},{end_str},EnglishOriginal,,0,0,0,,{{\\blur1.5}}{text_en.upper()}")
        # Turkish translation (lower)
        if text_tr:
            events.append(f"Dialogue: 0,{start_str},{end_str},TurkishMeaning,,0,0,0,,{{\\c&H00D0D0D0&}}{text_tr}")

    with open(output_ass, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(events) + "\n")
