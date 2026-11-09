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


CABINET_STATE = {'cookies': [{'name': 'sid', 'value': 'ok', 'domain': 'emaktab.uz', 'path': '/', 'expires': -1,
                              'httpOnly': True, 'secure': True, 'sameSite': 'Lax'}], 'origins': []}


def cabinet_browser(browser, public=False):
    """emaktab.uz is a cabinet WITHOUT any logout link: only cookie sid=ok may open it, others go to the login page."""
    class Shim:
        def on(self, event, callback):
            browser.on(event, callback)

        async def new_context(self, **kwargs):
            context = await browser.new_context(**kwargs)

            async def cabinet(route):
                if public or 'sid=ok' in route.request.headers.get('cookie', ''):
                    await route.fulfill(body='<p>Mening kabinetim</p>', content_type='text/html')
                else:
                    await route.fulfill(status=302, headers={'location': 'https://login.emaktab.uz/'})

            async def login(route):
                await route.fulfill(body='<input name="password">', content_type='text/html')
            await context.route('https://emaktab.uz/**', cabinet)
            await context.route('https://login.emaktab.uz/**', login)
            return context

        async def close(self):
            await browser.close()
    return Shim()


def test_session_proof_is_real_even_without_a_visible_logout_link():
    async def check():
        pw = await m.async_playwright().start()
        browser = await pw.chromium.launch()
        real_sleep = asyncio.sleep

        async def quick(seconds):
            await real_sleep(0)
        try:
            with patch.object(m.asyncio, 'sleep', quick):
                r = m.Runner()
                r.browser = cabinet_browser(browser)
                assert (await r.session_proof(CABINET_STATE, 'https://emaktab.uz/cabinet?token=secret'))[0] is True
                wrong = {**CABINET_STATE, 'cookies': [{**CABINET_STATE['cookies'][0], 'value': 'wrong'}]}
                ok, why = await r.session_proof(wrong, 'https://emaktab.uz/cabinet')
                assert not ok and 'kabinetni ochmadi' in why and 'token' not in why
                r.browser = cabinet_browser(browser, public=True)
                ok, why = await r.session_proof(CABINET_STATE, 'https://emaktab.uz/cabinet')
                assert not ok and 'kirishsiz ham ochiladi' in why
        finally:
            await browser.close()
            await pw.stop()
    asyncio.run(check())


def test_saved_session_is_reused_without_captcha_or_password(monkeypatch, tmp_path):
    clear()
    from resilience import CircuitBreaker
    monkeypatch.setattr(m, 'breaker', CircuitBreaker(tmp_path / 'reuse-breaker'))
    with m.connect() as c:
        c.execute('INSERT INTO accounts(id,label,secret) VALUES(?,?,?)', ('reuse2', 'fixture', m.VAULT.encrypt(json.dumps({'login': 'never-typed', 'password': 'never-typed'}).encode())))
    # The login page keeps showing its form (it does not leave by itself) and the cabinet has no logout link.
    m.sessions.save('reuse2', {**CABINET_STATE, '_home': 'https://emaktab.uz/cabinet'})

    async def scenario():
        pw = await m.async_playwright().start()
        browser = await pw.chromium.launch()
        shim = cabinet_browser(browser)

        class Chromium:
            async def launch(self, **kwargs):
                return shim

        class Runtime:
            chromium = Chromium()

            async def stop(self):
                pass

        class Starter:
            async def start(self):
                return Runtime()
        monkeypatch.setattr(m, 'async_playwright', lambda: Starter())
        real_sleep = asyncio.sleep

        async def quick(seconds):
            await real_sleep(0)
        monkeypatch.setattr(m.asyncio, 'sleep', quick)
        try:
            await m.Runner().run(False)
        finally:
            await pw.stop()
    asyncio.run(scenario())
    account = next(a for a in m.public_accounts() if a['id'] == 'reuse2')
    assert account['status'] == 'success', account
    stored = m.sessions.load('reuse2')
    assert stored['_home'] == 'https://emaktab.uz/cabinet'


def test_unproven_login_is_skipped_and_the_cycle_goes_on(monkeypatch, tmp_path):
    """One account that never shows proof must not stop the others (password is sent once, never repeated)."""
    clear()
    from resilience import CircuitBreaker
    monkeypatch.setattr(m, 'breaker', CircuitBreaker(tmp_path / 'skip-breaker'))
    with m.connect() as c:
        for i in range(3):
            c.execute('INSERT INTO accounts(id,label,secret) VALUES(?,?,?)', (f'skip{i}', f'fixture{i}', m.VAULT.encrypt(json.dumps({'login': f'l{i}', 'password': f'p{i}'}).encode())))
    sent = []

    async def scenario():
        pw = await m.async_playwright().start()
        browser = await pw.chromium.launch()

        class Shim:
            def on(self, event, callback):
                browser.on(event, callback)

            async def new_context(self, **kwargs):
                context = await browser.new_context(**kwargs)

                async def login(route):
                    if route.request.method == 'POST':
                        sent.append(1)
                    await route.fulfill(body='<input name="login"><input name="password"><button data-test-id="login-button">Kirish</button>', content_type='text/html')
                await context.route('https://login.emaktab.uz/**', login)
                return context

            async def close(self):
                await browser.close()

        class Chromium:
            async def launch(self, **kwargs):
                return Shim()

        class Runtime:
            chromium = Chromium()

            async def stop(self):
                pass

        class Starter:
            async def start(self):
                return Runtime()
        monkeypatch.setattr(m, 'async_playwright', lambda: Starter())
        real_sleep = asyncio.sleep

        async def quick(seconds):
            await real_sleep(0)
        monkeypatch.setattr(m.asyncio, 'sleep', quick)
        try:
            await m.Runner().run(False)
        finally:
            await pw.stop()
    asyncio.run(scenario())
    states = {a['id']: a['status'] for a in m.public_accounts()}
    assert states == {'skip0': 'unverified', 'skip1': 'unverified', 'skip2': 'unverified'}, states


def test_account_logins_are_decrypted_once_per_secret(monkeypatch):
    clear()
    with m.connect() as c:
        for i in range(40):
            c.execute('INSERT INTO accounts(id,label,secret) VALUES(?,?,?)', (f'speed{i}', f'f{i}', m.VAULT.encrypt(json.dumps({'login': f'l{i}', 'password': 'x'}).encode())))
    m._LOGINS.clear()
    real = m.VAULT.decrypt
    calls = []
    monkeypatch.setattr(m.VAULT, 'decrypt', lambda token: calls.append(1) or real(token))
    for _ in range(5):
        assert [a['login'] for a in m.public_accounts()][:2] == ['l0', 'l1']
    assert len(calls) == 40


def test_retry_picks_only_safe_accounts_and_never_a_rejected_password():
    clear()
    rows = [('ok', 'success', 'Kirish tasdiqlandi'), ('unv', 'unverified', 'Natija noma’lum'), ('bad', 'failed', 'Login/parol rad etildi; akkaunt o‘tkazildi'),
            ('net', 'failed', 'Mahalliy brauzer/tarmoq xatosi'), ('man', 'waiting', 'Qo‘lda kirish'), ('stp', 'cancelled', 'Foydalanuvchi ishni to‘xtatdi')]
    with m.connect() as c:
        for aid, status, detail in rows:
            c.execute('INSERT INTO accounts(id,label,secret,status,detail) VALUES(?,?,?,?,?)', (aid, aid, m.VAULT.encrypt(b'{"login":"l","password":"p"}'), status, detail))
    ids = m.retry_ids()
    assert ids == ['unv', 'net', 'stp']
    m.queue_accounts(None, ids)
    with m.connect() as c:
        status = dict(c.execute('SELECT id,status FROM accounts').fetchall())
    assert [k for k, v in status.items() if v == 'queued'] == ['unv', 'net', 'stp']
    assert status['bad'] == 'failed' and status['ok'] == 'success' and status['man'] == 'waiting'


def _add_accounts(n, prefix):
    with m.connect() as c:
        for i in range(n):
            c.execute('INSERT INTO accounts(id,label,secret) VALUES(?,?,?)', (f'{prefix}{i}', f'fixture{i}', m.VAULT.encrypt(json.dumps({'login': f'l{i}', 'password': f'p{i}'}).encode())))


def _fake_playwright(monkeypatch, scripts, launched):
    """A browser stand-in: each account's goto() runs the next script (hang, crash, error)."""
    class Page:
        url = 'about:blank'

        def __init__(self, script):
            self.script = script

        def set_default_timeout(self, t):
            pass

        def on(self, *a):
            pass

        async def goto(self, *a, **k):
            await self.script()

    class Context:
        def __init__(self, script):
            self.script = script

        async def route(self, *a, **k):
            pass

        async def new_page(self):
            return Page(self.script)

        async def close(self):
            pass

    class Browser:
        def __init__(self):
            self.callback = None

        def on(self, event, callback):
            self.callback = callback

        async def new_context(self, **kwargs):
            return Context(scripts.pop(0)(self))

        async def close(self):
            pass

    class Chromium:
        async def launch(self, **kwargs):
            b = Browser()
            launched.append(b)
            return b

    class Runtime:
        chromium = Chromium()

        async def stop(self):
            pass

    class Starter:
        async def start(self):
            return Runtime()
    monkeypatch.setattr(m, 'async_playwright', lambda: Starter())


def test_hanging_account_times_out_and_the_cycle_goes_on(monkeypatch, tmp_path):
    clear()
    from resilience import CircuitBreaker
    monkeypatch.setattr(m, 'breaker', CircuitBreaker(tmp_path / 'hang-breaker'))
    monkeypatch.setattr(m, 'ACCOUNT_SECONDS', {False: .3, True: .3})
    _add_accounts(3, 'hang')

    async def hang():
        await asyncio.Event().wait()

    async def boom():
        raise RuntimeError('net::ERR_INTERNET_DISCONNECTED')
    scripts = [lambda b: hang, lambda b: boom, lambda b: boom]
    _fake_playwright(monkeypatch, scripts, [])
    asyncio.run(m.Runner().run(False))
    states = {a['id']: (a['status'], a['detail']) for a in m.public_accounts()}
    assert states['hang0'][0] == 'failed' and 'Vaqt tugadi' in states['hang0'][1], states
    assert states['hang1'][0] == 'failed' and 'ERR_INTERNET_DISCONNECTED' in states['hang1'][1], states
    assert states['hang2'][0] == 'failed', states


def test_browser_crash_loses_only_that_account(monkeypatch, tmp_path):
    clear()
    from resilience import CircuitBreaker
    monkeypatch.setattr(m, 'breaker', CircuitBreaker(tmp_path / 'crash-breaker'))
    _add_accounts(3, 'crash')
    launched = []

    async def boom():
        raise RuntimeError('later')

    def crash(browser):
        async def go():
            browser.callback()  # the hidden browser dies in the middle of this account
            await asyncio.Event().wait()
        return go
    scripts = [crash, lambda b: boom, lambda b: boom]
    _fake_playwright(monkeypatch, scripts, launched)

    async def go():
        r = m.Runner()
        r.task = asyncio.create_task(r.run(False))  # as Runner.start() does
        await r.task
    asyncio.run(go())
    states = {a['id']: (a['status'], a['detail']) for a in m.public_accounts()}
    assert states['crash0'][0] == 'failed' and 'Brauzer kutilmaganda yopildi' in states['crash0'][1], states
    assert states['crash1'][1].startswith('Mahalliy brauzer/tarmoq xatosi') and states['crash2'][0] == 'failed', states
    assert len(launched) == 2  # the browser was started again for the remaining accounts


def test_run_only_selected_accounts_and_bulk_delete(monkeypatch):
    clear()
    _add_accounts(4, 'sel')
    started = []
    monkeypatch.setattr(m.runner, 'start', lambda *a, **k: started.append(a))
    with TestClient(m.app) as c:
        r = c.post('/api/run', json={'account_ids': ['sel1', 'sel3', 'ghost']}, headers=HEADERS)
        assert r.status_code == 200 and started[-1][4] == ['sel1', 'sel3'], started
        assert c.post('/api/run', json={'account_ids': ['ghost']}, headers=HEADERS).status_code == 400
        assert c.post('/api/accounts/delete', json={'ids': []}, headers=HEADERS).status_code == 422
        r = c.post('/api/accounts/delete', json={'ids': ['sel0', 'sel2', 'ghost']}, headers=HEADERS)
        assert r.json() == {'ok': True, 'deleted': 2}
        assert [a['id'] for a in m.public_accounts()] == ['sel1', 'sel3']


def test_selected_ids_limit_the_cycle(monkeypatch, tmp_path):
    clear()
    from resilience import CircuitBreaker
    monkeypatch.setattr(m, 'breaker', CircuitBreaker(tmp_path / 'only-breaker'))
    _add_accounts(4, 'only')

    async def boom():
        raise RuntimeError('x')
    _fake_playwright(monkeypatch, [lambda b: boom, lambda b: boom], [])
    asyncio.run(m.Runner().run(False, None, False, ['only1', 'only3']))
    states = {a['id']: a['status'] for a in m.public_accounts()}
    assert states == {'only0': 'idle', 'only1': 'failed', 'only2': 'idle', 'only3': 'failed'}, states


def test_delete_chosen_groups_keeps_accounts_and_protects_default():
    clear()
    with m.connect() as c:
        c.execute("DELETE FROM account_groups WHERE id!='default'")
        c.execute("INSERT INTO account_groups(id,name) VALUES('g1','4-B'),('g2','9-D'),('g3','5-V')")
    _add_accounts(2, 'grp')
    with m.connect() as c:
        c.execute("UPDATE accounts SET group_id='g2' WHERE id='grp1'")
    with TestClient(m.app) as c:
        r = c.post('/api/groups/delete', json={'ids': ['g1', 'g2', 'default', 'ghost']}, headers=HEADERS)
        assert r.json() == {'ok': True, 'deleted': 2, 'moved': 1}, r.text
        assert c.post('/api/groups/delete', json={'ids': []}, headers=HEADERS).status_code == 422
        names = {g['id']: g['name'] for g in c.get('/api/state').json()['groups']}
        assert set(names) == {'default', 'g3'}, names
    assert {a['id']: a['group_id'] for a in m.public_accounts()} == {'grp0': 'default', 'grp1': 'default'}
