"""Funding uncertainty must be visible without printing private accounting data."""
import sys
import types
from unittest.mock import Mock

import pytest

from test_studio_ui import ui_modules


@pytest.mark.parametrize('status, expected', [
    ({'enforced': True, 'status': 'blocked', 'reason_code': 'spend_not_initialized'},
     'Harcama geçmişi ve kullanılabilir bakiye doğrulanmayı bekliyor'),
    ({'enforced': True, 'status': 'blocked', 'reason_code': 'spend_funding_not_initialized'},
     'Bilinmeyen tutarlar sıfır kabul edilmiyor'),
    ({'enforced': True, 'status': 'blocked', 'reason_code': 'spend_funding_policy_expired'},
     'Kayıtlı bütçe veya abonelik bilgisi güncel değil'),
    ({'enforced': True, 'status': 'blocked', 'reason_code': 'private-secret<script>'},
     'Bütçe kaydı doğrulanamadığı için'),
    ({'enforced': False, 'status': 'not_enabled'}, 'Bütçe koruması kapalı'),
    ({'enforced': True, 'status': 'unavailable'}, 'Kullanılabilir tutar bilinmiyor'),
    ({'enforced': True, 'status': 'active', 'funding': None}, 'Bütçe durumu doğrulanamadı'),
])
def test_budget_notice_preserves_unknown_and_never_echoes_private_details(ui_modules, monkeypatch, status, expected):
    studio, _ = ui_modules
    reader = Mock(return_value={**status, 'private': 'private-secret<script>'})
    monkeypatch.setitem(sys.modules, 'app.services.production_spend_runtime',
                        types.SimpleNamespace(budget_status=reader))
    body = studio._production_budget_notice()
    assert expected in body
    assert 'private-secret' not in body and '<script>' not in body
    assert '0.00 USD' not in body
    reader.assert_called_once_with(read_timeout=2)


@pytest.mark.parametrize('remaining, expected', [(5_500_000, '5.50 USD'), (0, '0.00 USD'), (-2_000_000, '-2.00 USD')])
def test_verified_cash_remaining_preserves_debt_without_claiming_invoice(ui_modules, monkeypatch, remaining, expected):
    studio, _ = ui_modules
    monkeypatch.setitem(sys.modules, 'app.services.production_spend_runtime',
        types.SimpleNamespace(budget_status=lambda **_: {'enforced': True, 'status': 'active', 'funding': {
            'currency': 'USD', 'accounting': 'reserved_cash_upper_bound_not_invoice',
            'cash_remaining_micro': remaining}}))
    body = studio._production_budget_notice()
    assert expected in body
    assert ('Yeni ek harcama payı yok' in body) == (remaining <= 0)
    assert ('fatura toplamı değildir' in body) == (remaining > 0)


def test_dashboard_failure_is_visible_without_exception_or_secret(ui_modules, monkeypatch):
    studio, _ = ui_modules
    reader = Mock(side_effect=RuntimeError('private-credential'))
    monkeypatch.setitem(sys.modules, 'app.services.production_spend_runtime',
                        types.SimpleNamespace(budget_status=reader))
    body = studio._production_budget_notice()
    assert 'Bütçe durumu doğrulanamadı' in body and 'private-credential' not in body


def test_subscription_foundation_does_not_display_unknown_cash_as_zero_balance(ui_modules, monkeypatch):
    studio, _ = ui_modules
    monkeypatch.setitem(sys.modules, 'app.services.production_spend_runtime',
        types.SimpleNamespace(budget_status=lambda **_: {'enforced': True, 'status': 'active', 'funding': {
            'mode': 'cash_disabled_unknown_history', 'historical_cash_micro': None,
            'cash_spending_enabled': False, 'new_cash_allowance_micro': 0}}))
    body = studio._production_budget_notice()
    assert 'Ek API harcaması kapalı' in body and 'abonelik kredileri ayrı takip edilir' in body
    assert 'Önceki API faturaları' in body and '0.00 USD' not in body


def test_unauthenticated_studio_never_reads_budget(ui_modules, monkeypatch):
    studio, _ = ui_modules
    reader = Mock(side_effect=AssertionError('No unauthenticated budget access'))
    monkeypatch.setattr(studio, '_production_budget_notice', reader)
    body = studio.studio_home(studio_token=None).body.decode()
    assert 'Üretim bütçesi' not in body
    reader.assert_not_called()


def test_authenticated_studio_places_budget_before_creation(ui_modules, monkeypatch):
    studio, _ = ui_modules
    monkeypatch.setattr(studio, '_production_budget_notice', lambda: '<div>VISIBLE_BUDGET_HOLD</div>')
    monkeypatch.setattr(studio, '_production_channel_choices', lambda *_: '')
    body = studio.studio_create(studio_token='studio-secret').body.decode()
    assert body.index('VISIBLE_BUDGET_HOLD') < body.index('id="studio-form"')


def test_youtube_page_shows_budget_with_channel_state(ui_modules, monkeypatch):
    studio, routes = ui_modules
    reader = Mock(return_value='<div>VISIBLE_BUDGET_HOLD</div>')
    monkeypatch.setattr(studio, '_production_budget_notice', reader)
    body = routes.youtube_home(studio_token='studio-secret').body.decode()
    assert body.index('VISIBLE_BUDGET_HOLD') < body.index('channel-overview-host')
    reader.assert_called_once_with()
