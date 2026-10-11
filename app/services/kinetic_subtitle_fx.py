"""
Kinetic Subtitle FX Engine for Zirvenin Kanunu.
Transforms standard audio / voiceover dialogues into Hollywood & Alex Hormozi tier viral captions:
1. Dynamic word-by-word timing with bounce scale and glowing color highlights.
2. High-impact power words with instant visual triggers & viral emojis.
3. Safe bottom-half positioning tailored for 9:16 split-screens (no overlap with divider at Y=960).
4. High-CTR comment magnets & persistent interactive HUD elements.
"""

from pathlib import Path
import re
import sys
from typing import List, Tuple, Dict, Any, Optional

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Power words mapped to (emoji, BGR hex color)
POWER_WORDS_MAP: Dict[str, Tuple[str, str]] = {
    "GÜÇ": ("⚡", "00D7FF"),         # Gold
    "POWER": ("⚡", "00D7FF"),
    "TEHLİKE": ("⚠️", "0022FF"),     # Crimson Red
    "DANGER": ("⚠️", "0022FF"),
    "KRAL": ("👑", "00D7FF"),
    "KING": ("👑", "00D7FF"),
    "ÖLÜM": ("💀", "1E1EFF"),
    "DEATH": ("💀", "1E1EFF"),
    "DÜŞMAN": ("🎯", "0055FF"),
    "ENEMY": ("🎯", "0055FF"),
    "ZİRVE": ("🏔️", "00FFFF"),      # Neon Yellow
    "ZİRVEDEKİ": ("🏔️", "00FFFF"),
    "SAYGI": ("🔥", "00D7FF"),
    "RESPECT": ("🔥", "00D7FF"),
    "SESSİZLİK": ("🤫", "00E5FF"),   # Electric Cyan
    "SESSİZCE": ("🤫", "00E5FF"),
    "SILENCE": ("🤫", "00E5FF"),
    "KONTROL": ("🧠", "00FFFF"),
    "CONTROL": ("🧠", "00FFFF"),
    "HAKİKAT": ("👁️", "00FFFF"),
    "TRUTH": ("👁️", "00FFFF"),
    "PARA": ("💰", "00E676"),       # Neon Green
    "MONEY": ("💰", "00E676"),
    "FIRTINA": ("🌪️", "00E5FF"),
    "STORM": ("🌪️", "00E5FF"),
    "DARBE": ("💥", "0033FF"),
    "ÖLÜMCÜL": ("☠️", "0011EE"),
    "PLANLAR": ("♟️", "00D7FF"),
    "MASADAN": ("♠️", "00D7FF"),
    "NİYETİNİ": ("🎭", "00D7FF"),
}

POWER_WORDS_REGEX = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in POWER_WORDS_MAP.keys()) + r")\b",
    re.IGNORECASE
)


def format_ass_time(seconds: float) -> str:
    """Formats float seconds into ASS timestamp string: H:MM:SS.cs"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs >= 100:
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def get_power_word_info(word: str) -> Optional[Tuple[str, str]]:
    """Returns (emoji, bgr_color) if word is a recognized viral power word."""
    clean_w = re.sub(r"[^\w]", "", word).upper()
    return POWER_WORDS_MAP.get(clean_w, None)


def build_ass_header(
    font_name: str = "Montserrat ExtraBold",
    subtitle_margin_v: int = 460, # 460px from bottom puts subtitle at Y ≈ 1460 (centered in bottom screen 960-1920)
    cta_margin_v: int = 240
) -> str:
    """Constructs ASS script header and styles."""
    return f"""[Script Info]
Title: Zirvenin Kanunu Kinetic Master Subtitles
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: HormoziActive,{font_name},52,&H0000FFFF,&H00000000,&H00000000,&HA0000000,-1,0,0,0,105,105,2,0,1,6,2,2,40,40,{subtitle_margin_v},1
Style: HormoziNormal,{font_name},48,&H00FFFFFF,&H00000000,&H00000000,&HA0000000,-1,0,0,0,100,100,1,0,1,5,2,2,40,40,{subtitle_margin_v},1
Style: TopHUD,{font_name},32,&H0000D7FF,&H00000000,&H00000000,&HB0000000,-1,0,0,0,100,100,2,0,1,4,2,8,40,40,140,1
Style: CommentMagnetHUD,{font_name},34,&H0000FFFF,&H00000000,&H00000000,&HC0000000,-1,0,0,0,102,102,1,0,1,5,3,2,30,30,{cta_margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def split_into_word_chunks(
    sentences: List[Tuple[str, float, float]],
    chunk_size: int = 3
) -> List[Dict[str, Any]]:
    """
    Splits timed sentences into word chunks (default 2-4 words) with interpolated timings.
    Each chunk contains words with individual start/end times and highlights.
    """
    timed_chunks = []

    for sentence_text, s_start, s_end in sentences:
        words = sentence_text.strip().split()
        if not words:
            continue

        total_chars = sum(len(w) for w in words)
        total_duration = max(0.2, s_end - s_start)

        # Distribute timing proportionally according to character length
        curr_t = s_start
        word_timings = []
        for w in words:
            w_dur = (len(w) / max(1, total_chars)) * total_duration
            w_start = curr_t
            w_end = min(curr_t + w_dur, s_end)
            word_timings.append({
                "word": w,
                "start": w_start,
                "end": w_end
            })
            curr_t = w_end

        # Group words into chunks of `chunk_size`
        for i in range(0, len(word_timings), chunk_size):
            chunk_slice = word_timings[i:i + chunk_size]
            chunk_start = chunk_slice[0]["start"]
            chunk_end = chunk_slice[-1]["end"]
            timed_chunks.append({
                "words": chunk_slice,
                "start": chunk_start,
                "end": chunk_end
            })

    return timed_chunks


def generate_kinetic_word_by_word_ass(
    timed_sentences: List[Tuple[str, float, float]],
    output_path: Path,
    character_badge: str = "👑 ZİRVENİN KANUNU 👑",
    cta_text: str = "💬 Sen olsan ne yapardın? 1 mi 2 mi? 👇",
    chunk_size: int = 3,
    subtitle_margin_v: int = 460
) -> Path:
    """
    Renders Hormozi/MrBeast kinetic subtitles:
    - Displays short 2-4 word punchy chunks in bottom screen.
    - Active word pops in neon yellow/gold with bouncy zoom effect {\\t(0,80,\\fscx120\\fscy120)\\t(80,160,\\fscx106\\fscy106)}.
    - Power words trigger viral emojis and golden halos.
    - Persistent Top HUD & Comment Magnet CTA banner at bottom.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    header = build_ass_header(subtitle_margin_v=subtitle_margin_v)
    lines = [header]

    # Calculate overall duration
    max_duration = max(s[2] for s in timed_sentences) if timed_sentences else 30.0
    full_end_str = format_ass_time(max_duration + 1.0)

    # 1. Top HUD Badge
    if character_badge:
        lines.append(
            f"Dialogue: 0,0:00:00.00,{full_end_str},TopHUD,,0,0,0,,{character_badge}\n"
        )

    # 2. Bottom CTA Hook / Comment Magnet
    if cta_text:
        lines.append(
            f"Dialogue: 0,0:00:01.00,{full_end_str},CommentMagnetHUD,,0,0,0,,{{\\b1\\c&H00D7FF&}}{cta_text}\n"
        )

    # 3. Word-by-Word Kinetic Dialogue Events
    chunks = split_into_word_chunks(timed_sentences, chunk_size=chunk_size)

    for chunk in chunks:
        words = chunk["words"]
        for active_idx, active_item in enumerate(words):
            w_start = format_ass_time(active_item["start"])
            w_end = format_ass_time(active_item["end"])

            chunk_fragments = []
            for idx, item in enumerate(words):
                w_text = item["word"]
                p_info = get_power_word_info(w_text)

                if idx == active_idx:
                    # Active Word: Bouncy zoom + Highlight Color + Emoji if Power Word
                    if p_info:
                        emoji, color_bgr = p_info
                        display_text = f"{emoji} {w_text.upper()}"
                        chunk_fragments.append(
                            f"{{\\c&H{color_bgr}&\\t(0,90,\\fscx122\\fscy122)\\t(90,180,\\fscx108\\fscy108)\\b1\\bord7}}{display_text}{{\\r\\c&HFFFFFF&\\b1}}"
                        )
                    else:
                        chunk_fragments.append(
                            f"{{\\c&H002EFFFF&\\t(0,90,\\fscx120\\fscy120)\\t(90,180,\\fscx106\\fscy106)\\b1\\bord7}}{w_text.upper()}{{\\r\\c&HFFFFFF&\\b1}}"
                        )
                else:
                    # Inactive Words: Crisp clean white with bold stroke
                    chunk_fragments.append(
                        f"{{\\c&HFFFFFF&\\b1}}{w_text.upper()}"
                    )

            rendered_chunk = " ".join(chunk_fragments)
            lines.append(f"Dialogue: 2,{w_start},{w_end},HormoziActive,,0,0,0,,{rendered_chunk}\n")

    with open(output_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    return output_path


# Backward compatibility wrapper
def generate_hype_kinetic_ass(
    dialogues_en: list,
    dialogues_tr: list,
    output_path: Path,
    character_badge: str = "ZİRVENİN KANUNU"
):
    """Retained for backward compatibility with older pipeline scripts."""
    subtitles = []
    for idx, item in enumerate(dialogues_en):
        text_en, s_start, s_end = item
        text_tr = dialogues_tr[idx] if idx < len(dialogues_tr) else text_en
        subtitles.append((text_tr, s_start, s_end))
    return generate_kinetic_word_by_word_ass(
        timed_sentences=subtitles,
        output_path=output_path,
        character_badge=f"🔥 {character_badge.upper()} 🔥"
    )


if __name__ == "__main__":
    test_out = Path("output/test_kinetic_word_by_word.ass")
    test_data = [
        ("48 Güç Yasası, Yasa 3: Niyetini asla belli etme.", 0.0, 4.5),
        ("Bir fırtına koparmak istiyorsan, havanın en sakin olduğu anı bekle.", 4.5, 9.2),
        ("Zirvedeki zihinler hamlelerini asla bağırarak duyurmaz;", 9.2, 13.8),
        ("sessizce planlar ve rakibi yok olduğunda masadan kalkarlar.", 13.8, 18.5),
        ("Zirvenin Kanunu: En ölümcül darbe, varlığı hissedilmeyendir.", 18.5, 23.0)
    ]
    out = generate_kinetic_word_by_word_ass(test_data, test_out)
    print(f"✅ Kinetic Subtitle FX Test Complete: {out}")
