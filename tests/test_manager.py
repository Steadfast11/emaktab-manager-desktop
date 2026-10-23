import asyncio
import importlib.util
import json
import os
import tempfile
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

os.environ['EMAKTAB_DATA_DIR'] = tempfile.mkdtemp(prefix='emaktab-test-')
sys.path.insert(0, str(Path(__file__).parents[1]))
spec = importlib.util.spec_from_file_location('manager', Path(__file__).parents[1] / 'app.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
from fastapi.testclient import TestClient

HEADERS = {'Origin': 'http://127.0.0.1:8765', 'X-Manager': '1'}


def clear():
    with m.connect() as c:
        c.execute('DELETE FROM accounts')
        c.execute('DELETE FROM logs')


def test_private_storage_and_validation():
    clear()
    with TestClient(m.app) as c:
        body = {'label': 'Private', 'login': 'private-login', 'password': 'unique-password'}
        assert c.post('/api/accounts', json=body).status_code == 403
        assert c.post('/api/accounts', json=body, headers={**HEADERS, 'Origin':'https://evil.test'}).status_code == 403
        assert c.get('/api/state', headers={'Host':'evil.test'}).status_code == 403
        aid = c.post('/api/accounts', json=body, headers=HEADERS).json()['id']
        out = c.get('/api/state').text
        assert 'private-login' in out and 'unique-password' not in out
        with m.connect() as db:
            token = db.execute('SELECT secret FROM accounts WHERE id=?', (aid,)).fetchone()[0]
        assert b'unique-password' not in token
        assert json.loads(m.VAULT.decrypt(token))['password'] == 'unique-password'
        invalid = {**body, 'label':''}
        response = c.post('/api/accounts', json=invalid, headers=HEADERS)
        assert response.status_code == 422 and 'unique-password' not in response.text
        for _ in range(4):
            assert c.post('/api/accounts', json=body, headers=HEADERS).status_code == 200
        assert c.post('/api/accounts', json=body, headers=HEADERS).status_code == 200
        assert c.put('/api/schedule', json={'time':'29:77','enabled':True}, headers=HEADERS).status_code == 422
        assert c.delete('/api/accounts/'+aid, headers=HEADERS).status_code == 200
        assert len(c.get('/api/state').json()['accounts']) == 5


def test_real_browser_authentication_and_isolation():
    async def check():
        async with m.async_playwright() as pw:
            browser = await pw.chromium.launch()
            c1, c2 = await browser.new_context(), await browser.new_context()
            await c1.add_cookies([{'name':'session','value':'one','domain':'emaktab.uz','path':'/'}])
            assert not await c2.cookies()
            p = await c1.new_page()
            await p.route('https://emaktab.uz/**', lambda route: route.fulfill(body='<a href="/logout">Exit</a>'))
            await p.goto('https://emaktab.uz/private')
            assert await m.authenticated(p)
            await p.set_content('<input name="password"><a href="/logout">Exit</a>')
            assert not await m.authenticated(p)
            await p.set_content('<p>Redirect alone</p>')
            assert not await m.authenticated(p)
            await p.set_content('<input name="Captcha.Input">')
            assert await m.visible(p, m.CHALLENGE)
            r = m.Runner()
            with patch.object(m, 'update') as u:
                assert not await r.wait_manual(p, 'fake', False)
                assert u.call_args.args[1] == 'waiting'
            await browser.close()
    asyncio.run(check())


def test_failure_does_not_skip_remaining_accounts():
    clear()
    with TestClient(m.app) as c:
        for i in range(5):
            c.post('/api/accounts', json={'label':str(i),'login':'dummy','password':'dummy'}, headers=HEADERS)
    async def check():
        browser = AsyncMock()
        # Each context fails before any credentials can be submitted.
        browser.new_context.side_effect = m.BrowserError('must never be logged secret')
        browser.on = __import__("unittest.mock", fromlist=["Mock"]).Mock()
        pw = AsyncMock()
        pw.chromium.launch.return_value = browser
        starter = AsyncMock()
        starter.start.return_value = pw
        r = m.Runner()
        real_sleep = asyncio.sleep
        async def quick_sleep(n):
            await real_sleep(0)
        with patch.object(m, 'async_playwright', return_value=starter), patch.object(m.asyncio, 'sleep', quick_sleep):
            await r.run(False)
        assert browser.new_context.call_count == 5
        assert all(a['status'] == 'failed' for a in m.public_accounts())
        with m.connect() as db:
            assert 'secret' not in str(list(db.execute('SELECT message FROM logs')))
    asyncio.run(check())


def test_closed_browser_cleanup_is_idempotent():
    async def check():
        r = m.Runner()
        r.browser = type('Browser', (), {'close': AsyncMock(side_effect=RuntimeError('closed'))})()
        pw = type('PW', (), {'stop': AsyncMock()})()
        r.pw = pw
        await r.close()
        await r.close()
        assert r.browser is None and r.pw is None
        pw.stop.assert_awaited_once()
    asyncio.run(check())


def test_manual_browser_close_cancels_active_run():
    async def check():
        r = m.Runner()
        browser = object()
        r.browser = browser
        r.task = asyncio.create_task(asyncio.sleep(60))
        r.browser_disconnected(browser)
        try:
            await r.task
        except asyncio.CancelledError:
            pass
        assert r.task.cancelled()
    asyncio.run(check())


def test_unknown_result_stops_batch_without_locking_next_run(tmp_path):
    from resilience import CircuitBreaker, RateLimitDetector
    with patch.object(m, 'breaker', CircuitBreaker(tmp_path / 'unknown.sqlite')):
        try:
            m.halt_cycle('missing', RateLimitDetector.classify_ui([]))
        except m.StopCycle:
            pass
        assert m.breaker.status()['state'] == 'IDLE'


def test_editor_loads_existing_values_only_through_local_post():
    clear()
    with TestClient(m.app) as client:
        values = {'label':'Example', 'login':'fixture-login', 'password':'fixture-secret'}
        aid = client.post('/api/accounts', json=values, headers=HEADERS).json()['id']
        url = '/api/accounts/' + aid + '/editor'
        assert client.post(url).status_code == 403
        response = client.post(url, json={}, headers=HEADERS)
        assert response.json() == values
        assert response.headers['cache-control'] == 'no-store'
        assert 'fixture-secret' not in client.get('/api/state').text
        assert client.post('/api/accounts/missing/editor', json={}, headers=HEADERS).status_code == 404


def test_account_groups_rename_move_and_private_storage():
    clear()
    with TestClient(m.app) as client:
        group = client.post('/api/groups', json={'name':'Oila'}, headers=HEADERS).json()['id']
        body = {'label':'Fixture','login':'group-fixture','password':'fixture-secret','group_id':group}
        aid = client.post('/api/accounts', json=body, headers=HEADERS).json()['id']
        with m.connect() as db:
            before = db.execute('SELECT secret FROM accounts WHERE id=?',(aid,)).fetchone()[0]
        assert client.put('/api/groups/'+group,json={'name':'Yangi nom'},headers=HEADERS).status_code == 200
        state = client.get('/api/state').json()
        assert next(g['name'] for g in state['groups'] if g['id']==group)=='Yangi nom'
        assert next(a['group_id'] for a in state['accounts'] if a['id']==aid)==group
        with m.connect() as db:
            assert db.execute('SELECT secret FROM accounts WHERE id=?',(aid,)).fetchone()[0] == before
        assert client.put('/api/accounts/'+aid,json={**body,'group_id':'default'},headers=HEADERS).status_code==200
        assert client.get('/api/state').json()['accounts'][0]['group_id']=='default'
        assert client.post('/api/accounts',json={**body,'group_id':'missing'},headers=HEADERS).status_code==404
        assert client.post('/api/groups',json={'name':'   '},headers=HEADERS).status_code==400
        assert client.post('/api/groups',json={'name':'Blocked'}).status_code==403


def test_web_only_defaults_and_removed_android_api(tmp_path):
    from resilience import CircuitBreaker
    from unittest.mock import Mock
    clear()
    with patch.object(m,'breaker',CircuitBreaker(tmp_path/'web-only.sqlite')), TestClient(m.app) as client:
        aid=client.post('/api/accounts',json={'label':'Fixture','login':'offline','password':'fixture'},headers=HEADERS).json()['id']
        assert m.Run().mode=='web' and m.Schedule(time='07:00').mode=='web'
        assert not hasattr(m.Runner,'run_android')
        assert client.post('/api/run',json={'mode':'android'},headers=HEADERS).status_code==422
        assert client.put('/api/schedule',json={'mode':'android'},headers=HEADERS).status_code==422
        assert client.get('/api/android/status').status_code==404
        assert client.post('/api/android/prepare',json={},headers=HEADERS).status_code==404
        with patch.object(m.runner,'start',Mock()) as start:
            assert client.post('/api/run',json={'account_id':aid,'headed':True},headers=HEADERS).status_code==200
            start.assert_called_once_with(True,'web',aid)
        assert client.get('/api/state').json()['schedule']['mode']=='web'


def test_browser_start_failure_reports_safe_code_without_secret():
    clear()
    with TestClient(m.app) as client:
        client.post('/api/accounts', json={'label':'Startup fixture','login':'fixture','password':'fixture'}, headers=HEADERS)
    async def check():
        r = m.Runner()
        with patch.object(r, 'start_browser', AsyncMock(side_effect=NotImplementedError('never disclose fixture-secret'))):
            await r.run(True)
        account = m.public_accounts()[0]
        assert account['status'] == 'failed'
        assert 'NotImplementedError/' in account['detail']
        assert 'fixture-secret' not in account['detail']
    asyncio.run(check())
