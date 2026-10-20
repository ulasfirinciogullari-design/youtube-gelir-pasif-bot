from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app import content_plan_routes as routes
from app.config import settings
from app.services import content_plan as plans
from test_content_plan import case, CHANNEL


@pytest.fixture
def ui(case, monkeypatch):
    monkeypatch.setattr(settings, 'factory_api_token', 'test-owner-cookie')
    monkeypatch.setattr(settings, 'google_redirect_uri', 'https://studio.example/studio/youtube/callback')
    monkeypatch.setattr(routes, '_channels', lambda: [(case.profile,'Capital Corrupt')])
    app = FastAPI();app.include_router(routes.router)
    client = TestClient(app, base_url='https://studio.example')
    client.cookies.set(routes.COOKIE_NAME, 'test-owner-cookie')
    return case, client


def test_owner_page_has_actionable_order_not_technical_controls(ui):
    case,client=ui
    before={key:case.client.dump(key) for key in case.client.scan_iter()}
    response=client.get('/studio/plan')
    assert response.status_code==200
    assert 'Yayın planı' in response.text and 'Bölüm 5/8' in response.text
    assert '/studio/plan/add' in response.text and '/studio/plan/settings' in response.text
    assert 'redis' not in response.text.lower() and 'test-owner-cookie' not in response.text
    assert response.headers['cache-control']=='private, no-store'
    assert before=={key:case.client.dump(key) for key in case.client.scan_iter()}


def test_authentication_and_cross_origin_posts_are_rejected(ui):
    case,client=ui
    data={'channel_id':CHANNEL,'revision':case.document['revision'],'title':'A new story','brief':'Complete editorial brief','format':'shorts'}
    response=client.post('/studio/plan/add',data=data,headers={'Origin':'https://foreign.example'},follow_redirects=False)
    assert response.status_code==403
    client.cookies.clear()
    assert client.get('/studio/plan').status_code==401
    assert client.get('/studio/api/content-plan?channel='+CHANNEL).status_code==401
    assert len(plans.read(CHANNEL)['items'])==2


def test_add_and_reorder_future_independent_items_persist_without_production(ui):
    case,client=ui
    revision=case.document['revision']
    for title in ('Long documentary','A short story'):
        response=client.post('/studio/plan/add',data={'channel_id':CHANNEL,'revision':revision,
            'title':title,'brief':'A concrete source-backed story','format':'long' if title.startswith('Long') else 'shorts'},
            headers={'Origin':'https://studio.example'},follow_redirects=False)
        assert response.status_code==303 and 'saved=1' in response.headers['location']
        revision=plans.read(CHANNEL)['revision']
    last=plans.read(CHANNEL)['items'][-1]['id']
    response=client.post('/studio/plan/action',data={'channel_id':CHANNEL,'revision':revision,
        'item_id':last,'action':'up'},headers={'Origin':'https://studio.example'},follow_redirects=False)
    assert 'saved=1' in response.headers['location']
    assert plans.read(CHANNEL)['items'][2]['title']=='A short story'
    case.funding.assert_not_called()


def test_series_form_creates_ordered_dependency_chain(ui):
    case,client=ui
    response=client.post('/studio/plan/series',data={'channel_id':CHANNEL,'revision':case.document['revision'],
        'name':'Yeni seri','episodes':'Başlangıç\nDönüm noktası\nFinal','format':'shorts'},
        headers={'Origin':'https://studio.example'},follow_redirects=False)
    assert 'saved=1' in response.headers['location']
    entries=plans.read(CHANNEL)['items'][-3:]
    assert [v['series']['number'] for v in entries]==[1,2,3]
    assert entries[-1]['depends_on']==[entries[-2]['id']]
    assert len({v['series']['id'] for v in entries})==1


def test_editorial_text_is_escaped_everywhere(ui):
    case,client=ui
    entry=plans.item('<script>alert(1)</script>', '" onfocus="alert(2) <img src=x onerror=alert(3)>')
    plans.change(CHANNEL,case.document['revision'],'add',payload=entry)
    response=client.get('/studio/plan')
    assert '<script>alert(1)</script>' not in response.text
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in response.text
    assert '<img src=x' not in response.text


def test_failed_storage_does_not_render_an_empty_queue(ui, monkeypatch):
    _,client=ui
    def unavailable(*args,**kwargs):raise ConnectionError()
    monkeypatch.setattr(plans,'read',unavailable)
    assert client.get('/studio/plan').status_code==503
