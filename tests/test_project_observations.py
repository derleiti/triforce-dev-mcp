from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes import project_observations as route
from app.routes.client_auth import get_current_client


def app_with(engine, identity=None):
    app=FastAPI(); app.include_router(route.router, prefix="/v1")
    route.get_memory_engine=lambda: engine
    async def current():
        return identity or {"email":"admin@example.test","client_id":"c1"}
    app.dependency_overrides[get_current_client]=current
    return TestClient(app)


def engine(enabled=True):
    provider=AsyncMock(); provider.record.return_value=42
    return SimpleNamespace(
        settings=SimpleNamespace(
            episodic_memory_enabled=enabled,
            memory_record_enabled=enabled,
            memory_project_id="triforce",
        ),
        provider=provider,
    )


def test_records_scoped_verified_bugfix():
    e=engine(); client=app_with(e)
    r=client.post('/v1/project-observations',json={
        'project_key':'gimp-mcp','kind':'bugfix','title':'Studio endpoint fixed',
        'content':'Uses configured bind host','verified':True,'evidence':'92 tests passed',
        'repo':'gimp-mcp','commit':'abc123','tags':['mcp','gimp']
    })
    assert r.status_code==200
    body=r.json(); assert body['status']=='recorded' and body['verification_state']=='verified'
    kwargs=e.provider.record.await_args.kwargs
    assert kwargs['project'].startswith('triforce:account:')
    assert kwargs['project'].endswith(':project:gimp-mcp')
    assert kwargs['metadata']['event_type']=='project_observation'
    assert kwargs['metadata']['evidence']=='92 tests passed'


def test_todo_is_open_and_secret_is_redacted():
    e=engine(); client=app_with(e)
    r=client.post('/v1/project-observations',json={
        'project_key':'helper','kind':'todo','title':'Fix portal scroll',
        'content':'Authorization: Bearer supersecret'
    })
    assert r.status_code==200 and r.json()['verification_state']=='open'
    kwargs=e.provider.record.await_args.kwargs
    assert 'supersecret' not in kwargs['text']


def test_disabled_is_fail_open():
    e=engine(False); client=app_with(e)
    r=client.post('/v1/project-observations',json={
        'project_key':'helper','kind':'fact','title':'x'
    })
    assert r.status_code==200 and r.json()=={'status':'disabled'}
    e.provider.record.assert_not_awaited()


def test_invalid_project_key_rejected():
    e=engine(); client=app_with(e)
    r=client.post('/v1/project-observations',json={
        'project_key':'../../etc/passwd','kind':'fact','title':'x'
    })
    assert r.status_code==422


def test_search_uses_same_account_project_scope():
    e=engine(); e.provider.search.return_value=[{
        'id':7,'title':'bugfix: fixed thing','metadata':{
            'kind':'bugfix','summary':'fixed details','verification_state':'verified',
            'evidence':'pytest passed','repo':'gimp-mcp','commit':'abc','source':'test','tags':['gimp']
        }
    }]
    client=app_with(e)
    r=client.post('/v1/project-observations/search',json={'project_key':'gimp-mcp','query':'fixed','limit':4})
    assert r.status_code==200
    body=r.json(); assert body['status']=='ok' and body['results'][0]['summary']=='fixed details'
    args=e.provider.search.await_args.args
    assert args[0]=='fixed' and args[1].startswith('triforce:account:') and args[1].endswith(':project:gimp-mcp')
    assert args[2]==4


def test_different_accounts_get_different_project_scopes():
    e1=engine(); c1=app_with(e1,{'email':'one@example.test','client_id':'1'})
    c1.post('/v1/project-observations',json={'project_key':'helper','kind':'fact','title':'x'})
    p1=e1.provider.record.await_args.kwargs['project']
    e2=engine(); c2=app_with(e2,{'email':'two@example.test','client_id':'2'})
    c2.post('/v1/project-observations',json={'project_key':'helper','kind':'fact','title':'x'})
    p2=e2.provider.record.await_args.kwargs['project']
    assert p1 != p2
