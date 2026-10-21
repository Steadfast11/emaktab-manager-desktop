import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
import pytest
from fastapi.testclient import TestClient
from resilience import CircuitBreaker,CooldownScheduler,RateLimitDetector,Outcome,SessionStore,LocalIdentityRotator
from simulation import LocalSimulationServer,TestCase as Scenario,make_app
from cryptography.fernet import Fernet


def test_classification_and_server_wait():
    d=RateLimitDetector
    assert d.classify_http(429,{'Retry-After':'300'}).retry_after_seconds==300
    assert d.classify_http(403).outcome==Outcome.UNKNOWN
    assert d.classify_ui(['Неверные данные'],login_form=True).outcome==Outcome.BAD_CREDENTIALS
    assert d.classify_ui(['Неверные данные'],authenticated=True).outcome==Outcome.SUCCESS
    assert d.classify_ui(['Siz kirishga haddan ziyod harakat qildingiz']).outcome==Outcome.RATE_LIMIT
    assert CooldownScheduler(base=3,maximum=5,jitter=0).next_delay(5,900)==900


def test_pause_survives_restart_budget_is_persistent(tmp_path):
    now=[100000.0];clock=lambda:now[0];file=tmp_path/'b.sqlite3'
    b=CircuitBreaker(file,cooldown=CooldownScheduler(base=3,jitter=0),clock=clock)
    for _ in range(2):
        b.pause_rate()
        with pytest.raises(ValueError):b.assert_allowed()
        b=CircuitBreaker(file,cooldown=CooldownScheduler(base=3,jitter=0),clock=clock)
        with pytest.raises(ValueError):b.resume()
        now[0]=b.status()['blocked_until']+1
        b.resume();assert b.status()['state']=='HALF_OPEN'
    b.pause_rate();now[0]=b.status()['blocked_until']+1
    with pytest.raises(ValueError):b.resume()
    assert b.status()['recoveries_remaining']==0
    now[0]+=86401;b.resume();b.success();assert b.status()['state']=='IDLE'


def test_failed_recovery_and_restart_do_not_unlock(tmp_path):
    now=[100.0];clock=lambda:now[0]
    b=CircuitBreaker(tmp_path/'b',cooldown=CooldownScheduler(base=1,jitter=0),clock=clock)
    b.pause_rate();now[0]+=2;b.resume()
    assert CircuitBreaker(tmp_path/'b',clock=clock).status()['state']=='PAUSED_RATE_LIMIT'
    b.resume();b.rejected();assert b.status()['state']=='PAUSED_RATE_LIMIT'


def test_simulated_identity_does_not_override_global_limit(tmp_path):
    b=CircuitBreaker(tmp_path/'b',cooldown=CooldownScheduler(base=1,jitter=0))
    sim=LocalSimulationServer(b)
    with TestClient(make_app(sim)) as c:
        assert c.post('/step',json={'scenario':'SUCCESS'}).json()['outcome']=='SUCCESS'
        assert c.post('/step',json={'scenario':'BAD_CREDENTIALS'}).json()['outcome']=='BAD_CREDENTIALS'
        assert c.post('/step',json={'scenario':'RATE_LIMIT'}).json()['breaker']['state']=='PAUSED_RATE_LIMIT'
        assert c.post('/step',json={'scenario':'SUCCESS'}).status_code==409
        assert c.post('/login',json={'scenario':'SUCCESS','identity':'lab-device-02'}).status_code==429
        assert c.post('/login',json={'scenario':'RATE_LIMIT_EXPIRED'}).status_code==200
        # Backend expiry is not permission to reset the client breaker.
        assert c.post('/step',json={'scenario':'SUCCESS'}).status_code==409
        private=c.post('/login',json={'scenario':'SUCCESS','password':'must-not-echo'})
        assert private.status_code==422 and 'must-not-echo' not in private.text
        assert c.post('/reset').json()['state']=='IDLE'
        assert c.post('/step',json={'scenario':'SUCCESS'}).json()['outcome']=='SUCCESS'


def test_encrypted_sessions_and_local_fixture_scope(tmp_path):
    store=SessionStore(tmp_path/'s',Fernet(Fernet.generate_key()))
    state={'cookies':[{'name':'fixture','value':'secret-token'}]}
    store.save('one',state)
    assert b'secret-token' not in (tmp_path/'s').read_bytes()
    assert store.load('one')==state and store.load('two') is None
    store.delete('one');assert store.load('one') is None
    r=LocalIdentityRotator()
    assert r.next_fixture('http://127.0.0.1:8765/simulation/login').startswith('lab-device')
    with pytest.raises(ValueError):r.next_fixture('https://login.emaktab.uz/')



def test_browser_reuses_encrypted_session_without_resubmitting_password(monkeypatch,tmp_path):
    import asyncio,json
    import test_manager
    from playwright.async_api import async_playwright
    m=test_manager.m;test_manager.clear()
    monkeypatch.setattr(m,'breaker',CircuitBreaker(tmp_path/'reuse-breaker'))
    with m.connect() as c:
        c.execute('INSERT INTO accounts(id,label,secret) VALUES(?,?,?)',('reuse','fixture',m.VAULT.encrypt(json.dumps({'login':'not-submitted','password':'not-submitted'}).encode())))
    m.sessions.save('reuse',{'cookies':[{'name':'fixture_session','value':'fixture-only','domain':'login.emaktab.uz','path':'/','expires':-1,'httpOnly':True,'secure':True,'sameSite':'Lax'}],'origins':[]})
    observed=[]
    async def scenario():
        pw=await async_playwright().start()
        browser=await pw.chromium.launch()
        class Browser:
            def on(self, event, callback): browser.on(event, callback)
            async def new_context(self,**kwargs):
                context=await browser.new_context(**kwargs)
                async def route(r):
                    observed.append(r.request.method)
                    assert 'fixture_session=fixture-only' in r.request.headers.get('cookie','')
                    await r.fulfill(body='<a href="/logout">Exit fixture</a>',content_type='text/html')
                await context.route('https://login.emaktab.uz/**',route)
                return context
            async def close(self):await browser.close()
        class Chromium:
            async def launch(self,**kwargs):return Browser()
        class Runtime:
            chromium=Chromium()
            async def stop(self):pass
        class Starter:
            async def start(self):return Runtime()
        monkeypatch.setattr(m,'async_playwright',lambda:Starter())
        original_sleep=asyncio.sleep
        async def quick(seconds):await original_sleep(0)
        monkeypatch.setattr(m.asyncio,'sleep',quick)
        try:
            await m.Runner().run(False)
        finally:
            await pw.stop()
    asyncio.run(scenario())
    assert observed==['GET']
    assert m.public_accounts()[0]['status']=='success'
