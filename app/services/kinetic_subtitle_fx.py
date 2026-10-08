"""
Kinetic Subtitle FX Engine for Zirvenin Kanunu.
Transforms standard captions into high-retention cinematic kinetic subtitles:
1. Dynamic Power-Word Highlighter (Glow Gold/Electric Cyan for key impact words).
2. Hormozi/CineShot style emphasis tags ({\\fscx115\\fscy115\\b1}).
3. Multi-language dual-line layout (Spoken English top neon, Turkish meaning bottom crisp white).
"""

from pathlib import Path
import re
import sys

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

POWER_WORDS_REGEX = re.compile(
    r"\b(DANGER|TEHLİKE|POWER|GÜÇ|FEAR|KORKU|RESPECT|SAYGI|SILENCE|SESSİZLİK|"
    r"DEATH|ÖLÜM|ENEMY|DÜŞMAN|KING|KRAL|TRUTH|HAKİKAT|LIES|YALANLAR|MONEY|PARA|"
    r"PREDICTABLE|TAHMİN|FREE|ÖZGÜR|CONTROL|KONTROL|CANAVAR|MONSTER|HAKLI|ZİRVE)\b",
    re.IGNORECASE
)


def format_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs >= 100:
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def highlight_power_words(text: str, color_bgr: str = "00D7FF") -> str:
    """Wraps viral power words in ASS tags for neon pop and 115% scale."""
    def repl(match):
        w = match.group(0)
        return rf"{{\c&H{color_bgr}&\fscx118\fscy118\b1}}{w}{{\r\fnMontserrat ExtraBold\fs42\b1\c&HFFFFFF&}}"
    return POWER_WORDS_REGEX.sub(repl, text)


def generate_hype_kinetic_ass(
    dialogues_en: list,
    dialogues_tr: list,
    output_path: Path,
    character_badge: str = "ZİRVENİN KANUNU"
):
    """
    Generates Hollywood-tier dual kinetic subtitles with animated power-word emphasis.
    """
    ass_header = f"""[Script Info]
Title: Zirvenin Kanunu Hype Subtitles
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: EnglishNeon,Montserrat ExtraBold,44,&H00FFFF00,&H00000000,&H00000000,&H90000000,-1,0,0,0,100,100,1,0,1,5,3,2,60,60,420,1
Style: TurkishMaster,Montserrat ExtraBold,40,&H00FFFFFF,&H00000000,&H00000000,&H90000000,-1,0,0,0,100,100,1,0,1,5,3,2,60,60,350,1
Style: TopHUD,Montserrat Black,32,&H0000D7FF,&H00000000,&H00000000,&HA0000000,-1,0,0,0,100,100,2,0,1,4,2,8,40,40,240,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [ass_header]

    # Calculate overall duration for Top HUD badge
    max_end = max(d[2] for d in dialogues_en) if dialogues_en else 30.0
    hud_end_time = format_ass_time(max_end + 1.0)
    lines.append(f"Dialogue: 0,0:00:00.00,{hud_end_time},TopHUD,,0,0,0,,🔥 {character_badge.upper()} 🔥\n")

    for idx, (en_text, start_s, end_s) in enumerate(dialogues_en):
        start_t = format_ass_time(start_s)
        end_t = format_ass_time(end_s)
        tr_text = dialogues_tr[idx] if idx < len(dialogues_tr) else ""

        # Apply Power Word Highlights
        en_highlighted = highlight_power_words(en_text.upper(), color_bgr="00FFFF") # Neon Cyan
        tr_highlighted = highlight_power_words(tr_text, color_bgr="00D7FF") # Radiant Gold

        lines.append(f"Dialogue: 1,{start_t},{end_t},EnglishNeon,,0,0,0,,{en_highlighted}\n")
        lines.append(f"Dialogue: 1,{start_t},{end_t},TurkishMaster,,0,0,0,,{tr_highlighted}\n")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    return output_path


if __name__ == "__main__":
    test_out = Path("output/test_kinetic.ass")
    test_en = [("I am not in danger, Skyler. I AM THE DANGER.", 0.0, 4.0)]
    test_tr = ["Ben tehlikede değilim Skyler. ASIL TEHLİKE BENİM."]
    generate_hype_kinetic_ass(test_en, test_tr, test_out, "WALTER WHITE")
    print(f"✅ Kinetic Subtitle Engine Hazır: {test_out}")
