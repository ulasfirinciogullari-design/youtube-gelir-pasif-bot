from __future__ import annotations

from html import escape
import secrets

from fastapi import APIRouter, Cookie, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services.google_cloud_test_users import (
    GoogleCloudTestUserError,
    build_test_user_authorization_url,
    complete_test_user_authorization,
    discard_test_user_authorization_state,
    is_cloud_test_user_state,
)
from app.services.youtube_auth import STATE_TTL_SECONDS
from app.youtube_routes import (
    COOKIE_NAME,
    OAUTH_BINDING_COOKIE,
    OAUTH_CALLBACK_PATH,
    _require_auth,
    _require_same_origin,
    _shell,
    youtube_oauth_callback,
)


router = APIRouter()
CLOUD_BINDING_COOKIE = 'youtube_cloud_test_user_browser_binding'


def _delete_cloud_binding_cookie(response):
    response.delete_cookie(
        CLOUD_BINDING_COOKIE,
        path=OAUTH_CALLBACK_PATH,
        secure=True,
        httponly=True,
        samesite='lax',
    )
    return response


@router.get('/studio/youtube/fix-access', response_class=HTMLResponse)
def google_cloud_test_user_page(
    studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    _require_auth(studio_token)
    body = '''
<div class="hero"><div class="hero-copy"><div class="eyebrow">403 DÜZELTME</div><h1>Google erişimini otomatik düzelt</h1><div class="muted">Kanal Gmail’ini yaz. Sonraki Google ekranında bu otomasyonun Cloud projesini kurduğun ana hesabı seçip yalnızca “İzin ver” düğmesine bas.</div></div><div class="hero-tools"><span class="badge good">🔒 Şifre saklanmaz</span></div></div>
<section class="card"><div class="section-head"><div><span class="section-kicker">TEK ADIM</span><h2>Kanal hesabını test kullanıcısı ekle</h2><div class="muted">Bu işlem yalnızca yazdığın Google hesabını OAuth test listesine ekler. Başka Cloud ayarını değiştirmez.</div></div></div>
<form method="post" action="/studio/youtube/fix-access">
<div class="profile-grid"><label class="wide">Yeni kanalın Gmail adresi<input type="email" name="target_email" required maxlength="254" autocomplete="email" placeholder="ornek@gmail.com"></label></div>
<div class="actions"><button type="submit">Google ile düzelt</button><a class="btn secondary" href="/studio/youtube">Geri dön</a></div>
</form></section>
<div class="notice"><b>Google hesap seçimi geldiğinde:</b> Yeni kanal Gmail’ini değil, Google Cloud projesini ve OAuth istemcisini ilk kurduğun ana Google hesabını seç.</div>'''
    return _shell(
        body,
        title='Google 403 erişimini düzelt',
        same_origin_forms=True,
    )


@router.post('/studio/youtube/fix-access')
def google_cloud_test_user_start(
    request: Request,
    target_email: str = Form(...),
    studio_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    _require_auth(studio_token)
    _require_same_origin(request)
    browser_binding = secrets.token_urlsafe(32)
    try:
        authorization_url = build_test_user_authorization_url(
            target_email,
            browser_binding,
        )
    except GoogleCloudTestUserError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response = RedirectResponse(authorization_url, status_code=302)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.set_cookie(
        CLOUD_BINDING_COOKIE,
        browser_binding,
        max_age=STATE_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite='lax',
        path=OAUTH_CALLBACK_PATH,
    )
    return response


@router.get('/studio/youtube/callback', name='google_cloud_test_user_callback')
def google_cloud_or_youtube_callback(
    request: Request,
    state: str = '',
    code: str = '',
    error: str = '',
    youtube_binding: str | None = Cookie(default=None, alias=OAUTH_BINDING_COOKIE),
    cloud_binding: str | None = Cookie(default=None, alias=CLOUD_BINDING_COOKIE),
):
    if not is_cloud_test_user_state(state):
        return youtube_oauth_callback(
            request=request,
            state=state,
            code=code,
            error=error,
            oauth_binding=youtube_binding,
        )

    try:
        if error:
            discard_test_user_authorization_state(state, cloud_binding or '')
            return _delete_cloud_binding_cookie(_shell(
                '<div class="hero"><h1>Google yönetim izni verilmedi</h1></div><div class="notice">Hiçbir ayar değiştirilmedi. Tekrar denerken Cloud projesini kurduğun ana Google hesabını seçip izin ver.</div><div class="actions"><a class="btn secondary" href="/studio/youtube/fix-access">Tekrar dene</a></div>',
                title='Google izni tamamlanmadı',
                status_code=400,
            ))
        result = complete_test_user_authorization(
            code,
            state,
            cloud_binding or '',
        )
    except GoogleCloudTestUserError as exc:
        message = escape(str(exc) or 'Google test kullanıcısı eklenemedi')
        return _delete_cloud_binding_cookie(_shell(
            f'<div class="hero"><h1>Düzeltme tamamlanamadı</h1></div><div class="notice">{message}</div><div class="actions"><a class="btn secondary" href="/studio/youtube/fix-access">Tekrar dene</a><a class="btn secondary" href="/studio/youtube">YouTube’a dön</a></div>',
            title='Google 403 düzeltilemedi',
            status_code=400,
        ))

    target_email = escape(str(result.get('email') or ''))
    action = 'listeye eklendi' if result.get('added') else 'zaten listede'
    return _delete_cloud_binding_cookie(_shell(
        f'<div class="hero"><div class="hero-copy"><div class="eyebrow">TAMAMLANDI</div><h1>403 engeli kaldırıldı</h1><div class="muted"><b>{target_email}</b> test kullanıcıları listesinde {action}.</div></div><div class="hero-tools"><span class="badge good">✓ Hazır</span></div></div><section class="card"><h2>Şimdi kanal hesabını bağla</h2><p class="muted">YouTube bağlantısını yeniden başlat. Google hesap seçimi geldiğinde bu kez yeni kanal Gmail’ini seç.</p><div class="actions"><a class="btn success" href="/studio/youtube">YouTube bağlantısına dön</a></div></section>',
        title='Google erişimi düzeltildi',
    ))
