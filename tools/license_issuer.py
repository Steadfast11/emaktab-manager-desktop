"""OWNER ONLY. Never bundle this tool or its private key with customer packages."""
import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def encode(value):
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


def main():
    parser = argparse.ArgumentParser(description='eMaktab Manager — owner license issuer')
    parser.add_argument('--authority', type=Path, required=True, help='Private directory outside source/release/repository')
    parser.add_argument('--init-public', type=Path, help='Create authority once and write public verifier module')
    parser.add_argument('--device', help='Device code supplied by the customer')
    parser.add_argument('--output', type=Path, help='Write the license to this private file')
    args = parser.parse_args()
    os.umask(0o077)
    directory = args.authority.expanduser().resolve(); directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    private = directory / 'issuer-private.pem'
    if args.init_public:
        if not private.exists():
            key = Ed25519PrivateKey.generate()
            fd = os.open(private, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd,'wb') as file:
                file.write(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        key = serialization.load_pem_private_key(private.read_bytes(), password=None)
        public = encode(key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw))
        args.init_public.write_text('# Public verification key only. Private authority is never distributed.\nPUBLIC_KEY = '+repr(public)+'\n')
        print('Public verifier written. Private signing key stayed in the authority directory.')
    if args.device:
        device = args.device.strip().upper()
        if not re.fullmatch(r'EM1-(?:[A-F0-9]{8}-){7}[A-F0-9]{8}',device):
            raise SystemExit('Qurilma kodi noto‘g‘ri')
        if not args.output:
            raise SystemExit('--output is required; licenses are not printed into logs')
        key = serialization.load_pem_private_key(private.read_bytes(), password=None)
        payload = json.dumps({'v':1,'product':'emaktab-manager','device':device,'id':secrets.token_hex(16),
                              'issued':datetime.now(timezone.utc).isoformat(timespec='seconds')},sort_keys=True,separators=(',',':')).encode()
        license_key = 'EML1.'+encode(payload)+'.'+encode(key.sign(payload))
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(license_key+'\n');args.output.chmod(0o600)
        print('License saved to the requested private file. Bound to one device.')
    if not args.init_public and not args.device:
        parser.error('Choose --init-public or --device')


if __name__=='__main__':
    main()
