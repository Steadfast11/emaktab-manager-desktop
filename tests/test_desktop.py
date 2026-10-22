"""Launcher lifecycle acceptance tests use temporary stores, never live accounts."""
import json
import hashlib
import os
from pathlib import Path
import socket
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from desktop import InstanceLock, bind_socket, read_instance, user_data_dir
import desktop
from resilience import ClosingConnection


def test_platform_data_paths_do_not_follow_download_folder(tmp_path):
    assert user_data_dir('darwin', {}, tmp_path) == tmp_path / 'Library/Application Support/eMaktab Manager'
    assert user_data_dir('win32', {'LOCALAPPDATA': str(tmp_path)}, tmp_path) == tmp_path / 'eMaktabManager'
    assert user_data_dir('linux', {'XDG_DATA_HOME': str(tmp_path)}, tmp_path) == tmp_path / 'emaktab-manager'


def test_busy_port_is_preserved_and_lock_is_released(tmp_path):
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0)); occupied.listen()
        port = occupied.getsockname()[1]
        other = bind_socket(port)
        assert other.getsockname()[1] != port
        assert occupied.getsockname()[1] == port
        other.close()
    first, second = InstanceLock(tmp_path / 'lock'), InstanceLock(tmp_path / 'lock')
    try:
        assert first.acquire()
        assert not second.acquire()
        first.close()
        assert second.acquire()
    finally:
        first.close(); second.close()


def test_browser_payload_rejects_corruption_and_path_traversal(tmp_path, monkeypatch):
    resources = tmp_path / 'resources'; resources.mkdir()
    data = tmp_path / 'private'; data.mkdir()
    monkeypatch.setattr(desktop, '__file__', str(resources / 'desktop.py'))
    monkeypatch.delenv('PLAYWRIGHT_BROWSERS_PATH', raising=False)
    payload = resources / 'browser-payload.zip'
    with zipfile.ZipFile(payload, 'w') as archive:
        archive.writestr('../escape', 'not executable')
    manifest = {'sha256':hashlib.sha256(payload.read_bytes()).hexdigest(), 'executable':'chrome'}
    (resources / 'browser-payload.json').write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError, match='Unsafe browser archive path'):
        desktop.configure_browser(data)
    assert not list(tmp_path.rglob('escape'))
    payload.write_bytes(b'corrupted')
    with pytest.raises(RuntimeError, match='checksum mismatch'):
        desktop.configure_browser(data)


def wait_ready(runtime, process):
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError('Launcher exited before readiness')
        info = read_instance(runtime)
        if info:
            return info
        time.sleep(.1)
    raise AssertionError('Launcher startup timed out')


def local_request(base, path, body=None, headers=None):
    request = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(), headers=headers or {})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def test_repeat_launch_shutdown_crash_recovery_and_credentials_survive(tmp_path):
    env = dict(os.environ, EMAKTAB_DATA_DIR=str(tmp_path))
    command = ([os.environ['EMAKTAB_TEST_EXECUTABLE']] if os.environ.get('EMAKTAB_TEST_EXECUTABLE') else [sys.executable, str(ROOT / 'desktop.py')]) + ['--no-browser']
    process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        info = wait_ready(tmp_path / 'desktop.json', process)
        base = f"http://127.0.0.1:{info['port']}"
        headers = {'Origin':base, 'X-Manager':'1', 'Content-Type':'application/json'}
        assert local_request(base, '/api/license/status')['active'] is False
        with pytest.raises(urllib.error.HTTPError) as unlicensed:
            local_request(base, '/api/state')
        assert unlicensed.value.code == 403
        with pytest.raises(urllib.error.HTTPError) as missing_token:
            local_request(base, '/api/desktop/health')
        assert missing_token.value.code == 403
        # Existing encrypted data must survive crashes even while activation blocks its API.
        from cryptography.fernet import Fernet
        token = Fernet((tmp_path / 'vault.key').read_bytes()).encrypt(json.dumps({'login':'offline-test','password':'synthetic-only'}).encode())
        with sqlite3.connect(tmp_path / 'manager.sqlite3', factory=ClosingConnection) as database:
            database.execute('INSERT INTO accounts(id,label,secret) VALUES(?,?,?)', ('synthetic','Synthetic',token))
        key = (tmp_path / 'vault.key').read_bytes()
        second = subprocess.run(command, env=env, timeout=8)
        assert second.returncode == 0 and process.poll() is None
        assert read_instance(tmp_path / 'desktop.json')['instance'] == info['instance']
        with pytest.raises(urllib.error.HTTPError) as cross_origin:
            local_request(base, '/api/desktop/quit', {}, {**headers, 'Origin':'https://untrusted.example'})
        assert cross_origin.value.code == 403
        # Simulate a forced termination: stale desktop.json must not block restart.
        os.kill(info['pid'], signal.SIGTERM if os.name == 'nt' else signal.SIGKILL)
        process.wait(timeout=12)
        process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        recovered = wait_ready(tmp_path / 'desktop.json', process)
        base = f"http://127.0.0.1:{recovered['port']}"
        assert local_request(base, '/api/license/status')['active'] is False
        with sqlite3.connect(tmp_path / 'manager.sqlite3', factory=ClosingConnection) as database:
            assert database.execute('SELECT secret FROM accounts WHERE id=?',('synthetic',)).fetchone()[0] == token
        assert (tmp_path / 'vault.key').read_bytes() == key
        local_request(base, '/api/desktop/quit', {}, {'Origin':base,'X-Manager':'1','Content-Type':'application/json'})
        assert process.wait(timeout=12) == 0
        assert not (tmp_path / 'desktop.json').exists()
    finally:
        if process.poll() is None:
            process.kill(); process.wait(timeout=5)
