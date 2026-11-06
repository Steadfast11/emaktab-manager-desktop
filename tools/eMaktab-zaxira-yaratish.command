#!/bin/bash
# eMaktab Manager: hozirgi akkauntlardan zaxira yaratadi -> Desktop/eMaktab-zaxira.command
# Zaxira faylni bossangiz, o'chib ketgan akkauntlar qayta kiritiladi (borlariga tegmaydi).
# DIQQAT: zaxira faylda loginlar VA parollar bor. Uni begonalarga bermang.
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 topilmadi. Terminalda 'xcode-select --install' ni ishga tushiring va qayta urinib ko'ring."
  exit 1
fi
python3 -I - <<'GEN_END'
import base64, hashlib, hmac, json, os, sqlite3, subprocess, sys, time
from pathlib import Path

data = Path(os.environ.get('EMAKTAB_DATA_DIR') or Path.home() / 'Library/Application Support/eMaktab Manager')
out_dir = Path(os.environ.get('EMAKTAB_OUT_DIR') or Path.home() / 'Desktop')
db, keyfile = data / 'manager.sqlite3', data / 'vault.key'
if not db.is_file() or not keyfile.is_file():
    print(f"Ma'lumotlar topilmadi: {data}")
    sys.exit(1)

raw = base64.urlsafe_b64decode(keyfile.read_bytes().strip())
sign_key, enc_key = raw[:16], raw[16:]

def decrypt(token):
    blob = base64.urlsafe_b64decode(token)
    if blob[0] != 0x80 or not hmac.compare_digest(hmac.new(sign_key, blob[:-32], hashlib.sha256).digest(), blob[-32:]):
        raise ValueError('kalit mos kelmadi')
    r = subprocess.run(['openssl', 'enc', '-aes-128-cbc', '-d', '-K', enc_key.hex(), '-iv', blob[9:25].hex()],
                       input=blob[25:-32], capture_output=True)
    if r.returncode:
        raise ValueError("shifrni ochib bo'lmadi")
    return json.loads(r.stdout)

con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
groups = dict(con.execute('SELECT id,name FROM account_groups'))
accounts, bad = [], 0
for label, secret, gid in con.execute('SELECT label,secret,group_id FROM accounts ORDER BY rowid'):
    try:
        cred = decrypt(bytes(secret))
        accounts.append({'group': groups.get(gid, 'Asosiy'), 'label': label, 'login': cred['login'], 'password': cred['password']})
    except Exception:
        bad += 1
if not accounts:
    print("Zaxiralash uchun ochiladigan akkaunt yo'q" + (f' ({bad} tasi ochilmadi)' if bad else '') + '.')
    sys.exit(1)

licence = (data / 'license.key').read_text().strip() if (data / 'license.key').is_file() else ''
payload = {'created': time.strftime('%Y-%m-%d %H:%M'), 'groups': list(dict.fromkeys(a['group'] for a in accounts)), 'accounts': accounts, 'license': licence}
blob = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode()).decode()

TEMPLATE = r"""#!/bin/bash
# eMaktab Manager zaxirasi (@@COUNT@@ ta akkaunt, @@DATE@@). Bosilganda o'chib ketgan akkauntlarni qayta kiritadi.
# Bor akkauntlarga tegmaydi. Ichida parollar bor: begonalarga bermang.
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 topilmadi. Terminalda 'xcode-select --install' ni ishga tushiring va qayta urinib ko'ring."
  read -r -p "Enter bosing..." _; exit 1
fi
python3 -I - <<'PY'
import base64, hashlib, hmac, json, os, secrets, sqlite3, subprocess, sys, time
from pathlib import Path

PAYLOAD = json.loads(base64.b64decode('@@BLOB@@').decode())
data = Path(os.environ.get('EMAKTAB_DATA_DIR') or Path.home() / 'Library/Application Support/eMaktab Manager')
data.mkdir(parents=True, exist_ok=True)
os.chmod(data, 0o700)
db, keyfile = data / 'manager.sqlite3', data / 'vault.key'

if not keyfile.exists():
    if db.exists():  # kalitsiz eski baza ochilmaydi: o'chirmasdan chetga olamiz
        db.rename(data / ('manager.sqlite3.eski-' + time.strftime('%Y%m%d-%H%M%S')))
    fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(base64.urlsafe_b64encode(os.urandom(32)))
raw = base64.urlsafe_b64decode(keyfile.read_bytes().strip())
sign_key, enc_key = raw[:16], raw[16:]

def openssl(args, payload):
    r = subprocess.run(['openssl', 'enc', '-aes-128-cbc'] + args, input=payload, capture_output=True)
    if r.returncode:
        raise ValueError('openssl xatosi')
    return r.stdout

def encrypt(obj):
    iv = os.urandom(16)
    body = b'\x80' + int(time.time()).to_bytes(8, 'big') + iv + openssl(['-e', '-K', enc_key.hex(), '-iv', iv.hex()], json.dumps(obj).encode())
    return base64.urlsafe_b64encode(body + hmac.new(sign_key, body, hashlib.sha256).digest())

def decrypt(token):
    blob = base64.urlsafe_b64decode(token)
    if blob[0] != 0x80 or not hmac.compare_digest(hmac.new(sign_key, blob[:-32], hashlib.sha256).digest(), blob[-32:]):
        raise ValueError('kalit mos kelmadi')
    return json.loads(openssl(['-d', '-K', enc_key.hex(), '-iv', blob[9:25].hex()], blob[25:-32]))

con = sqlite3.connect(db, timeout=30)
con.executescript('''CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY, label TEXT NOT NULL, secret BLOB NOT NULL, status TEXT NOT NULL DEFAULT 'idle', detail TEXT NOT NULL DEFAULT '', updated TEXT);
    CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY, account_id TEXT, time TEXT, message TEXT);
    CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER DEFAULT 0, time TEXT DEFAULT '07:00', last_date TEXT DEFAULT '');
    INSERT OR IGNORE INTO settings(id) VALUES(1);
    CREATE TABLE IF NOT EXISTS account_groups(id TEXT PRIMARY KEY, name TEXT NOT NULL);
    INSERT OR IGNORE INTO account_groups(id,name) VALUES('default','Asosiy');''')
if 'group_id' not in [r[1] for r in con.execute('PRAGMA table_info(accounts)')]:
    con.execute("ALTER TABLE accounts ADD COLUMN group_id TEXT NOT NULL DEFAULT 'default'")
if 'mode' not in [r[1] for r in con.execute('PRAGMA table_info(settings)')]:
    con.execute("ALTER TABLE settings ADD COLUMN mode TEXT NOT NULL DEFAULT 'web'")
os.chmod(db, 0o600)

have, unreadable = set(), 0
for (secret,) in con.execute('SELECT secret FROM accounts'):
    try:
        have.add(decrypt(bytes(secret))['login'])
    except Exception:
        unreadable += 1
group_ids = {name: gid for gid, name in con.execute('SELECT id,name FROM account_groups')}

added = skipped = 0
for a in PAYLOAD['accounts']:
    if a['login'] in have:
        skipped += 1
        continue
    if a['group'] not in group_ids:
        group_ids[a['group']] = secrets.token_hex(8)
        con.execute('INSERT INTO account_groups(id,name) VALUES(?,?)', (group_ids[a['group']], a['group']))
    con.execute('INSERT INTO accounts(id,label,secret,group_id) VALUES(?,?,?,?)',
                (secrets.token_hex(8), a['label'], encrypt({'login': a['login'], 'password': a['password']}), group_ids[a['group']]))
    have.add(a['login'])
    added += 1
con.commit()
con.close()

licence_file = data / 'license.key'
licence_restored = False
if PAYLOAD.get('license') and not licence_file.exists():
    fd = os.open(licence_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(PAYLOAD['license'])
    licence_restored = True

print(f"Zaxira: {len(PAYLOAD['accounts'])} ta akkaunt ({PAYLOAD['created']}).")
print(f"Qayta kiritildi: {added} ta. Allaqachon bor edi: {skipped} ta.")
if licence_restored:
    print("Litsenziya qayta tiklandi (u shu kompyuterga bog'langan; boshqa kompyuterda ishlamasligi mumkin).")
if unreadable:
    print(f"Diqqat: bazada ochib bo'lmaydigan {unreadable} ta yozuv bor (boshqa kalit bilan saqlangan).")
print("Ilova ochiq bo'lsa, sahifani yangilang (yoki ilovani qayta oching).")
PY
read -r -p "Enter bosing..." _
"""

target = out_dir / 'eMaktab-zaxira.command'
text = TEMPLATE.replace('@@COUNT@@', str(len(accounts))).replace('@@DATE@@', payload['created']).replace('@@BLOB@@', blob)
target.write_text(text, encoding='utf-8')
target.chmod(0o700)
print(f'Zaxira tayyor: {target}')
print(f'Akkauntlar: {len(accounts)} ta, guruhlar: {len(payload["groups"])} ta' + (f', ochilmaganlari (zaxiraga kirmadi): {bad}' if bad else ''))
print("Ichida parollar va litsenziya bor: faylni begona odamga bermang.")
GEN_END
