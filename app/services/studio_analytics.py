"""Owner performance view: honest empty states and separate optional consent."""
from html import escape

REASONS = {'needs_permission': 'İzleyici analizi izni gerekli', 'waiting': 'İlk rapor bekleniyor',
    'fresh': 'Rapor güncel', 'stale': 'Son rapor eski', 'unavailable': 'Rapor şu an okunamıyor'}


def render(model):
    sections = []
    for channel in model.get('channels', []):
        status = channel['status']
        action = ''
        if status == 'needs_permission':
            action = ('<form method="post" action="/studio/youtube/analytics/connect/'
                + escape(channel['channel_id'], quote=True) + '"><button type="submit">'
                'Google ile izleyici analizini bağla</button></form>')
        videos = channel.get('videos', {})
        rows = []
        for row in videos.values():
            curve = ''
            points = row.get('retention') or []
            if len(points) >= 2:
                ceiling = max(1.0, max(point[1] for point in points))
                coordinates = ' '.join(f'{point[0] * 300:.2f},{70 - point[1] / ceiling * 65:.2f}' for point in points)
                curve = ('<svg viewBox="0 0 300 80" role="img" aria-label="Video boyunca izleyici tutma eğrisi" '
                    'style="width:100%;max-width:440px;height:90px"><title>Başlangıçtan sona izlenme oranı</title>'
                    '<path d="M0 72H300" stroke="#d5dfdb" fill="none"/>'
                    '<polyline fill="none" stroke="#276759" stroke-width="2" points="' + coordinates + '"/></svg>')
            rows.append('<article class="overview-video"><div class="overview-video-title">'
                + escape(row['title'] or 'YouTube videosu') + '</div><div class="overview-video-meta">'
                + ('Shorts' if row['content_type'] == 'SHORTS' else 'Video')
                + f' · {row["views"]:,} izlenme · Ortalama {row["averageViewDuration"]:.1f} sn'
                + f' · %{row["averageViewPercentage"]:.1f} izlenen oran'
                + f' · {row["subscribersGained"]:,} kazanılan abone</div>' + curve + '</article>')
        count = sum(row['content_type'] == 'SHORTS' and row['engagedViews'] >= 100 for row in videos.values())
        note = ('Yeterli örnek var: yeni seri planlaması daha çok izlenen anlatım biçimlerini ipucu olarak kullanabilir.'
            if count >= 3 and status == 'fresh' else
            'Otomatik karşılaştırma için en az 3 Shorts videosunda 100’er etkileşimli izlenme bekleniyor.')
        interval = ''
        if channel.get('requested_start') and channel.get('requested_end'):
            interval = '<p class="tiny">İstenen dönem: ' + escape(str(channel['requested_start'])) + ' – ' + escape(str(channel['requested_end'])) + '</p>'
        sections.append('<section class="card overview-section"><div class="section-head"><h2>'
            + escape(channel['title']) + '</h2><span class="badge">' + REASONS.get(status, REASONS['unavailable'])
            + '</span></div>' + action + interval + (''.join(rows) or '<p class="muted">Henüz doğrulanmış izleyici raporu yok.</p>')
            + '<p class="metrics-note">' + note + '</p></section>')
    return ('<div class="hero"><div><div class="eyebrow">İZLEYİCİ ANALİZİ</div>'
        '<h1>İnsanlar ne kadar izliyor?</h1><p class="muted">İzlenme süresi, videoda kalma oranı ve kazanılan aboneler. '
        'Raporlar sunucuda otomatik yenilenir.</p></div></div>'
        '<div class="notice">Son 28 günlük rapor üç gün geriden istenir; YouTube verileri daha da gecikebilir. '
        'Shorts ve uzun videolar birbirleriyle kıyaslanmaz. Tekrar izlemeler nedeniyle oran %100’ü aşabilir.</div>'
        + (''.join(sections) or '<div class="empty">Önce Kanallar sayfasından bir YouTube kanalı bağla.</div>')
        + '<p class="metrics-note">Bu veriler fikir seçiminde yardımcıdır; erişim veya gelir garantisi değildir.</p>')
