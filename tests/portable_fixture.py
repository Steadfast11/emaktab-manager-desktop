"""Fixtures for the Windows portable-data acceptance test (run by .github/workflows/website-exe.yml).

All accounts here are synthetic. Layout of the data embedded in eMaktab.exe:
[program][zip blob: manager.sqlite3, vault.key, meta.json][footer: EMKDATA1 + int64 length + sha256(blob)]
"""
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import struct
import sys
import zipfile

from cryptography.fernet import Fernet

MAGIC = b'EMKDATA1'
FOOTER = 48

SETS = {
    'A': {
        'groups': [('default', 'Asosiy'), ('gA', 'Sinf 5A')],
        'accounts': [('a1', 'Shared', 'Shared@Mail.uz', 'incoming-pw', 'default'),
                     ('a2', 'A2', 'only-in-A', 'pwA2', 'gA'),
                     ('a3', 'A3', 'only-in-A-2', 'pwA3', 'gA')],
    },
    'B': {
        'groups': [('default', 'Asosiy'), ('gB', 'sinf 5a')],
        'accounts': [('b1', 'Shared live', 'shared@mail.uz', 'live-pw', 'default'),
                     ('b2', 'B2', 'only-in-B', 'pwB2', 'gB')],
    },
}


def make_data(folder, name):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    (folder / 'vault.key').write_bytes(key)
    vault = Fernet(key)
    database = folder / 'manager.sqlite3'
    if database.exists():
        database.unlink()
    connection = sqlite3.connect(database)
    with connection as c:
        c.executescript('''CREATE TABLE accounts(id TEXT PRIMARY KEY, label TEXT NOT NULL, secret BLOB NOT NULL, status TEXT NOT NULL DEFAULT 'idle', detail TEXT NOT NULL DEFAULT '', updated TEXT, group_id TEXT NOT NULL DEFAULT 'default');
        CREATE TABLE logs(id INTEGER PRIMARY KEY, account_id TEXT, time TEXT, message TEXT);
        CREATE TABLE settings(id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER DEFAULT 0, time TEXT DEFAULT '07:00', last_date TEXT DEFAULT '');
        INSERT INTO settings(id) VALUES(1);
        CREATE TABLE account_groups(id TEXT PRIMARY KEY, name TEXT NOT NULL);''')
        for gid, gname in SETS[name]['groups']:
            c.execute('INSERT INTO account_groups(id,name) VALUES(?,?)', (gid, gname))
        for aid, label, login, password, gid in SETS[name]['accounts']:
            secret = vault.encrypt(json.dumps({'login': login, 'password': password}).encode())
            c.execute('INSERT INTO accounts(id,label,secret,group_id) VALUES(?,?,?,?)', (aid, label, secret, gid))
    connection.close()
    return folder


def digest(database):
    connection = sqlite3.connect(database)
    try:
        accounts = connection.execute('SELECT id,label,hex(secret),group_id FROM accounts ORDER BY id').fetchall()
        groups = connection.execute('SELECT id,name FROM account_groups ORDER BY id').fetchall()
    finally:
        connection.close()
    text = ''.join('A|%s|%s|%s|%s\n' % row for row in accounts) + ''.join('G|%s|%s\n' % row for row in groups)
    return hashlib.sha256(text.encode()).hexdigest(), len(accounts)


def attach(exe, folder):
    folder = Path(folder)
    value, count = digest(folder / 'manager.sqlite3')
    memory = io.BytesIO()
    with zipfile.ZipFile(memory, 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(folder / 'manager.sqlite3', 'manager.sqlite3')
        z.write(folder / 'vault.key', 'vault.key')
        z.writestr('meta.json', json.dumps({'savedAt': '2026-01-01T00:00:00.0000000Z', 'digest': 'fixture-' + value, 'accounts': count, 'app': 'fixture'}))
    blob = memory.getvalue()
    with open(exe, 'ab') as f:
        f.write(blob + MAGIC + struct.pack('<q', len(blob)) + hashlib.sha256(blob).digest())


def read_trailer(exe):
    raw = Path(exe).read_bytes()
    footer = raw[-FOOTER:]
    assert footer[:8] == MAGIC, 'No data trailer in exe'
    size = struct.unpack('<q', footer[8:16])[0]
    blob = raw[len(raw) - FOOTER - size:len(raw) - FOOTER]
    assert hashlib.sha256(blob).digest() == footer[16:48], 'Trailer checksum mismatch'
    return zipfile.ZipFile(io.BytesIO(blob))


def extract_trailer(exe, folder):
    zf = read_trailer(exe)
    Path(folder).mkdir(parents=True, exist_ok=True)
    zf.extractall(folder)
    return json.loads(zf.read('meta.json'))


def read_accounts(folder):
    folder = Path(folder)
    vault = Fernet((folder / 'vault.key').read_bytes())
    connection = sqlite3.connect(folder / 'manager.sqlite3')
    try:
        rows = connection.execute('SELECT id,label,secret,group_id FROM accounts').fetchall()
        groups = dict(connection.execute('SELECT id,name FROM account_groups').fetchall())
    finally:
        connection.close()
    accounts = {}
    for aid, label, secret, gid in rows:
        credential = json.loads(vault.decrypt(secret))
        accounts[credential['login'].lower()] = {'password': credential['password'], 'group': groups.get(gid), 'group_id': gid}
    return accounts, groups


def main(argv):
    command = argv[1]
    if command == 'make':          # make <folder> <A|B>
        make_data(argv[2], argv[3])
    elif command == 'attach':      # attach <exe> <folder>
        attach(argv[2], argv[3])
    elif command == 'sync':        # sync <folder>   (marks live data as unchanged since last sync)
        value, _ = digest(Path(argv[2]) / 'manager.sqlite3')
        (Path(argv[2]) / 'portable.sync').write_text(value + '\n2025-06-01T00:00:00.0000000Z')
    elif command == 'has-trailer':  # has-trailer <exe>
        try:
            read_trailer(argv[2])
        except Exception:
            sys.exit(1)
    elif command == 'extract':     # extract <exe> <folder>
        print(json.dumps(extract_trailer(argv[2], argv[3])))
    elif command == 'expect':      # expect <folder> <json file {login: password}> [expected group count]
        accounts, groups = read_accounts(argv[2])
        expected = json.loads(Path(argv[3]).read_text())
        actual = {login: value['password'] for login, value in accounts.items()}
        assert actual == expected, 'Accounts differ: %r != %r' % (actual, expected)
        if len(argv) > 4:
            names = [name.lower() for name in groups.values()]
            assert len(names) == len(set(names)) == int(argv[4]), 'Unexpected groups: %r' % (groups,)
            assert accounts['only-in-a']['group'].lower() == 'sinf 5a'
        print('OK', sorted(actual))
    else:
        raise SystemExit('unknown command')


if __name__ == '__main__':
    main(sys.argv)
