"""Native entry point. Never imports the application before choosing its data directory."""
import argparse
import contextlib
import hmac
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile

VERSION = '1.0.0'
PRODUCT = 'eMaktab Manager'
LOCAL_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def user_data_dir(system=None, environ=None, home=None):
    system, env, home = system or sys.platform, os.environ if environ is None else environ, Path(home or Path.home())
    if env.get('EMAKTAB_DATA_DIR'):
        return Path(env['EMAKTAB_DATA_DIR']).expanduser().resolve()
    if system == 'win32':
        return Path(env.get('LOCALAPPDATA', home / 'AppData/Local')) / 'eMaktabManager'
    if system == 'darwin':
        return home / 'Library/Application Support/eMaktab Manager'
    return Path(env.get('XDG_DATA_HOME', home / '.local/share')) / 'emaktab-manager'


class InstanceLock:
    """OS releases the lock even after a crash; never delete the lock inode."""
    def __init__(self, path):
        self.file = open(path, 'a+b')

    def acquire(self):
        try:
            if os.name == 'nt':
                import msvcrt
                self.file.seek(0)
                if not self.file.read(1):
                    self.file.write(b'\0'); self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (BlockingIOError, OSError):
            return False

    def close(self):
        self.file.close()


def bind_socket(preferred=8766):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(('127.0.0.1', preferred))
    except OSError:
        sock.bind(('127.0.0.1', 0))
    sock.listen(128)
    return sock


def find_chrome():
    if sys.platform == 'darwin':
        candidates = [Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),
                      Path.home() / 'Applications/Google Chrome.app/Contents/MacOS/Google Chrome']
    elif sys.platform == 'win32':
        candidates = [Path(os.environ.get(key, '')) / 'Google/Chrome/Application/chrome.exe'
                      for key in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA') if os.environ.get(key)]
    else:
        candidates = [Path(p) for name in ('google-chrome', 'google-chrome-stable') if (p := shutil.which(name))]
    return next((str(p) for p in candidates if p.is_file()), None)


def bundled_browser_root():
    return Path(__file__).resolve().parent / 'browsers'


def configure_browser(data, install=True):
    resources = Path(__file__).resolve().parent
    manifest = resources / 'browser-payload.json'
    if not manifest.exists():
        return  # Source development uses the normal Playwright cache.
    info = json.loads(manifest.read_text())
    cache = data / 'browser-cache' / info['sha256'][:20]
    executable = cache / info['executable']
    if not executable.is_file() and install:
        payload = resources / 'browser-payload.zip'
        with payload.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        if digest != info['sha256']:
            raise RuntimeError('Browser payload checksum mismatch')
        cache.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='extract-', dir=cache.parent) as temp:
            target = Path(temp)
            with zipfile.ZipFile(payload) as archive:
                for member in archive.infolist():
                    destination = target / member.filename
                    if not destination.resolve().is_relative_to(target.resolve()):
                        raise RuntimeError('Unsafe browser archive path')
                    mode = member.external_attr >> 16
                    if stat.S_ISLNK(mode):
                        link = archive.read(member).decode('utf-8')
                        if not (destination.parent / link).resolve().is_relative_to(target.resolve()):
                            raise RuntimeError('Unsafe browser archive symlink')
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.symlink_to(link)
                    elif member.is_dir():
                        destination.mkdir(parents=True, exist_ok=True)
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(member) as source, open(destination, 'wb') as output:
                            shutil.copyfileobj(source, output)
                        if mode:
                            destination.chmod(stat.S_IMODE(mode))
            if not (target / info['executable']).is_file():
                raise RuntimeError('Browser payload incomplete')
            if cache.exists():
                shutil.rmtree(cache)
            os.replace(target, cache)
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(cache)


def open_window(url, data):
    executable = find_chrome()
    if not executable:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            executable = pw.chromium.executable_path
        if not Path(executable).is_file():
            raise RuntimeError('Chrome topilmadi. Google Chrome o‘rnating yoki to‘liq desktop paketdan foydalaning.')
    command = [executable, '--app=' + url, '--new-window', '--no-first-run',
               '--no-default-browser-check', '--user-data-dir=' + str(data / 'ui-profile')]
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if sys.platform == 'darwin' and 'Google Chrome.app' in executable:
        def activate():
            time.sleep(1)
            subprocess.run(['osascript', '-e', 'tell application id "com.google.Chrome" to activate'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
        threading.Thread(target=activate, daemon=True).start()


def read_instance(path):
    try:
        value = json.loads(path.read_text())
        port = int(value['port'])
        if not 1 <= port <= 65535 or not isinstance(value['token'], str):
            return None
        req = urllib.request.Request(f'http://127.0.0.1:{port}/api/desktop/health',
                                     headers={'X-Desktop-Token': value['token']})
        with LOCAL_HTTP.open(req, timeout=.7) as response:
            body = json.load(response)
        return value if body.get('product') == PRODUCT and body.get('instance') == value.get('instance') else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_instance(path, info):
    temp = path.with_suffix('.tmp')
    with open(temp, 'w', encoding='utf-8') as f:
        json.dump(info, f)
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def install_desktop_routes(app, server, token, instance):
    from fastapi import HTTPException, Request

    @app.get('/api/desktop/health')
    async def health(request: Request):
        if not hmac.compare_digest(request.headers.get('x-desktop-token', ''), token):
            raise HTTPException(403, 'Private launcher endpoint')
        return {'product': PRODUCT, 'version': VERSION, 'instance': instance}

    @app.post('/api/desktop/quit')
    async def quit_desktop():
        # Application middleware requires the exact local Origin and X-Manager.
        server.should_exit = True
        return {'ok': True}


def smoke_test(base, data):
    """Packaged offline acceptance test, using only an isolated synthetic account."""
    from playwright.sync_api import sync_playwright
    from cryptography.fernet import Fernet
    import sqlite3
    headers = {'Origin': base, 'X-Manager': '1', 'Content-Type': 'application/json'}
    def request(path, body=None):
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(), headers=headers)
        with LOCAL_HTTP.open(req, timeout=10) as r:
            return json.load(r)
    assert not request('/api/state')['accounts'], 'Self-test must never use an existing database'
    group = request('/api/groups', {'name': 'Offline acceptance'})['id']
    password = secrets.token_urlsafe(24)
    aid = request('/api/accounts', {'group_id': group, 'label': 'Synthetic test', 'login': 'offline-synthetic', 'password': password})['id']
    state = request('/api/state')
    assert len(state['accounts']) == 1 and password not in json.dumps(state)
    with sqlite3.connect(data / 'manager.sqlite3') as c:
        secret = c.execute('SELECT secret FROM accounts WHERE id=?', (aid,)).fetchone()[0]
    assert password.encode() not in secret
    assert json.loads(Fernet((data / 'vault.key').read_bytes()).decrypt(secret))['password'] == password
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chromium')
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
        page.goto(base)
        page.get_by_role('heading', name='offline-synthetic', exact=True).wait_for()
        page.get_by_role('button', name='Tahrirlash', exact=True).click()
        page.locator('#editor').wait_for(state='visible')
        assert page.locator('#password').input_value() == password
        page.locator('#toggle-password').click()
        assert page.locator('#password').get_attribute('type') == 'text'
        page.locator('#cancel').click()
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile layout overflows'
        assert not errors
        browser.close()
    return {'ok': True, 'packaged_browser': True, 'encrypted_storage': True, 'ui': True, 'real_logins': 0}


def serve(data, *, no_browser=False, self_test=False):
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(data, 0o700)
    lock = InstanceLock(data / 'desktop.lock')
    runtime = data / 'desktop.json'
    if not lock.acquire():
        try:
            for _ in range(120):
                existing = read_instance(runtime)
                if existing:
                    configure_browser(data, install=False)
                    if not no_browser:
                        open_window(f"http://127.0.0.1:{existing['port']}/", data)
                    return 0
                time.sleep(.25)
            raise RuntimeError('Ilova ishga tushmoqda yoki javob bermayapti. Biroz kutib qayta oching.')
        finally:
            lock.close()
    sock = None
    try:
        configure_browser(data)
        sock = bind_socket()
        port = sock.getsockname()[1]
        os.environ['EMAKTAB_PORT'] = str(port)
        os.environ['EMAKTAB_DATA_DIR'] = str(data)
        import app as manager
        from licensing import LicenseGate, install_license_gate
        from license_config import PUBLIC_KEY
        import uvicorn
        manager.app.state.desktop = True
        server = uvicorn.Server(uvicorn.Config(manager.app, host='127.0.0.1', port=port, access_log=False,
                                                log_config=None, ws='none', loop='asyncio', http='h11'))
        token, instance = secrets.token_urlsafe(32), secrets.token_hex(16)
        install_desktop_routes(manager.app, server, token, instance)
        install_license_gate(manager.app, LicenseGate(data, PUBLIC_KEY), self_test=self_test)
        result = {'code': 0}
        def ready():
            deadline = time.monotonic() + 60
            info = {'port': port, 'token': token, 'instance': instance, 'pid': os.getpid()}
            while not server.started:
                if time.monotonic() > deadline or server.should_exit:
                    result['code'] = 1
                    server.should_exit = True
                    return
                time.sleep(.05)
            try:
                write_instance(runtime, info)
                base = f'http://127.0.0.1:{port}'
                if self_test:
                    print(json.dumps(smoke_test(base, data)), flush=True)
                elif not no_browser:
                    open_window(base + '/', data)
            except Exception as error:
                result['code'] = 1
                if self_test:
                    import traceback
                    print(json.dumps({'ok': False, 'error_type': type(error).__name__, 'line': traceback.extract_tb(error.__traceback__)[-1].lineno}), flush=True)
                else:
                    show_error('Brauzer ochilmadi. Chrome o‘rnatilganini tekshiring. Ma’lumotlaringiz saqlangan.')
            finally:
                if self_test or result['code']:
                    server.should_exit = True
        threading.Thread(target=ready, daemon=True).start()
        server.run(sockets=[sock])
        return result['code'] if server.started else 1
    finally:
        with contextlib.suppress(OSError):
            runtime.unlink()
        if sock:
            sock.close()
        lock.close()


def show_error(message):
    if sys.platform == 'darwin':
        subprocess.run(['osascript', '-e', 'on run argv\ndisplay alert "eMaktab Manager" message (item 1 of argv)\nend run', message], check=False)
    elif sys.platform == 'win32':
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, PRODUCT, 0x10)
    elif shutil.which('zenity'):
        subprocess.run(['zenity', '--error', '--title=' + PRODUCT, '--text=' + message], check=False)
    else:
        print(message, file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=PRODUCT)
    parser.add_argument('--no-browser', action='store_true', help='Run without opening a window')
    parser.add_argument('--self-test', action='store_true', help='Offline acceptance test in a disposable database')
    parser.add_argument('--version', action='version', version=VERSION)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.self_test:
            with tempfile.TemporaryDirectory(prefix='emaktab-acceptance-') as directory:
                return serve(Path(directory), no_browser=True, self_test=True)
        return serve(user_data_dir(), no_browser=args.no_browser)
    except Exception as error:
        # Never send arbitrary exception content (which may contain private paths/values) to the UI.
        if args.self_test:
            print(json.dumps({'ok': False, 'error_type': type(error).__name__}), flush=True)
        else:
            show_error('Ilova ishga tushmadi. Diskda joy va ma’lumotlar papkasiga yozish huquqini tekshiring. Akkauntlar o‘chirilmagan.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
