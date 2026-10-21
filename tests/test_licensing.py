import base64
import json
from pathlib import Path
import secrets

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from licensing import LicenseGate, install_license_gate, verify_license


def encoded(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def issue(private, device):
    raw=json.dumps({'v':1,'product':'emaktab-manager','device':device,'id':secrets.token_hex(16)}).encode()
    return 'EML1.'+encoded(raw)+'.'+encoded(private.sign(raw))


def setup(data, device='device-A'):
    private=Ed25519PrivateKey.generate()
    public=encoded(private.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw))
    return private,public,LicenseGate(data,public,device)


def test_forgery_tampering_wrong_device_and_copied_license_fail(tmp_path):
    private,public,gate=setup(tmp_path)
    key=issue(private,'device-A')
    gate.activate(key)
    assert gate.active and LicenseGate(tmp_path,public,'device-A').active
    assert LicenseGate(tmp_path,public,'device-B').active is None
    with pytest.raises(ValueError,match='boshqa qurilma'):
        verify_license(key,public,'device-B')
    with pytest.raises(ValueError):
        verify_license(issue(Ed25519PrivateKey.generate(),'device-A'),public,'device-A')
    prefix,payload,signature=key.split('.')
    mutated=encoded(json.dumps({'v':1,'product':'emaktab-manager','device':'device-B','id':secrets.token_hex(16)}).encode())
    with pytest.raises(ValueError):
        verify_license('.'.join((prefix,mutated,signature)),public,'device-B')
    for bad in ('','broken','EML1.a.a','x'*3000):
        with pytest.raises(ValueError):
            gate.activate(bad)
    assert (tmp_path/'license.key').read_text()==key


def test_no_feature_access_before_activation_and_valid_key_unlocks(tmp_path):
    private,public,gate=setup(tmp_path)
    app=FastAPI();install_license_gate(app,gate)
    @app.get('/api/state')
    async def state():return {'private':'protected'}
    @app.post('/api/run')
    async def run():return {'running':True}
    with TestClient(app) as client:
        assert client.get('/api/state').status_code==403
        assert client.post('/api/run').status_code==403
        assert client.get('/').status_code==200
        assert client.get('/api/license/status').json()['active'] is False
        assert client.post('/api/license/activate',json={'key':issue(private,'device-B')}).status_code==400
        assert client.get('/api/state').status_code==403
        assert client.post('/api/license/activate',json={'key':issue(private,'device-A')}).status_code==200
        assert client.get('/api/state').json()=={'private':'protected'}


def test_self_test_cannot_be_used_to_run_real_accounts_or_enable_schedule(tmp_path):
    _,_,gate=setup(tmp_path)
    app=FastAPI();install_license_gate(app,gate,self_test=True)
    with TestClient(app) as client:
        assert client.post('/api/run').status_code==403
        assert client.put('/api/schedule').status_code==403
        assert client.post('/api/resilience/resume').status_code==403
        assert not app.state.license_check()
