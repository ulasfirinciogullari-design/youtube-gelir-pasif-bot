"""
Authentic Social Media Comment Renderer for 'Zirvenin Kanunu'.
Generates 100% pixel-perfect, native dark-mode comment cards for YouTube, X (Twitter),
TikTok, and Instagram using Playwright (Chromium) and genuine Apple / Twemoji vector emojis.

Updates based on direct feedback:
1. Realistic like counts (842, 620, 1.8K, 3.2K) instead of exaggerated 14K+.
2. Authentic casual avatars (meme cat, casual youth, chill dog) instead of corporate studio headshots.
3. Zero artificial platform logo badges (no fake "YouTube" / "Post" badges). Native app look.
4. Hilarious, niche, scene-specific comments reflecting the authentic comedy of Kolpaçino.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = BASE_DIR / "output"
ASSETS_DIR = BASE_DIR / "assets"
EMOJIS_DIR = ASSETS_DIR / "emojis"
AVATARS_DIR = ASSETS_DIR / "avatars"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
EMOJIS_DIR.mkdir(parents=True, exist_ok=True)
AVATARS_DIR.mkdir(parents=True, exist_ok=True)

# Authentic SVG Icons for Native App UI
ICONS = {
    "youtube_like": '''<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
<path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"></path>
</svg>''',

    "youtube_dislike": '''<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
<path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h3a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-3"></path>
</svg>''',

    "x_verified": '''<svg width="19" height="19" viewBox="0 0 22 22" fill="none">
<path d="M20.396 11a1.996 1.996 0 0 0-.847-1.637 1.996 1.996 0 0 0-.17-1.833 1.996 1.996 0 0 0-1.154-1.442 1.996 1.996 0 0 0-.877-1.621 1.996 1.996 0 0 0-1.748-.624 1.996 1.996 0 0 0-1.442-1.154 1.996 1.996 0 0 0-1.833-.17A1.996 1.996 0 0 0 11 1.604a1.996 1.996 0 0 0-1.637.847 1.996 1.996 0 0 0-1.833.17 1.996 1.996 0 0 0-1.442 1.154 1.996 1.996 0 0 0-1.748.624 1.996 1.996 0 0 0-.877 1.621 1.996 1.996 0 0 0-1.154 1.442 1.996 1.996 0 0 0-.17 1.833A1.996 1.996 0 0 0 1.604 11a1.996 1.996 0 0 0 .847 1.637 1.996 1.996 0 0 0 .17 1.833 1.996 1.996 0 0 0 1.154 1.442 1.996 1.996 0 0 0 .877 1.621 1.996 1.996 0 0 0 1.748.624 1.996 1.996 0 0 0 1.442-1.154 1.996 1.996 0 0 0 1.833-.17A1.996 1.996 0 0 0 11 20.396a1.996 1.996 0 0 0 1.637-.847 1.996 1.996 0 0 0 1.833-.17 1.996 1.996 0 0 0 1.442-1.154 1.996 1.996 0 0 0 1.748-.624 1.996 1.996 0 0 0 .877-1.621 1.996 1.996 0 0 0 1.154-1.442 1.996 1.996 0 0 0 .17-1.833A1.996 1.996 0 0 0 20.396 11z" fill="#1D9BF0"/>
<path d="M9.688 14.875l-3.563-3.562 1.25-1.25 2.313 2.312 5.062-5.062 1.25 1.25-6.312 6.312z" fill="#FFFFFF"/>
</svg>''',

    "x_reply": '''<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"></path>
</svg>''',

    "x_retweet": '''<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
<path d="M17 1l4 4-4 4"></path>
<path d="M3 11V9a4 4 0 0 1 4-4h14"></path>
<path d="M7 23l-4-4 4-4"></path>
<path d="M21 13v2a4 4 0 0 1-4 4H3"></path>
</svg>''',

    "x_like": '''<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
<path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"></path>
</svg>''',

    "x_views": '''<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
<line x1="18" y1="20" x2="18" y2="10"></line>
<line x1="12" y1="20" x2="12" y2="4"></line>
<line x1="6" y1="20" x2="6" y2="14"></line>
</svg>''',

    "x_share": '''<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
<circle cx="18" cy="5" r="3"></circle>
<circle cx="6" cy="12" r="3"></circle>
<circle cx="18" cy="19" r="3"></circle>
<line x1="8.59" y1="13.51" x2="15.42" y2="17.49"></line>
<line x1="15.41" y1="6.51" x2="8.59" y2="10.49"></line>
</svg>''',

    "tiktok_heart": '''<svg width="26" height="26" viewBox="0 0 24 24" fill="#FF2D55">
<path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/>
</svg>''',

    "instagram_heart": '''<svg width="24" height="24" viewBox="0 0 24 24" fill="#ED4956">
<path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/>
</svg>''',
}


def _file_to_base64_data_uri(path: Path, mime: str = "image/jpeg") -> str:
    """Encodes a local file as a data URI."""
    if not path.exists():
        return ""
    b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def _get_twemoji_base64(hex_code: str) -> Optional[str]:
    """Finds or retrieves twemoji SVG and returns as data URI."""
    p = EMOJIS_DIR / f"{hex_code}.svg"
    if p.exists():
        b64 = base64.b64encode(p.read_bytes()).decode("utf-8")
        return f"data:image/svg+xml;base64,{b64}"

    # Try downloading from CDN if not cached
    try:
        import urllib.request
        url = f"https://cdnjs.cloudflare.com/ajax/libs/twemoji/14.0.2/svg/{hex_code}.svg"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=3).read()
        p.write_bytes(data)
        b64 = base64.b64encode(data).decode("utf-8")
        return f"data:image/svg+xml;base64,{b64}"
    except Exception:
        return None


def replace_emojis_with_twemoji(text: str) -> str:
    """
    Scans text for unicode emojis and replaces each individual emoji
    with its authentic Apple/Twemoji SVG data URI.
    """
    emoji_pattern = re.compile(
        r'([\U0001F000-\U0010FFFF]|[\u2600-\u27BF])(\uFE0F)?(?:\u200D([\U0001F000-\U0010FFFF]|[\u2600-\u27BF])(\uFE0F)?)*'
    )

    def _replace_match(m):
        raw = m.group(0)
        codepoints = [f"{ord(c):x}" for c in raw if ord(c) != 0xfe0f]
        hex_str = "-".join(codepoints)

        data_uri = _get_twemoji_base64(hex_str)
        if not data_uri and len(codepoints) > 1:
            data_uri = _get_twemoji_base64(codepoints[0])

        if data_uri:
            return f'<img class="emoji" src="{data_uri}" alt="{raw}" />'
        return raw

    replaced = emoji_pattern.sub(_replace_match, text)
    orphan_pattern = re.compile(r'(\S+)(\s*(?:<img class="emoji"[^>]+>\s*)+)$')
    final_text = orphan_pattern.sub(r'<span class="emoji-cluster" style="white-space: nowrap;">\1\2</span>', replaced)
    return final_text


@dataclass
class SocialComment:
    platform: str  # 'youtube', 'tiktok', 'x', 'instagram'
    author_name: str
    handle: str
    text: str
    timestamp: str
    avatar_file: str
    verified: bool = False
    metrics: Dict[str, Any] = field(default_factory=dict)
    output_filename: str = "comment.png"


def build_card_html(comment: SocialComment) -> str:
    """Builds realistic, authentic HTML for the comment card based on platform."""
    avatar_path = AVATARS_DIR / comment.avatar_file
    avatar_uri = _file_to_base64_data_uri(avatar_path, "image/jpeg")

    formatted_text = replace_emojis_with_twemoji(comment.text)
    p = comment.platform.lower()

    if p == "youtube":
        likes_str = comment.metrics.get("likes", "842")
        actions_html = f'''
        <div class="yt-actions">
            <div class="action-btn">
                {ICONS["youtube_like"]}
                <span>{likes_str}</span>
            </div>
            <div class="action-btn">
                {ICONS["youtube_dislike"]}
            </div>
            <div class="reply-pill">Yanıtla</div>
        </div>
        '''
        header_html = f'''
        <div class="author-row">
            <span class="author-name">{comment.author_name}</span>
            <span class="user-handle">{comment.handle}</span>
            <span class="meta-dot">•</span>
            <span class="timestamp">{comment.timestamp}</span>
        </div>
        '''
        card_class = "yt-card"

    elif p == "tiktok":
        likes_str = comment.metrics.get("likes", "1.8K")
        actions_html = f'''
        <div class="tt-meta-row">
            <span class="timestamp">{comment.timestamp}</span>
            <span class="meta-dot">·</span>
            <span class="reply-text">Yanıtla</span>
        </div>
        '''
        header_html = f'''
        <div class="author-row">
            <span class="author-name">{comment.handle.replace("@", "")}</span>
        </div>
        '''
        right_action = f'''
        <div class="tt-side-action">
            {ICONS["tiktok_heart"]}
            <span class="tt-like-count">{likes_str}</span>
        </div>
        '''
        card_class = "tt-card"

    elif p in ("x", "twitter"):
        verified_icon = ICONS["x_verified"] if comment.verified else ""
        retweets = comment.metrics.get("retweets", "185")
        likes = comment.metrics.get("likes", "3.2K")
        views = comment.metrics.get("views", "94K")

        actions_html = f'''
        <div class="x-actions">
            <div class="x-action-item">
                {ICONS["x_reply"]}
                <span>64</span>
            </div>
            <div class="x-action-item">
                {ICONS["x_retweet"]}
                <span>{retweets}</span>
            </div>
            <div class="x-action-item like-item">
                {ICONS["x_like"]}
                <span>{likes}</span>
            </div>
            <div class="x-action-item">
                {ICONS["x_views"]}
                <span>{views}</span>
            </div>
            <div class="x-action-item">
                {ICONS["x_share"]}
            </div>
        </div>
        '''
        header_html = f'''
        <div class="author-row">
            <span class="author-name">{comment.author_name}</span>
            {verified_icon}
            <span class="user-handle">{comment.handle}</span>
            <span class="meta-dot">·</span>
            <span class="timestamp">{comment.timestamp}</span>
        </div>
        '''
        card_class = "x-card"

    elif p == "instagram":
        likes_str = comment.metrics.get("likes", "1.4K beğenme")
        actions_html = f'''
        <div class="ig-meta-row">
            <span class="timestamp">{comment.timestamp}</span>
            <span class="meta-dot">·</span>
            <span class="like-count">{likes_str}</span>
            <span class="meta-dot">·</span>
            <span class="reply-text">Yanıtla</span>
        </div>
        '''
        header_html = f'''
        <div class="author-row">
            <span class="author-name">{comment.handle.replace("@", "")}</span>
        </div>
        '''
        right_action = f'''
        <div class="ig-side-action">
            {ICONS["instagram_heart"]}
        </div>
        '''
        card_class = "ig-card"

    else:
        actions_html = ""
        header_html = f'<div class="author-row"><span class="author-name">{comment.author_name}</span></div>'
        card_class = "default-card"

    side_action_html = right_action if "right_action" in locals() else ""

    html = f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="utf-8">
<style>
  * {{
    box-sizing: border-box;
    margin: 0;
    padding: 0;
  }}

  body {{
    width: 960px;
    background: transparent;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    display: flex;
    justify-content: center;
    align-items: center;
    padding: 24px 16px;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
  }}

  .card-container {{
    width: 928px;
    position: relative;
    background: rgba(20, 23, 29, 0.94);
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 24px;
    box-shadow: 0 16px 44px rgba(0, 0, 0, 0.65), 0 4px 16px rgba(0, 0, 0, 0.4);
    backdrop-filter: blur(24px);
    overflow: hidden;
    padding: 22px 28px 22px 24px;
    color: #F1F1F1;
  }}

  /* Card Layout */
  .card-inner {{
    display: flex;
    gap: 18px;
    align-items: flex-start;
  }}

  /* Avatar */
  .avatar-wrap {{
    position: relative;
    flex-shrink: 0;
  }}

  .avatar {{
    width: 58px;
    height: 58px;
    border-radius: 50%;
    object-fit: cover;
    border: 1.5px solid rgba(255, 255, 255, 0.2);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.35);
    display: block;
  }}

  .ig-avatar {{
    border: 2px solid transparent;
    background: linear-gradient(45deg, #f09433, #e6683c 25%, #dc2743 50%, #cc2366 75%, #bc1888 100%) border-box;
  }}

  /* Content Area */
  .content-area {{
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 9px;
  }}

  .author-row {{
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 15.5px;
    line-height: 1.2;
  }}

  .author-name {{
    font-weight: 700;
    color: #FFFFFF;
    letter-spacing: -0.2px;
  }}

  .user-handle {{
    font-weight: 500;
    color: #8E95A5;
    font-size: 14.5px;
  }}

  .meta-dot {{
    color: #6E7585;
    font-size: 14px;
  }}

  .timestamp {{
    font-size: 13.5px;
    color: #8E95A5;
    font-weight: 400;
  }}

  /* Comment Text */
  .comment-text {{
    color: #F8F9FA;
    font-size: 18.5px;
    line-height: 1.48;
    font-weight: 450;
    letter-spacing: -0.15px;
    word-break: break-word;
    padding-right: 20px;
  }}

  /* Authentic Vector Emoji */
  img.emoji {{
    height: 1.28em;
    width: 1.28em;
    margin: 0 0.12em;
    vertical-align: -0.22em;
    display: inline-block;
  }}

  /* YouTube Actions */
  .yt-actions {{
    display: flex;
    align-items: center;
    gap: 16px;
    margin-top: 6px;
    color: #AAAAAA;
    font-size: 14px;
    font-weight: 500;
  }}

  .action-btn {{
    display: inline-flex;
    align-items: center;
    gap: 7px;
    cursor: pointer;
    padding: 6px 12px;
    border-radius: 18px;
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.08);
  }}

  .reply-pill {{
    cursor: pointer;
    padding: 6px 14px;
    border-radius: 18px;
    color: #F1F1F1;
    background: rgba(255, 255, 255, 0.08);
    font-size: 13px;
    font-weight: 600;
  }}

  /* X Actions */
  .x-actions {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    max-width: 580px;
    margin-top: 8px;
    color: #71767B;
    font-size: 14px;
  }}

  .x-action-item {{
    display: flex;
    align-items: center;
    gap: 8px;
  }}

  .x-action-item.like-item {{
    color: #F91880;
  }}

  /* TikTok Meta & Side Action */
  .tt-meta-row {{
    display: flex;
    align-items: center;
    gap: 10px;
    margin-top: 4px;
    font-size: 14px;
    color: rgba(255, 255, 255, 0.55);
  }}

  .tt-meta-row .reply-text {{
    font-weight: 600;
    color: rgba(255, 255, 255, 0.85);
  }}

  .tt-side-action {{
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
    position: absolute;
    right: 28px;
    bottom: 22px;
  }}

  .tt-like-count {{
    font-size: 13px;
    font-weight: 600;
    color: rgba(255, 255, 255, 0.7);
  }}

  /* Instagram Meta & Side Action */
  .ig-meta-row {{
    display: flex;
    align-items: center;
    gap: 12px;
    margin-top: 6px;
    font-size: 14px;
    color: #8E95A5;
  }}

  .ig-meta-row .like-count {{
    font-weight: 600;
    color: #A8A8A8;
  }}

  .ig-meta-row .reply-text {{
    font-weight: 600;
    color: #C8CDD6;
  }}

  .ig-side-action {{
    position: absolute;
    right: 30px;
    bottom: 24px;
  }}

  /* Top ambient glow accent */
  .card-container::before {{
    content: '';
    position: absolute;
    top: 0;
    left: 40px;
    right: 40px;
    height: 2px;
    background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.35), transparent);
  }}
</style>
</head>
<body>
  <div class="card-container {card_class}">
    <div class="card-inner">
      <div class="avatar-wrap">
        <img class="avatar {'ig-avatar' if p == 'instagram' else ''}" src="{avatar_uri}" alt="{comment.author_name}" />
      </div>
      <div class="content-area">
        {header_html}
        <div class="comment-text">
          {formatted_text}
        </div>
        {actions_html}
      </div>
    </div>
    {side_action_html}
  </div>
</body>
</html>"""
    return html


class AuthenticSocialCommentRenderer:
    """Renderer class utilizing Playwright Chromium for ultra-realistic social cards."""

    def render_batch(self, comments: List[SocialComment]) -> List[Path]:
        """Renders multiple cards efficiently in a single browser session."""
        from playwright.sync_api import sync_playwright

        rendered_paths = []
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(
                viewport={"width": 960, "height": 450},
                device_scale_factor=2
            )

            for comment in comments:
                out_path = OUTPUT_DIR / comment.output_filename
                html_content = build_card_html(comment)
                temp_html = OUTPUT_DIR / f"temp_{out_path.stem}.html"
                temp_html.write_text(html_content, encoding="utf-8")

                page.goto(f"file:///{temp_html.resolve()}", wait_until="networkidle")
                page.wait_for_timeout(80)

                container = page.query_selector("body")
                if container:
                    container.screenshot(path=str(out_path), omit_background=True)
                else:
                    page.screenshot(path=str(out_path), omit_background=True)

                if temp_html.exists():
                    try:
                        temp_html.unlink()
                    except Exception:
                        pass

                print(f"✅ Rendered: {out_path} ({out_path.stat().st_size} bytes)")
                rendered_paths.append(out_path)

            browser.close()

        return rendered_paths


def get_kolpacino_comments() -> List[SocialComment]:
    """Returns the 5 witty, niche, authentic Kolpaçino scene comments."""
    return [
        # Card 1: YouTube Dark Mode - The Classic Cat Avatar & Legendary Observation
        SocialComment(
            platform="youtube",
            author_name="Emrecan Yılmaz",
            handle="@emrecanyilmaz",
            timestamp="3 gün önce",
            text="Sabri abi adamın evini barkını yıktı, arabasını yaktı, üstüne 'kusura bakma ben onun adına özür diliyorum' diyor delirmemek elde değil ahsdhashd 😂",
            metrics={"likes": "842"},
            avatar_file="avatar_cat.jpg",
            output_filename="real_social_c1.png"
        ),
        # Card 2: TikTok Dark Mode - Tayfun's Nodding
        SocialComment(
            platform="tiktok",
            author_name="Selin Arslan",
            handle="@selin.arslan",
            timestamp="1g önce",
            text="Tayfun arkada kurbanlık koyun gibi 'Evet abi' diye kafa sallıyor ya, adamdaki rahatlık kimsede yok 😭💀",
            metrics={"likes": "1.8K"},
            avatar_file="avatar_chill_girl.jpg",
            output_filename="real_social_c2.png"
        ),
        # Card 3: X (Twitter) Dark Mode - Ricochet calculation
        SocialComment(
            platform="x",
            author_name="Burak Yılmaz",
            handle="@burak_tweet",
            timestamp="4s",
            text="Merminin sekme açısını Sabri abiye anlatsan 'sağlık olsun kardeşim' der yine Özgür'ün kafasına sıktırır amk ♟️🔥",
            verified=True,
            metrics={"retweets": "185", "likes": "3.2K", "views": "94K"},
            avatar_file="avatar_street_guy.jpg",
            output_filename="real_social_c3.png"
        ),
        # Card 4: Instagram Dark Mode - Aging 15 years
        SocialComment(
            platform="instagram",
            author_name="Damla Demir",
            handle="@damla_demir",
            timestamp="2g",
            text="Özgür'ün hayatı: Sabri abiyle 2 gün takılıp 15 yıl yaşlanmak ahahah 💀",
            metrics={"likes": "1.4K beğenme"},
            avatar_file="avatar_funny_dog.jpg",
            output_filename="real_social_c4.png"
        ),
        # Card 5: YouTube Dark Mode - Resignation quote
        SocialComment(
            platform="youtube",
            author_name="Onur Aksoy",
            handle="@onur_aksoy",
            timestamp="4 gün önce",
            text="Abi adam 'beni bir oyuna getirirsen bileyim yani' derkenki o kabullenmişlik çaresizliği beni benden alıyor 😂🎯",
            metrics={"likes": "620"},
            avatar_file="avatar_car.jpg",
            output_filename="real_social_c5.png"
        ),
    ]


def main():
    print("🚀 Rendering Updated Authentic Social Comment Cards...")
    renderer = AuthenticSocialCommentRenderer()
    comments = get_kolpacino_comments()
    rendered = renderer.render_batch(comments)

    for i, path in enumerate(rendered, 1):
        compat_path = OUTPUT_DIR / f"kolpacino_c{i}.png"
        shutil.copyfile(path, compat_path)
        print(f"📋 Synced: {compat_path.name} -> {path.name}")

    print("\n🎉 All 5 Authentic Social Cards Updated & Rendered Successfully!")


if __name__ == "__main__":
    main()
