"""Offline Ed25519 verification. This module contains no signing key or issuer."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

PRODUCT_ID = 'emaktab-manager'


def device_code():
    if sys.platform == 'darwin':
        output = subprocess.check_output(['/usr/sbin/ioreg', '-rd1', '-c', 'IOPlatformExpertDevice'], text=True, timeout=8)
        match = re.search(r'"IOPlatformUUID"\s*=\s*"([A-Za-z0-9-]+)"', output)
        identity = match.group(1) if match else ''
    elif sys.platform == 'win32':
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography', 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            identity = winreg.QueryValueEx(key, 'MachineGuid')[0]
    else:
        identity = next((p.read_text().strip() for p in (Path('/etc/machine-id'), Path('/var/lib/dbus/machine-id')) if p.is_file()), '')
    if not isinstance(identity, str) or len(identity.strip()) < 16:
        raise RuntimeError('A stable OS device identifier is unavailable')
    digest = hashlib.sha256((PRODUCT_ID + ':device:v1:' + sys.platform + ':' + identity.strip().lower()).encode()).hexdigest().upper()
    return 'EM1-' + '-'.join(digest[i:i+8] for i in range(0,64,8))


def decode(value):
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def verify_license(key, public_key, device):
    if not isinstance(key, str) or len(key) > 2048:
        raise ValueError('Litsenziya kaliti noto‘g‘ri')
    try:
        prefix, content, signature = key.strip().split('.')
        if prefix != 'EML1':
            raise ValueError()
        raw = decode(content)
        Ed25519PublicKey.from_public_bytes(decode(public_key)).verify(decode(signature), raw)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError()
        if payload.get('v') != 1 or payload.get('product') != PRODUCT_ID:
            raise ValueError()
        if not re.fullmatch(r'[a-f0-9]{32}', payload.get('id', '')):
            raise ValueError()
        if payload.get('device') != device:
            raise ValueError('Bu kalit boshqa qurilma uchun chiqarilgan')
        return payload
    except (InvalidSignature, KeyError, TypeError, UnicodeError, json.JSONDecodeError):
        raise ValueError('Litsenziya imzosi noto‘g‘ri') from None
    except ValueError as error:
        if str(error) == 'Bu kalit boshqa qurilma uchun chiqarilgan':
            raise
        raise ValueError('Litsenziya kaliti noto‘g‘ri') from None


class LicenseGate:
    def __init__(self, data, public_key, device=None):
        self.path = Path(data) / 'license.key'
        self.public_key = public_key
        self.device = device or device_code()
        self.active = None
        self.reload()

    def reload(self):
        try:
            self.active = verify_license(self.path.read_text(), self.public_key, self.device)
        except (OSError, ValueError):
            self.active = None

    def activate(self, key):
        payload = verify_license(key, self.public_key, self.device)
        temp = self.path.with_suffix('.tmp')
        with open(temp, 'w', encoding='utf-8') as file:
            file.write(key.strip())
        os.chmod(temp, 0o600)
        os.replace(temp, self.path)
        self.active = payload
        return payload


def install_license_gate(app, gate, *, self_test=False):
    from fastapi import HTTPException, Request
    from fastapi.responses import FileResponse, JSONResponse
    from pydantic import BaseModel, Field

    class Activation(BaseModel):
        key: str = Field(min_length=1, max_length=2048)

    app.state.license_check = (lambda: False) if self_test else (lambda: gate.active is not None)

    @app.middleware('http')
    async def licensed(request: Request, call_next):
        path = request.scope['path'].rstrip('/') or '/'
        allowed = path.startswith('/api/license/') or path.startswith('/api/desktop/') or path.startswith('/assets/')
        if self_test:
            # Self-test uses a disposable database; it can never authenticate a real account.
            if path in ('/api/run', '/api/schedule', '/api/resilience/resume'):
                return JSONResponse({'detail':'Offline test: real account execution disabled'}, status_code=403)
        elif not gate.active:
            if path == '/' and request.method in ('GET','HEAD'):
                return FileResponse(Path(__file__).resolve().parent / 'static/activate.html', headers={
                    'Cache-Control':'no-store', 'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
            if not allowed:
                return JSONResponse({'detail':'Avval ushbu qurilmada litsenziyani faollashtiring'}, status_code=403)
        return await call_next(request)

    @app.get('/api/license/status')
    async def status():
        return {'active':gate.active is not None, 'device':gate.device, 'license_id':gate.active['id'] if gate.active else None}

    @app.post('/api/license/activate')
    async def activate(body: Activation):
        try:
            gate.activate(body.key)
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        return {'active':True}
