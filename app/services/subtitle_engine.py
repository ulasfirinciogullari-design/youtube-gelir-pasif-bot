"""
Dynamic ASS / SRT Subtitle Generation Service for YouTube Shorts & Videos.
Creates modern, high-retention 'MrBeast / Hormozi' style animated karaoke subtitles
with thick outlines, bright highlights, and perfect mobile safe-zone alignment.
"""

from pathlib import Path
import re


def _format_ass_time(seconds: float) -> str:
    """Format seconds into ASS timestamp format: H:MM:SS.cs"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs == 100:
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def generate_shorts_ass_subtitles(
    sentences: list[dict],
    output_path: str | Path,
    font_name: str = "Arial Black",
    font_size: int = 68,
    primary_color: str = "&H00FFFFFF",  # Pure White
    highlight_color: str = "&H0000FFFF", # Vibrant Yellow
    outline_color: str = "&H00000000",   # Solid Black Outline
    outline_width: int = 7,
    margin_v: int = 860, # Center area for 1080x1920 (Safe Zone)
) -> Path:
    """
    Generate Advanced SubStation Alpha (.ass) subtitles with word-by-word highlight.
    
    Args:
        sentences: list of dicts with {'text': str, 'start': float, 'end': float}
        output_path: Path where .ass file should be written
    """
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    
    # ASS Header
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: ShortsStyle,{font_name},{font_size},{primary_color},&H000000FF,{outline_color},&H90000000,-1,0,0,0,100,100,3,0,1,{outline_width},4,2,50,50,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    
    events = []
    
    for sentence in sentences:
        text = sentence.get("text", "").strip()
        start = float(sentence.get("start", 0.0))
        end = float(sentence.get("end", 0.0))
        
        if not text or end <= start:
            continue
            
        words = text.split()
        if not words:
            continue
            
        # Group words into chunks of 3-5 words maximum to avoid cluttered screens on mobile
        max_chunk = 4
        word_chunks = [words[i:i + max_chunk] for i in range(0, len(words), max_chunk)]
        
        # Allocate time proportionally per chunk based on word counts
        total_words = len(words)
        total_duration = end - start
        
        current_chunk_start = start
        for chunk in word_chunks:
            chunk_duration = total_duration * (len(chunk) / total_words)
            chunk_end = current_chunk_start + chunk_duration
            
            # Word-level timing inside chunk
            per_word_dur = chunk_duration / len(chunk)
            
            for word_idx, active_word in enumerate(chunk):
                w_start = current_chunk_start + (word_idx * per_word_dur)
                w_end = w_start + per_word_dur
                
                # Build styled line where active word has highlight_color
                styled_parts = []
                for idx, w in enumerate(chunk):
                    clean_w = w.upper()
                    if idx == word_idx:
                        # Highlight active word
                        styled_parts.append(r"{\c" + highlight_color + r"\fscx108\fscy108}" + clean_w + r"{\rShortsStyle}")
                    else:
                        # Normal word
                        styled_parts.append(r"{\c" + primary_color + r"}" + clean_w)
                        
                dialogue_text = " ".join(styled_parts)
                start_str = _format_ass_time(w_start)
                end_str = _format_ass_time(w_end)
                
                events.append(f"Dialogue: 0,{start_str},{end_str},ShortsStyle,,0,0,0,,{dialogue_text}")
                
            current_chunk_start = chunk_end
            
    content = header + "\n".join(events) + "\n"
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(content)
        
    return out_file
