"""New system: start scheduled videos while the audience is awake, spaced apart.

Release time used to be a by-product of production speed, so a new Turkey day
started its Shorts right after midnight and several could go public within an
hour. This only delays when the next video starts; a video already being made
or published is never held back.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import settings

# Narration language -> audience time zone and the hours [start, end) a new
# video may start in.
WINDOWS = {'tr': ('Europe/Istanbul', 8, 23), 'en': ('America/New_York', 7, 22)}


def wait_reason(language: str, state: dict, now: float) -> str | None:
    if getattr(settings, 'studio_release_window', False) is not True:
        return None
    window = WINDOWS.get(language)
    if window:
        zone, start, end = window
        if not start <= datetime.fromtimestamp(now, ZoneInfo(zone)).hour < end:
            return 'outside_audience_hours'
    gap = getattr(settings, 'studio_release_min_gap_minutes', 0)
    if type(gap) is int and 0 < gap <= 720:
        try:
            last = float(state.get('last_public_continued_at') or 0)
        except (TypeError, ValueError):
            last = 0.0
        if last and 0 <= now - last < gap * 60:
            return 'spacing_after_last_video'
    return None
