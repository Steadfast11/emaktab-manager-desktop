import asyncio
import json
import os
import secrets
import sys
import traceback
from typing import Literal
import sqlite3
from resilience import ClosingConnection, CircuitBreaker, CooldownScheduler, RateLimitDetector, Outcome, SessionStore, session_evaluation
from simulation import LocalSimulationServer, make_app
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from playwright.async_api import async_playwright, TimeoutError as BrowserTimeout, Error as BrowserError

APP_PORT = int(os.environ.get('EMAKTAB_PORT', '8765'))
BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get('EMAKTAB_DATA_DIR', BASE / 'data'))
DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
os.chmod(DATA, 0o700)
KEY = DATA / 'vault.key'
if not KEY.exists():
    fd = os.open(KEY, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(Fernet.generate_key())
if KEY.is_symlink():
    raise RuntimeError('Vault key must not be a symlink')
os.chmod(KEY, 0o600)
VAULT = Fernet(KEY.read_bytes())
DB = DATA / 'manager.sqlite3'
TZ = ZoneInfo('Asia/Samarkand')
LOGIN_URL = 'https://login.emaktab.uz/'
LOGOUT = 'a[href*="/logout"], a[href*="/logoff"], form[action*="/logout"], [data-test-id="logout-button"]'
CHALLENGE = 'input[name="Captcha.Input"], iframe[src*="recaptcha"], iframe[src*="hcaptcha"], input[autocomplete="one-time-code"], input[name="otp"], input[name="code"], input[name="verificationCode"]'


def now():
    return datetime.now(TZ).isoformat(timespec='seconds')


def connect():
    c = sqlite3.connect(DB, factory=ClosingConnection)
    c.row_factory = sqlite3.Row
    return c


with connect() as c:
    c.executescript('''CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY, label TEXT NOT NULL, secret BLOB NOT NULL, status TEXT NOT NULL DEFAULT 'idle', detail TEXT NOT NULL DEFAULT '', updated TEXT);
    CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY, account_id TEXT, time TEXT, message TEXT);
    CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER DEFAULT 0, time TEXT DEFAULT '07:00', last_date TEXT DEFAULT '');
    INSERT OR IGNORE INTO settings(id) VALUES(1);''')
    c.execute('CREATE TABLE IF NOT EXISTS account_groups(id TEXT PRIMARY KEY, name TEXT NOT NULL)')
    c.execute("INSERT OR IGNORE INTO account_groups(id,name) VALUES('default','Asosiy')")
    if 'group_id' not in [r[1] for r in c.execute('PRAGMA table_info(accounts)')]:
        c.execute("ALTER TABLE accounts ADD COLUMN group_id TEXT NOT NULL DEFAULT 'default'")
    if 'mode' not in [r[1] for r in c.execute('PRAGMA table_info(settings)')]:
        c.execute("ALTER TABLE settings ADD COLUMN mode TEXT NOT NULL DEFAULT 'web'")
    c.execute("UPDATE settings SET mode='web' WHERE mode!='web'")
    c.execute("UPDATE accounts SET status='interrupted',detail='Oldingi ish server qayta ishga tushganda uzildi' WHERE status IN ('queued','running','waiting')")
os.chmod(DB, 0o600)


_LOGINS = {}


def login_of(aid, secret):
    """Decrypting every account on every poll is what made old computers crawl: remember the login per unchanged secret."""
    hit = _LOGINS.get(aid)
    if hit and hit[0] == secret:
        return hit[1]
    login = json.loads(VAULT.decrypt(secret))['login']
    _LOGINS[aid] = (secret, login)
    return login


def public_accounts():
    with connect() as c:
        rows = list(c.execute('SELECT id,label,secret,status,detail,updated,group_id FROM accounts ORDER BY rowid'))
    result = []
    for row in rows:
        account = dict(row)
        account['login'] = login_of(account['id'], account.pop('secret'))
        result.append(account)
    return result


def account_count():
    with connect() as c:
        return c.execute('SELECT count(*) FROM accounts').fetchone()[0]


def account_status(aid):
    with connect() as c:
        row = c.execute('SELECT status FROM accounts WHERE id=?', (aid,)).fetchone()
    return row[0] if row else None


REJECTED_DETAIL = 'Login/parol rad etildi'


def retry_ids():
    """Accounts worth another try: unproven/failed/stopped ones. A rejected password is never resent."""
    with connect() as c:
        return [r[0] for r in c.execute(
            "SELECT id FROM accounts WHERE status IN ('failed','unverified','interrupted','cancelled') AND detail NOT LIKE ? ORDER BY rowid",
            (REJECTED_DETAIL + '%',))]


def queue_accounts(account_id=None, only=None):
    stamp = now()
    with connect() as c:
        if only is not None:
            ids = list(only)
        else:
            ids = [r[0] for r in c.execute('SELECT id FROM accounts WHERE id=? ORDER BY rowid', (account_id,))] if account_id else [r[0] for r in c.execute('SELECT id FROM accounts ORDER BY rowid')]
        for aid in ids:
            c.execute("UPDATE accounts SET status='queued',detail=?,updated=? WHERE id=?", ('Navbatda: brauzer sessiyasi tekshiriladi', stamp, aid))
            c.execute('INSERT INTO logs(account_id,time,message) VALUES(?,?,?)', (aid, stamp, 'Navbatda: brauzer sessiyasi tekshiriladi'))
        c.execute('DELETE FROM logs WHERE id NOT IN (SELECT id FROM logs ORDER BY id DESC LIMIT 300)')


def update(aid, status, detail):
    with connect() as c:
        c.execute('UPDATE accounts SET status=?,detail=?,updated=? WHERE id=?', (status, detail, now(), aid))
        c.execute('INSERT INTO logs(account_id,time,message) VALUES(?,?,?)', (aid, now(), detail))
        c.execute('DELETE FROM logs WHERE id NOT IN (SELECT id FROM logs ORDER BY id DESC LIMIT 300)')


_VISIBLE = '''(s) => { for (const e of document.querySelectorAll(s)) { const r = e.getBoundingClientRect(); if (r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden') return true; } return false; }'''
_PROBE = '''([logout, challenge]) => { const v = (s) => { for (const e of document.querySelectorAll(s)) { const r = e.getBoundingClientRect(); if (r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden') return true; } return false; };
  return {password: v('input[name="password"]'), logout: v(logout), challenge: v(challenge), text: document.body ? document.body.innerText : ''}; }'''


async def evaluate_retry(page, script, arg, default):
    # One browser round trip instead of many; a page that is navigating right now is retried briefly.
    for _ in range(3):
        try:
            return await page.evaluate(script, arg)
        except BrowserError:
            await asyncio.sleep(.2)
    return default


async def visible(page, selector):
    return bool(await evaluate_retry(page, _VISIBLE, selector, False))


async def page_probe(page):
    return await evaluate_retry(page, _PROBE, [LOGOUT, CHALLENGE], {'password': False, 'logout': False, 'challenge': False, 'text': ''})


def light_router(block_images):
    """Fonts and media are never needed to log in; images only when no person has to see a captcha."""
    heavy = {'font', 'media'} | ({'image'} if block_images else set())

    async def handler(route):
        if route.request.resource_type in heavy:
            await route.abort()
        else:
            await route.fallback()
    return handler


async def settle(page, quiet=5, limit=15):
    """Wait until the address stops changing (about 1 s) instead of a fixed 3 s pause."""
    last, stable = page.url, 0
    for _ in range(limit):
        await asyncio.sleep(.2)
        if page.url != last:
            last, stable = page.url, 0
        else:
            stable += 1
            if stable >= quiet:
                return


LOGIN_HOST = urlparse(LOGIN_URL).hostname or ''


def emaktab_host(host):
    return host == 'emaktab.uz' or host.endswith('.emaktab.uz')


def clean_url(url):
    parts = urlparse(url)
    return f'{parts.scheme}://{parts.netloc}{parts.path}'


async def authenticated(page):
    # Cookies or a redirect alone do not prove authentication. Fail closed.
    host = urlparse(page.url).hostname or ''
    if not emaktab_host(host):
        return False
    seen = await page_probe(page)
    return not seen['password'] and seen['logout']


async def in_cabinet(page):
    # A hint only, never proof: an eMaktab page that is not the login host and shows no login form or challenge.
    # The logout link may sit inside a menu, so it cannot be required. Proof is Runner.session_proof().
    host = urlparse(page.url).hostname or ''
    if not emaktab_host(host) or host == LOGIN_HOST:
        return False
    seen = await page_probe(page)
    return not seen['password'] and not seen['challenge']


with connect() as c:
    first_breaker_install = not c.execute("SELECT name FROM sqlite_master WHERE name='breaker'").fetchone()
    has_accounts = c.execute('SELECT count(*) FROM accounts').fetchone()[0] > 0
breaker = CircuitBreaker(DB)
sessions = SessionStore(DB, VAULT)
# Existing live installation has a user-reported unresolved limit; do not resume it on deployment.
if first_breaker_install and has_accounts:
    breaker.pause_rate()
lab_breaker = CircuitBreaker(DATA / 'lab.sqlite3', cooldown=CooldownScheduler(base=3, maximum=60, jitter=0))
lab = LocalSimulationServer(lab_breaker)
os.chmod(DATA / 'lab.sqlite3', 0o600)


class BrowserMissing(RuntimeError):
    pass


LIGHT_ARGS = ['--disable-extensions', '--disable-background-networking', '--disable-sync', '--disable-default-apps', '--disable-component-update', '--mute-audio', '--no-first-run', '--no-default-browser-check']


def browser_channels():
    """System browsers first (nothing is bundled): Edge exists on every Windows, Chrome on macOS."""
    return ['msedge', 'chrome', 'chromium'] if sys.platform == 'win32' else ['chrome', 'msedge', 'chromium']


class StopCycle(Exception):
    pass


class SkipAccount(Exception):
    """This account failed and its status is already written: go on with the next one."""


def halt_cycle(aid, detection):
    if detection.outcome == Outcome.RATE_LIMIT:
        breaker.pause_rate(detection)
        update(aid, 'rate_limited', 'Kirish limiti: butun sikl to‘xtatildi')
        reason = 'Limit tufayli bu akkauntga urinish qilinmadi'
    else:
        # An unknown result stops this batch; only a real rate-limit keeps Run locked.
        if breaker.status()['state'] == 'HALF_OPEN':
            breaker.pause_review()
        update(aid, 'unverified', 'Natija noma’lum: qayta urinishdan oldin qo‘lda tekshirish kerak')
        reason = 'Noma’lum natija tufayli bu akkauntga urinish qilinmadi'
    for a in public_accounts():
        if a['status'] == 'queued':
            update(a['id'], 'skipped', reason)
    raise StopCycle()


async def page_detection(page, rate_response=None):
    if rate_response and rate_response[0]:
        return rate_response[0]
    seen = await page_probe(page)
    host = urlparse(page.url).hostname or ''
    proven = emaktab_host(host) and not seen['password'] and seen['logout']
    return RateLimitDetector.classify_ui([seen['text']], authenticated=proven, login_form=seen['password'])


class Runner:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.task = None
        self.browser = None
        self.pw = None
        self.contexts = []
        self.current = None
        self._public = (None, {})

    async def close(self):
        browser, pw = self.browser, self.pw
        self.browser = self.pw = None
        self.contexts = []
        for resource, method in ((browser, 'close'), (pw, 'stop')):
            if resource:
                try:
                    await asyncio.wait_for(getattr(resource, method)(), timeout=5)
                except Exception:
                    pass

    def browser_disconnected(self, browser):
        if self.browser is browser and self.task and not self.task.done():
            self.task.cancel()

    async def start_browser(self, headed=False):
        await self.close()
        self.pw = await async_playwright().start()
        last = None
        for channel in browser_channels():
            try:
                self.browser = await self.pw.chromium.launch(headless=not headed, channel=channel, args=LIGHT_ARGS + ([] if headed else ['--disable-gpu']))
                return self.browser
            except BrowserError as error:
                last = error
        raise BrowserMissing('Microsoft Edge yoki Google Chrome topilmadi. Birini o‘rnating.') from last

    async def run(self, headed=False, account_id=None, retry=False):
        async with self.lock:
            try:
                only = retry_ids() if retry else None
                queue_accounts(account_id, only)
                await self.start_browser(headed)
                active_browser = self.browser
                active_browser.on('disconnected', lambda: self.browser_disconnected(active_browser))
                with connect() as c:
                    rows = list(c.execute('SELECT * FROM accounts WHERE id=? ORDER BY rowid', (account_id,))) if account_id else list(c.execute('SELECT * FROM accounts ORDER BY rowid'))
                if only is not None:
                    rows = [r for r in rows if r['id'] in only]
                for row in rows:
                    aid = row['id']
                    self.current = aid
                    update(aid, 'running', 'Alohida sessiya tekshirilmoqda')
                    context = None
                    submitted = False
                    try:
                        saved = None if breaker.status()['state'] == 'HALF_OPEN' else sessions.load(aid)
                        home = saved.pop('_home', None) if saved else None
                        kwargs = {'storage_state': saved} if saved else {}
                        context = await self.browser.new_context(locale='uz-UZ', timezone_id='Asia/Samarkand', **kwargs)
                        self.contexts.append(context)
                        await context.route('**/*', light_router(not headed))
                        page = await context.new_page()
                        page.set_default_timeout(15000)
                        rate = [None]
                        def response_seen(response):
                            host = urlparse(response.url).hostname or ''
                            if response.status == 429 and (host == 'emaktab.uz' or host.endswith('.emaktab.uz')):
                                rate[0] = RateLimitDetector.classify_http(429, response.headers)
                        page.on('response', response_seen)
                        # A saved session goes straight to the cabinet: no login page load, fewer requests to the site.
                        reused = bool(saved and home) and await self.reuse_session(page, saved, home)
                        initial = None
                        if not reused:
                            await page.goto(LOGIN_URL, wait_until='domcontentloaded', timeout=30000)
                            initial = await page_detection(page, rate)
                            if initial.outcome == Outcome.RATE_LIMIT:
                                halt_cycle(aid, initial)
                            if initial.outcome != Outcome.SUCCESS and saved and not home:
                                reused = await self.reuse_session(page, saved, home)
                        if not reused and initial.outcome != Outcome.SUCCESS:
                            sessions.delete(aid)
                            if await visible(page, CHALLENGE):
                                ok = await self.wait_manual(page, aid, headed, rate)
                            else:
                                credential = json.loads(VAULT.decrypt(row['secret']))
                                await page.locator('input[name="login"]').fill(credential['login'])
                                await page.locator('input[name="password"]').fill(credential['password'])
                                matches = (await page.locator('input[name="login"]').input_value() == credential['login'] and await page.locator('input[name="password"]').input_value() == credential['password'])
                                del credential
                                if not matches:
                                    update(aid, 'failed', 'Login/parolni maydonga kiritib bo‘lmadi; akkaunt o‘tkazildi')
                                    raise SkipAccount()
                                submitted = True
                                await page.locator('[data-test-id="login-button"]').click()
                                ok = False
                                seen = 0
                                for _ in range(30):
                                    detection = await page_detection(page, rate)
                                    if detection.outcome == Outcome.RATE_LIMIT:
                                        halt_cycle(aid, detection)
                                    if detection.outcome == Outcome.SUCCESS:
                                        ok = True
                                        break
                                    if detection.outcome == Outcome.BAD_CREDENTIALS:
                                        update(aid, 'failed', 'Login/parol rad etildi; akkaunt o‘tkazildi')
                                        breaker.rejected()
                                        if breaker.status()['state'] in ('PAUSED_RATE_LIMIT','PAUSED_REVIEW'):
                                            raise StopCycle()
                                        break
                                    if await in_cabinet(page):
                                        seen += 1
                                        if seen == 3 and (await self.session_proof(await context.storage_state(), page.url))[0]:
                                            ok = True
                                            break
                                    else:
                                        seen = 0
                                    if await visible(page, CHALLENGE):
                                        ok = await self.wait_manual(page, aid, headed, rate)
                                        break
                                    await asyncio.sleep(1)
                        else:
                            ok = True
                        if ok:
                            state = await context.storage_state()
                            host = urlparse(page.url).hostname or ''
                            home_url = clean_url(page.url) if emaktab_host(host) and host != LOGIN_HOST else home
                            sessions.save(aid, {**state, '_home': home_url} if home_url else state)
                            breaker.success()
                            update(aid, 'success', 'Kirish tasdiqlandi; brauzer sessiyasi shifrlab saqlandi')
                        elif account_status(aid) in ('running', 'queued'):
                            # No proof either way: record it, never retry the password, go on with the next account.
                            update(aid, 'unverified', 'Natija noma’lum: parol qayta yuborilmadi, keyingi akkauntga o‘tildi')
                    except SkipAccount:
                        pass
                    except StopCycle:
                        raise
                    except asyncio.CancelledError:
                        update(aid, 'cancelled', 'Foydalanuvchi ishni to‘xtatdi')
                        raise
                    except Exception:
                        if submitted:
                            update(aid, 'unverified', 'Kirish yuborildi, natija noma’lum (xato); parol qayta yuborilmadi, keyingi akkauntga o‘tildi')
                        else:
                            update(aid, 'failed', 'Mahalliy brauzer/tarmoq xatosi; login qayta yuborilmadi, keyingi akkauntga o‘tildi')
                        breaker.rejected()
                        if breaker.status()['state'] in ('PAUSED_RATE_LIMIT','PAUSED_REVIEW'):
                            raise StopCycle()
                    finally:
                        if context:
                            try:
                                await context.close()
                            except Exception:
                                pass
                            if context in self.contexts:
                                self.contexts.remove(context)
                    self.current = None
                    await asyncio.sleep(.3)
            except StopCycle:
                pass
            except asyncio.CancelledError:
                raise
            except Exception as error:
                kind = type(error).__name__
                loop = type(asyncio.get_running_loop()).__name__
                detail = f'{kind}/{loop}'
                if isinstance(error, BrowserMissing):
                    detail += ' ' + str(error.args[0])
                if isinstance(error, OSError):
                    detail += f' winerror={getattr(error, "winerror", None)} errno={error.errno} {error.strerror or ""}'.rstrip()
                try:
                    (DATA / 'browser-error.log').write_text(now() + '\n' + traceback.format_exc(), encoding='utf-8')
                except Exception:
                    pass
                message = f'Brauzer ochilmadi [{detail}]. Shu kodni dastur egasiga yuboring.'
                for a in public_accounts():
                    if a['status'] in ('queued', 'running'):
                        update(a['id'], 'failed', message)
            finally:
                for a in public_accounts():
                    if a['status'] == 'queued':
                        update(a['id'], 'skipped', 'Sikl to‘xtadi; akkaunt tekshirilmadi')
                self.current = None
                breaker.rejected()
                await self.close()

    async def session_proof(self, state, url):
        """Real proof of a reusable login: the saved state opens `url` inside the cabinet,
        while a clean browser (no cookies) is sent away to the login page."""
        url = clean_url(url)

        async def probe(storage):
            kwargs = {'storage_state': storage} if storage else {}
            context = await self.browser.new_context(locale='uz-UZ', timezone_id='Asia/Samarkand', **kwargs)
            self.contexts.append(context)
            try:
                page = await context.new_page()
                page.set_default_timeout(15000)
                await page.goto(url, wait_until='domcontentloaded', timeout=30000)
                await settle(page)  # let client-side redirects to the login page happen
                return await in_cabinet(page), page.url
            finally:
                try:
                    await context.close()
                except Exception:
                    pass
                if context in self.contexts:
                    self.contexts.remove(context)

        inside, final = await probe(state)
        if not inside:
            return False, 'saqlangan sessiya kabinetni ochmadi (' + clean_url(final) + ')'
        # Whether a page opens without any cookies does not depend on the account: ask once per page per browser.
        if self._public[0] is not self.browser:
            self._public = (self.browser, {})
        cache = self._public[1]
        if url not in cache:
            cache[url] = (await probe(None))[0]
        open_anyway = cache[url]
        if open_anyway:
            return False, 'sahifa kirishsiz ham ochiladi, isbot bo‘lmadi (' + url + ')'
        return True, ''

    async def reuse_session(self, page, saved, home):
        """Is the saved session still valid? Decided by real proof, not by a logout link that may be hidden."""
        try:
            if home:
                await page.goto(home, wait_until='domcontentloaded', timeout=30000)
                await settle(page)
            if await authenticated(page):
                return True
            return await in_cabinet(page) and (await self.session_proof(saved, page.url))[0]
        except Exception:
            return False

    async def wait_manual(self, page, aid, headed, rate=None):
        if not headed:
            update(aid, 'waiting', 'CAPTCHA/2FA yoki qo‘lda kirish kerak. Brauzer oynasi rejimida qayta ishga tushiring')
            return False
        update(aid, 'waiting', 'Brauzer oynasida CAPTCHA/2FA yoki kirishni bajaring. 120 soniya kutiladi')
        stable, reason = 0, ''
        for _ in range(120):
            check = await page_detection(page, rate)
            if check.outcome == Outcome.RATE_LIMIT:
                halt_cycle(aid, check)
            if await authenticated(page):
                update(aid, 'success', 'Qo‘lda kirishdan so‘ng akkaunt holati tasdiqlandi')
                return True
            if await in_cabinet(page):
                stable += 1
                if stable >= 3:
                    proven, reason = await self.session_proof(await page.context.storage_state(), page.url)
                    if proven:
                        update(aid, 'success', 'Qo‘lda kirishdan so‘ng sessiya haqiqiy tekshiruvdan o‘tdi')
                        return True
                    stable = -30
            else:
                stable = 0
            await asyncio.sleep(1)
        update(aid, 'unverified', 'Qo‘lda tasdiqlash vaqti tugadi. ' + (reason or 'Kabinet sahifasi topilmadi (' + clean_url(page.url) + ')'))
        return False

    def start(self, headed, mode='web', account_id=None, retry=False):
        if self.task and not self.task.done():
            raise HTTPException(409, 'Ish allaqachon davom etmoqda')
        if mode != 'web':
            raise HTTPException(422, 'Faqat sayt rejimi mavjud')
        self.task = asyncio.create_task(self.run(headed, account_id, retry))


runner = Runner()


async def scheduler():
    while True:
        with connect() as c:
            s = dict(c.execute('SELECT * FROM settings WHERE id=1').fetchone())
        dt = datetime.now(TZ)
        licensed = getattr(app.state, 'license_check', lambda: True)()
        if licensed and s['enabled'] and dt.strftime('%H:%M') == s['time'] and s['last_date'] != dt.date().isoformat() and account_count():
            if not runner.task or runner.task.done():
                breaker.clear()
                with connect() as c:
                    c.execute('UPDATE settings SET last_date=? WHERE id=1', (dt.date().isoformat(),))
                runner.start(False, 'web')
        await asyncio.sleep(15)


@asynccontextmanager
async def lifespan(app):
    timer = asyncio.create_task(scheduler())
    yield
    timer.cancel()
    if runner.task and not runner.task.done():
        runner.task.cancel()
        try:
            await runner.task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
    await runner.close()
    runner.task = None


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware('http')
async def local_only(request: Request, call_next):
    if request.headers.get('host') not in (f'localhost:{APP_PORT}', f'127.0.0.1:{APP_PORT}', 'testserver'):
        return JSONResponse({'detail': 'Faqat mahalliy kirish'}, status_code=403)
    if request.method not in ('GET', 'HEAD'):
        origin = request.headers.get('origin')
        if origin not in (f'http://localhost:{APP_PORT}', f'http://127.0.0.1:{APP_PORT}') or request.headers.get('x-manager') != '1':
            return JSONResponse({'detail': 'Mahalliy interfeys orqali foydalaning'}, status_code=403)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return response


class Group(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class Account(BaseModel):
    group_id: str = "default"
    label: str = Field(min_length=1, max_length=60)
    login: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=500)


class Run(BaseModel):
    account_id: str | None = None
    headed: bool = False
    mode: Literal['web'] = 'web'
    retry: bool = False


class Schedule(BaseModel):
    enabled: bool = False
    mode: Literal['web'] = 'web'
    time: str = Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')


@app.exception_handler(RequestValidationError)
async def private_validation(request, exc):
    return JSONResponse({'detail': 'Maydonlarni tekshiring; barcha qiymatlar to‘liq bo‘lishi kerak'}, status_code=422)


def editable():
    if runner.task and not runner.task.done():
        raise HTTPException(409, 'Avval davom etayotgan ishni to‘xtating')


@app.get('/')
async def home():
    return FileResponse(BASE / 'static/index.html')


@app.get('/assets/{name}')
async def asset(name: str):
    if name not in ('app.js', 'style.css', 'activation.js', 'activation.css'):
        raise HTTPException(404)
    return FileResponse(BASE / 'static' / name)


@app.get('/api/state')
async def state():
    with connect() as c:
        logs = [dict(r) for r in c.execute('SELECT * FROM logs ORDER BY id DESC LIMIT 60')]
        schedule = dict(c.execute('SELECT enabled,time,mode FROM settings WHERE id=1').fetchone())
        groups = [dict(row) for row in c.execute('SELECT id,name FROM account_groups ORDER BY rowid')]
    return {'groups': groups, 'accounts': public_accounts(), 'logs': logs, 'running': bool(runner.task and not runner.task.done()), 'schedule': schedule, 'breaker': breaker.status(), 'session_policy': session_evaluation(), 'desktop': bool(getattr(app.state, 'desktop', False))}


@app.post('/api/groups')
async def add_group(group: Group):
    editable()
    name = group.name.strip()
    if not name:
        raise HTTPException(400, 'Bo‘lim nomini kiriting')
    gid = secrets.token_hex(8)
    with connect() as c:
        c.execute('INSERT INTO account_groups(id,name) VALUES(?,?)', (gid, name))
    return {'id': gid}


@app.put('/api/groups/{gid}')
async def rename_group(gid: str, group: Group):
    editable()
    name = group.name.strip()
    if not name:
        raise HTTPException(400, 'Bo‘lim nomini kiriting')
    with connect() as c:
        if not c.execute('UPDATE account_groups SET name=? WHERE id=?', (name, gid)).rowcount:
            raise HTTPException(404, 'Bo‘lim topilmadi')
    return {'ok': True}


@app.post('/api/accounts')
async def add(a: Account):
    editable()
    with connect() as c:
        if not c.execute('SELECT 1 FROM account_groups WHERE id=?', (a.group_id,)).fetchone():
            raise HTTPException(404, 'Bo‘lim topilmadi')
        aid = secrets.token_hex(8)
        c.execute('INSERT INTO accounts(id,label,secret,group_id) VALUES(?,?,?,?)', (aid, a.label, VAULT.encrypt(json.dumps({'login': a.login, 'password': a.password}).encode()), a.group_id))
    return {'id': aid}


@app.post('/api/accounts/{aid}/editor')
async def account_editor(aid: str):
    editable()
    with connect() as c:
        row = c.execute('SELECT label,secret FROM accounts WHERE id=?', (aid,)).fetchone()
    if row is None:
        raise HTTPException(404, 'Akkaunt topilmadi')
    credential = json.loads(VAULT.decrypt(row['secret']))
    return {'label': row['label'], 'login': credential['login'], 'password': credential['password']}


@app.put('/api/accounts/{aid}')
async def edit(aid: str, a: Account):
    editable()
    sessions.delete(aid)
    with connect() as c:
        if not c.execute('SELECT 1 FROM account_groups WHERE id=?', (a.group_id,)).fetchone():
            raise HTTPException(404, 'Bo‘lim topilmadi')
        result = c.execute("UPDATE accounts SET label=?,secret=?,group_id=?,status='idle',detail='',updated=NULL WHERE id=?", (a.label, VAULT.encrypt(json.dumps({'login': a.login, 'password': a.password}).encode()), a.group_id, aid))
        if not result.rowcount:
            raise HTTPException(404)
    return {'ok': True}


@app.delete('/api/accounts/{aid}')
async def delete(aid: str):
    editable()
    sessions.delete(aid)
    with connect() as c:
        c.execute('DELETE FROM accounts WHERE id=?', (aid,))
        c.execute('DELETE FROM logs WHERE account_id=?', (aid,))
    return {'ok': True}


@app.post('/api/run')
async def run(options: Run):
    breaker.clear()
    if not public_accounts():
        raise HTTPException(400, 'Avval akkaunt qo‘shing')
    if options.account_id and not any(a['id'] == options.account_id for a in public_accounts()):
        raise HTTPException(404, 'Akkaunt topilmadi')
    if options.retry and not retry_ids():
        raise HTTPException(400, 'Qayta urinadigan akkaunt yo‘q')
    if options.retry:
        runner.start(options.headed, options.mode, options.account_id, True)
    else:
        runner.start(options.headed, options.mode, options.account_id)
    return {'ok': True}


@app.post('/api/stop')
async def stop():
    if runner.task and not runner.task.done():
        runner.task.cancel()
        try:
            await runner.task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
    await runner.close()
    runner.task = None
    return {'ok': True}


@app.put('/api/schedule')
async def schedule(s: Schedule):
    with connect() as c:
        c.execute('UPDATE settings SET enabled=?,time=?,mode=? WHERE id=1', (s.enabled, s.time, s.mode))
    return {'ok': True}


@app.post('/api/resilience/resume')
async def resume_cycle():
    editable()
    try:
        breaker.resume()
    except ValueError as e:
        raise HTTPException(409, str(e))
    return breaker.status()


@app.get('/api/session-policy')
async def policy():
    return session_evaluation()


app.mount('/simulation', make_app(lab))
