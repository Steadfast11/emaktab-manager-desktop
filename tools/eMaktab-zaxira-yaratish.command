#!/bin/bash
# eMaktab Manager: hozirgi akkauntlardan zaxira yaratadi -> Desktop/eMaktab-zaxira.command
# Hech narsa o'rnatilmaydi: faqat macOS'dagi tayyor perl, sqlite3 va openssl ishlatiladi.
# Zaxira faylni bossangiz, o'chib ketgan akkauntlar qayta kiritiladi (borlariga tegmaydi).
# DIQQAT: zaxira faylda loginlar VA parollar bor. Uni begonalarga bermang.
/usr/bin/perl - <<'GEN_END'
use strict; use warnings;
use MIME::Base64 qw(encode_base64 decode_base64);
use Digest::SHA qw(hmac_sha256);
use JSON::PP;
use File::Temp qw(tempfile);
use POSIX qw(strftime);

my $home   = $ENV{HOME};
my $data   = $ENV{EMAKTAB_DATA_DIR} || "$home/Library/Application Support/eMaktab Manager";
my $outdir = $ENV{EMAKTAB_OUT_DIR}  || "$home/Desktop";
my ($db, $keyfile) = ("$data/manager.sqlite3", "$data/vault.key");
die "Ma'lumotlar topilmadi: $data\n" unless -f $db && -f $keyfile;

sub slurp { my $f = shift; open my $h, '<', $f or die "$f: $!\n"; binmode $h; local $/; my $d = <$h>; close $h; $d }
sub b64u_dec { my $s = shift; $s =~ s/\s+//g; $s =~ tr{-_}{+/}; decode_base64($s) }
sub hx { my $s = pack('H*', shift); utf8::decode($s); $s }

my $raw = b64u_dec(slurp($keyfile));
die "vault.key noto'g'ri\n" unless length($raw) == 32;
my ($sign, $enc) = (substr($raw, 0, 16), substr($raw, 16));

sub aes_decrypt {
    my ($iv, $bytes) = @_;
    my ($fh, $tmp) = tempfile(UNLINK => 1); binmode $fh; print $fh $bytes; close $fh;
    open my $p, '-|', 'openssl', 'enc', '-d', '-aes-128-cbc', '-K', unpack('H*', $enc), '-iv', unpack('H*', $iv), '-in', $tmp
        or die "openssl ishlamadi\n";
    binmode $p; local $/; my $o = <$p>; close $p or die "shifrni ochib bo'lmadi\n"; $o
}
sub fernet_decrypt {
    my $b = b64u_dec(shift);
    die "format xato\n" if length($b) < 57 || ord($b) != 0x80;
    die "kalit mos kelmadi\n" unless hmac_sha256(substr($b, 0, -32), $sign) eq substr($b, -32);
    decode_json(aes_decrypt(substr($b, 9, 16), substr($b, 25, length($b) - 57)))
}

open my $q, '-|', 'sqlite3', '-readonly', $db,
    "SELECT hex(IFNULL(g.name,'Asosiy')), hex(a.label), CAST(a.secret AS TEXT) FROM accounts a LEFT JOIN account_groups g ON g.id=a.group_id ORDER BY a.rowid"
    or die "sqlite3 ishlamadi\n";
my @rows = <$q>; close $q; chomp @rows;

my (@accounts, $bad);
$bad = 0;
for my $r (@rows) {
    my ($g, $l, $tok) = split /\|/, $r, 3;
    my $c = eval { fernet_decrypt($tok) };
    if ($c) { push @accounts, { group => hx($g), label => hx($l), login => $c->{login}, password => $c->{password} } }
    else    { $bad++ }
}
unless (@accounts) { print "Zaxiralash uchun ochiladigan akkaunt yo'q" . ($bad ? " ($bad tasi ochilmadi)" : '') . ".\n"; exit 1 }

my $licence = -f "$data/license.key" ? slurp("$data/license.key") : '';
$licence =~ s/\s+//g;
my %seen; my @groups = grep { !$seen{$_}++ } map { $_->{group} } @accounts;
my $created = strftime('%Y-%m-%d %H:%M', localtime);
my $blob = encode_base64(JSON::PP->new->utf8->canonical->encode({ created => $created, groups => \@groups, accounts => \@accounts, license => $licence }), '');

my $template = <<'TPL';
#!/bin/bash
# eMaktab Manager zaxirasi (@@COUNT@@ ta akkaunt, @@DATE@@). Bosilganda o'chib ketgan akkauntlarni qayta kiritadi.
# Hech narsa o'rnatilmaydi. Bor akkauntlarga tegmaydi. Ichida parollar bor: begonalarga bermang.
/usr/bin/perl - <<'PL'
use strict; use warnings;
use MIME::Base64 qw(encode_base64 decode_base64);
use Digest::SHA qw(hmac_sha256);
use JSON::PP;
use File::Temp qw(tempfile);
use File::Path qw(make_path);
use Fcntl qw(O_WRONLY O_CREAT O_EXCL);
use POSIX qw(strftime);

my $P = JSON::PP->new->utf8->decode(decode_base64('@@BLOB@@'));
my $data = $ENV{EMAKTAB_DATA_DIR} || "$ENV{HOME}/Library/Application Support/eMaktab Manager";
make_path($data, { mode => 0700 }); chmod 0700, $data;
my ($db, $keyfile) = ("$data/manager.sqlite3", "$data/vault.key");

sub slurp { my $f = shift; open my $h, '<', $f or die "$f: $!\n"; binmode $h; local $/; my $d = <$h>; close $h; $d }
sub b64u_dec { my $s = shift; $s =~ s/\s+//g; $s =~ tr{-_}{+/}; decode_base64($s) }
sub b64u_enc { my $s = encode_base64(shift, ''); $s =~ tr{+/}{-_}; $s }
sub rand_bytes { my $n = shift; open my $h, '<', '/dev/urandom' or die; binmode $h; read($h, my $b, $n); close $h; $b }
sub hx { my $s = pack('H*', shift); utf8::decode($s); $s }
sub hxe { my $s = shift; utf8::encode($s); unpack('H*', $s) }

unless (-e $keyfile) {
    rename $db, "$db.eski-" . strftime('%Y%m%d-%H%M%S', localtime) if -e $db;   # kalitsiz eski baza: o'chirmasdan chetga olinadi
    sysopen(my $k, $keyfile, O_WRONLY | O_CREAT | O_EXCL, 0600) or die "vault.key yaratilmadi: $!\n";
    print $k b64u_enc(rand_bytes(32)); close $k;
}
my $raw = b64u_dec(slurp($keyfile));
die "vault.key noto'g'ri\n" unless length($raw) == 32;
my ($sign, $enc) = (substr($raw, 0, 16), substr($raw, 16));

sub aes {
    my ($mode, $iv, $bytes) = @_;
    my ($fh, $tmp) = tempfile(UNLINK => 1); binmode $fh; print $fh $bytes; close $fh;
    open my $p, '-|', 'openssl', 'enc', $mode, '-aes-128-cbc', '-K', unpack('H*', $enc), '-iv', unpack('H*', $iv), '-in', $tmp
        or die "openssl ishlamadi\n";
    binmode $p; local $/; my $o = <$p>; close $p or die "openssl xatosi\n"; $o
}
sub fernet_encrypt {
    my $iv = rand_bytes(16);
    my $body = "\x80" . pack('Q>', time) . $iv . aes('-e', $iv, JSON::PP->new->utf8->canonical->encode(shift));
    b64u_enc($body . hmac_sha256($body, $sign))
}
sub fernet_decrypt {
    my $b = b64u_dec(shift);
    die "format xato\n" if length($b) < 57 || ord($b) != 0x80;
    die "kalit mos kelmadi\n" unless hmac_sha256(substr($b, 0, -32), $sign) eq substr($b, -32);
    JSON::PP->new->utf8->decode(aes('-d', substr($b, 9, 16), substr($b, 25, length($b) - 57)))
}
sub sql_rows {
    my $sql = shift;
    open my $p, '-|', 'sqlite3', $db, $sql or die "sqlite3 ishlamadi\n";
    my @r = <$p>; close $p; chomp @r; map { [split /\|/, $_, -1] } @r
}
sub sql_run {
    my $sql = shift;
    open my $p, '|-', 'sqlite3', $db or die "sqlite3 ishlamadi\n";
    print $p ".timeout 30000\n$sql"; close $p or die "sqlite3 xatosi\n";
}

sql_run(<<'SQL');
CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY, label TEXT NOT NULL, secret BLOB NOT NULL, status TEXT NOT NULL DEFAULT 'idle', detail TEXT NOT NULL DEFAULT '', updated TEXT);
CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY, account_id TEXT, time TEXT, message TEXT);
CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER DEFAULT 0, time TEXT DEFAULT '07:00', last_date TEXT DEFAULT '');
INSERT OR IGNORE INTO settings(id) VALUES(1);
CREATE TABLE IF NOT EXISTS account_groups(id TEXT PRIMARY KEY, name TEXT NOT NULL);
INSERT OR IGNORE INTO account_groups(id,name) VALUES('default','Asosiy');
SQL
sql_run("ALTER TABLE accounts ADD COLUMN group_id TEXT NOT NULL DEFAULT 'default';\n")
    unless grep { $_->[1] eq 'group_id' } sql_rows('PRAGMA table_info(accounts)');
sql_run("ALTER TABLE settings ADD COLUMN mode TEXT NOT NULL DEFAULT 'web';\n")
    unless grep { $_->[1] eq 'mode' } sql_rows('PRAGMA table_info(settings)');
chmod 0600, $db;

my (%have, $unreadable);
$unreadable = 0;
for my $r (sql_rows('SELECT CAST(secret AS TEXT) FROM accounts')) {
    my $c = eval { fernet_decrypt($r->[0]) };
    if ($c) { $have{$c->{login}} = 1 } else { $unreadable++ }
}
my %gid = map { (hx($_->[1]) => $_->[0]) } sql_rows('SELECT id, hex(name) FROM account_groups');

my ($added, $skipped, $sql) = (0, 0, "BEGIN;\n");
for my $a (@{ $P->{accounts} }) {
    if ($have{$a->{login}}) { $skipped++; next }
    unless ($gid{$a->{group}}) {
        $gid{$a->{group}} = unpack('H*', rand_bytes(8));
        $sql .= "INSERT INTO account_groups(id,name) VALUES('$gid{$a->{group}}',CAST(x'" . hxe($a->{group}) . "' AS TEXT));\n";
    }
    my $tok = fernet_encrypt({ login => $a->{login}, password => $a->{password} });
    $sql .= "INSERT INTO accounts(id,label,secret,group_id) VALUES('" . unpack('H*', rand_bytes(8)) . "',CAST(x'" . hxe($a->{label})
          . "' AS TEXT),CAST(x'" . unpack('H*', $tok) . "' AS BLOB),'$gid{$a->{group}}');\n";
    $have{$a->{login}} = 1; $added++;
}
sql_run($sql . "COMMIT;\n");

my $licence_file = "$data/license.key";
my $licence_restored = 0;
if ($P->{license} && !-e $licence_file) {
    sysopen(my $l, $licence_file, O_WRONLY | O_CREAT | O_EXCL, 0600) or die "license.key yozilmadi: $!\n";
    print $l $P->{license}; close $l; $licence_restored = 1;
}

print "Zaxira: " . scalar(@{ $P->{accounts} }) . " ta akkaunt ($P->{created}).\n";
print "Qayta kiritildi: $added ta. Allaqachon bor edi: $skipped ta.\n";
print "Litsenziya qayta tiklandi (u shu kompyuterga bog'langan; boshqa kompyuterda ishlamasligi mumkin).\n" if $licence_restored;
print "Diqqat: bazada ochib bo'lmaydigan $unreadable ta yozuv bor (boshqa kalit bilan saqlangan).\n" if $unreadable;
print "Ilova ochiq bo'lsa, sahifani yangilang (yoki ilovani qayta oching).\n";
PL
read -r -p "Enter bosing..." _
TPL

my $n = scalar @accounts;
$template =~ s/\@\@COUNT\@\@/$n/g;
$template =~ s/\@\@DATE\@\@/$created/g;
$template =~ s/\@\@BLOB\@\@/$blob/g;
my $target = "$outdir/eMaktab-zaxira.command";
open my $o, '>', $target or die "$target: $!\n"; binmode $o; print $o $template; close $o;
chmod 0700, $target;
print "Zaxira tayyor: $target\n";
print "Akkauntlar: $n ta, guruhlar: " . scalar(@groups) . " ta" . ($bad ? ", ochilmaganlari (zaxiraga kirmadi): $bad" : '') . "\n";
print "Ichida parollar va litsenziya bor: faylni begona odamga bermang.\n";
GEN_END
