"""Cheap local checks that keep a new scheduled video from starting to waste money.

Each check runs before a topic or job is reserved and makes no network call
to a paid provider. A hold only delays new work; it never relaxes a spend
guard, and a check that cannot read its inputs never holds.
"""
from datetime import datetime, timezone

from app.config import settings

REASONS = {
    'voice_not_configured': 'Seslendirme anahtarı veya ses seçilmemiş; yeni video başlatılmıyor.',
    'video_price_review_expired': 'Video fiyat listesinin süresi doldu; yenilenene kadar yeni video başlatılmıyor.',
    'daily_cap_near': 'Günlük harcama sınırına yaklaşıldı; yeni video yarın başlayacak.',
}


def _voice_missing() -> bool:
    from app.services.production_spend_runtime import enforcement_enabled
    # Only the direct ElevenLabs route is used while enforcement is off; the
    # funded routes carry their own voice preflights.
    if enforcement_enabled() or getattr(settings, 'studio_abacus_included_production', False) is True:
        return False
    if not str(getattr(settings, 'elevenlabs_api_key', '') or '').strip():
        return True
    from app.services.voice import get_selected_voice
    return not get_selected_voice().get('voice_id')


def _video_prices_expired(now: datetime) -> bool:
    if getattr(settings, 'studio_hold_on_expired_video_prices', True) is not True:
        return False
    from app.services import fal_video_catalog
    return fal_video_catalog.primary_enabled(settings) and now >= fal_video_catalog.VALID_UNTIL


def _daily_cap_near(now: datetime) -> bool:
    cap = float(getattr(settings, 'cost_daily_cap_usd', 0) or 0)
    reserve = float(getattr(settings, 'cost_short_admission_reserve_usd', 0) or 0)
    if cap <= 0 or reserve <= 0:
        return False
    from app.services import cost_meter
    spent = cost_meter.today_total(now=now)
    return spent is not None and spent + reserve > cap


def hold_reason(*, now: datetime | None = None) -> str | None:
    """The first reason a new scheduled video must wait, or None."""
    now = now or datetime.now(timezone.utc)
    for reason, check in (('voice_not_configured', _voice_missing),
                          ('video_price_review_expired', lambda: _video_prices_expired(now)),
                          ('daily_cap_near', lambda: _daily_cap_near(now))):
        try:
            if check():
                return reason
        except Exception:
            continue
    return None
