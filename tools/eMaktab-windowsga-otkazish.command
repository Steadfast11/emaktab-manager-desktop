#!/bin/bash
# eMaktab Manager (Mac): hozirgi barcha akkauntlarni Windows kompyuterga o'tkazish uchun bitta fayl yaratadi.
#   -> Desktop/eMaktab-Windows-akkauntlar.cmd
# Shu faylni Windows'ga olib borib ikki marta bossangiz:
#   1) ilova (eMaktab.exe) yo'q bo'lsa, GitHub'dagi eng yangi relizdan yuklab o'rnatadi (SHA256 tekshiriladi);
#   2) akkauntlarni ilovaning o'z mexanizmi (eMaktab-akkauntlar.emk) orqali tiklaydi: bor akkauntlarga tegmaydi;
#   3) ilovani ochadi.
# Hech narsa o'rnatilmaydi: faqat macOS'dagi tayyor sqlite3, zip, perl va shasum ishlatiladi.
# DIQQAT: faylda loginlar VA parollar bor. Uni begonalarga bermang. Litsenziya o'tkazilmaydi (u kompyuterga bog'langan).
set -eu
DATA="${EMAKTAB_DATA_DIR:-$HOME/Library/Application Support/eMaktab Manager}"
OUT="${EMAKTAB_OUT_DIR:-$HOME/Desktop}"
fail() { echo "XATO: $*" >&2; exit 1; }
[ -f "$DATA/manager.sqlite3" ] && [ -f "$DATA/vault.key" ] || fail "Akkauntlar topilmadi: $DATA (ilova hali ishlatilmagan)."
mkdir -p "$OUT"

W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
# Ilova ochiq bo'lsa ham xavfsiz nusxa
sqlite3 "$DATA/manager.sqlite3" ".backup '$W/manager.sqlite3'" || fail "Bazadan nusxa olib bo'lmadi."
COUNT="$(sqlite3 "$W/manager.sqlite3" 'SELECT count(*) FROM accounts;')"
[ "${COUNT:-0}" -gt 0 ] || fail "O'tkazish uchun akkaunt yo'q."

# Bo'sh bo'limlar (akkauntsiz) Windows'ga o'tkazilmaydi
sqlite3 "$W/manager.sqlite3" "DELETE FROM account_groups WHERE id!='default' AND id NOT IN (SELECT group_id FROM accounts);" || fail "Bo'limlarni tozalab bo'lmadi."
# Ilovaning o'zi hisoblaydigan belgi (digest) bilan bir xil formula
DIGEST="$( { sqlite3 "$W/manager.sqlite3" "SELECT 'A|'||id||'|'||label||'|'||hex(secret)||'|'||group_id FROM accounts ORDER BY id;"
             sqlite3 "$W/manager.sqlite3" "SELECT 'G|'||id||'|'||name FROM account_groups ORDER BY id;"; } | shasum -a 256 | cut -d' ' -f1)"
# Jurnal va brauzer sessiyalarini olib tashlaymiz (Windows'da kerak emas), akkauntlarga tegilmaydi
sqlite3 "$W/manager.sqlite3" "DELETE FROM logs; DELETE FROM browser_sessions; UPDATE settings SET enabled=0; VACUUM;" || fail "Bazani tozalab bo'lmadi."
cp "$DATA/vault.key" "$W/vault.key"
SAVED="$(date -u +%Y-%m-%dT%H:%M:%S.0000000Z)"
printf '{"savedAt":"%s","digest":"%s","accounts":%s,"app":"mac-transfer"}' "$SAVED" "$DIGEST" "$COUNT" > "$W/meta.json"
(cd "$W" && zip -q -j pack.zip manager.sqlite3 vault.key meta.json) || fail "Paket yaratilmadi."
CREATED="$(date '+%Y-%m-%d %H:%M')"

cat > "$W/head.txt" <<'HEAD_END'
@echo off
setlocal
set "EMK_SELF=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$t=[IO.File]::ReadAllText($env:EMK_SELF); $i=$t.IndexOf('#PS'+'-START'); iex $t.Substring($i)"
echo.
pause
exit /b
HEAD_END

cat > "$W/restore.txt" <<'PS_END'
#PS-START
$ErrorActionPreference = 'Stop'
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072
    $lines = $t -split "`r?`n"
    $info = ($lines | Where-Object { $_.StartsWith('#INFO ') } | Select-Object -First 1).Substring(6) | ConvertFrom-Json
    $blob = [Convert]::FromBase64String((-join ($lines | Where-Object { $_.StartsWith('#B64 ') } | ForEach-Object { $_.Substring(5).Trim() })))
    $defaultDir = if ($env:EMAKTAB_APP_DIR) { $env:EMAKTAB_APP_DIR } else { Join-Path $env:LOCALAPPDATA 'eMaktab' }
    Write-Host ("Paket: " + $info.accounts + " ta akkaunt (" + $info.created + ", Mac'dan).")

    # 1) Ilovani topamiz yoki eng yangi relizdan yuklaymiz
    $searchDirs = @($env:EMAKTAB_APP_DIR, (Split-Path -Parent $env:EMK_SELF), (Join-Path $env:LOCALAPPDATA 'eMaktab'),
        [Environment]::GetFolderPath('Desktop'), (Join-Path $env:USERPROFILE 'Downloads'), [Environment]::GetFolderPath('MyDocuments'))
    $exe = $null
    foreach ($d in $searchDirs) {
        if ($d) { $p = Join-Path $d 'eMaktab.exe'; if (Test-Path -LiteralPath $p) { $exe = $p; break } }
    }
    if (-not $exe) {
        Write-Host "Ilova topilmadi. Eng yangi versiya yuklanmoqda (50 MB), biroz kuting..."
        $api = if ($env:EMAKTAB_RELEASES_URL) { $env:EMAKTAB_RELEASES_URL } else { 'https://api.github.com/repos/Steadfast11/emaktab-manager-desktop/releases?per_page=30' }
        try { $releases = Invoke-RestMethod -Uri $api -Headers @{ 'User-Agent' = 'eMaktab-installer' } -UseBasicParsing }
        catch { throw "GitHub'ga ulanib bo'lmadi. Internetni tekshiring va qayta urinib ko'ring." }
        $release = $null
        foreach ($r in @($releases)) {
            if ($r.draft) { continue }
            $names = @($r.assets | ForEach-Object { $_.name })
            if (($names -contains 'eMaktab.exe') -and ($names -contains 'eMaktab.exe.sha256')) { $release = $r; break }
        }
        if (-not $release) { throw "Reliz topilmadi (eMaktab.exe)." }
        $url = ($release.assets | Where-Object { $_.name -eq 'eMaktab.exe' } | Select-Object -First 1).browser_download_url
        $shaUrl = ($release.assets | Where-Object { $_.name -eq 'eMaktab.exe.sha256' } | Select-Object -First 1).browser_download_url
        $expected = (((New-Object Net.WebClient).DownloadString($shaUrl)).Trim() -split '\s+')[0].ToLower()
        if ($expected -notmatch '^[0-9a-f]{64}$') { throw "SHA256 fayli noto'g'ri." }
        New-Item -ItemType Directory -Force -Path $defaultDir | Out-Null
        $tmp = Join-Path $defaultDir 'eMaktab.exe.download'
        try { (New-Object Net.WebClient).DownloadFile($url, $tmp) }
        catch { throw "Ilovani yuklab bo'lmadi. Internetni tekshiring va qayta urinib ko'ring." }
        $got = ([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([IO.File]::ReadAllBytes($tmp)))).Replace('-', '').ToLower()
        if ($got -ne $expected) { Remove-Item -LiteralPath $tmp -Force; throw "Yuklangan fayl buzilgan (SHA256 mos emas). Qayta urinib ko'ring." }
        $exe = Join-Path $defaultDir 'eMaktab.exe'
        Move-Item -LiteralPath $tmp -Destination $exe -Force
        Write-Host ("Ilova o'rnatildi: $exe ($($release.tag_name))")
    }

    # 2) Akkauntlar paketini ilova yoniga qo'yamiz: ilova ishga tushganda o'zi tiklaydi yoki birlashtiradi
    $pack = Join-Path (Split-Path -Parent $exe) 'eMaktab-akkauntlar.emk'
    $sha = [Security.Cryptography.SHA256]::Create()
    $same = $false
    if (Test-Path -LiteralPath $pack) {
        $same = ([BitConverter]::ToString($sha.ComputeHash([IO.File]::ReadAllBytes($pack)))) -eq ([BitConverter]::ToString($sha.ComputeHash($blob)))
        if (-not $same) { Move-Item -LiteralPath $pack -Destination ($pack + '.eski-' + (Get-Date -Format 'yyyyMMdd-HHmmss')) }
    }
    if (-not $same) { [IO.File]::WriteAllBytes($pack, $blob) }
    Write-Host "Akkauntlar paketi qo'yildi: $pack"

    # 3) Ochamiz
    if ($env:EMAKTAB_NO_OPEN) { return }
    if (Get-Process -Name 'eMaktab' -ErrorAction SilentlyContinue) {
        Write-Host "Ilova hozir ochiq: akkauntlar ko'rinishi uchun uni yoping va qayta oching."
    } else {
        Start-Process -FilePath $exe
        Write-Host "Ilova ochilmoqda. Akkauntlar birinchi ochilishda tiklanadi."
    }
    Write-Host "TAYYOR."
} catch {
    Write-Host ("XATO: " + $_.Exception.Message) -ForegroundColor Red
}
PS_END

{ cat "$W/head.txt"; cat "$W/restore.txt"
  printf '#INFO {"created":"%s","accounts":%s}\n' "$CREATED" "$COUNT"
  base64 < "$W/pack.zip" | tr -d '\n' | fold -w 100 | sed 's/^/#B64 /'; echo
} | perl -pe 's/\r?\n/\r\n/' > "$OUT/eMaktab-Windows-akkauntlar.cmd"
chmod 600 "$OUT/eMaktab-Windows-akkauntlar.cmd"
perl -ne 'exit 1 if /[^\x00-\x7F]/' "$OUT/eMaktab-Windows-akkauntlar.cmd" || fail "Faylda ASCII bo'lmagan belgi qoldi (Windows buni buzadi)."
echo "Tayyor: $OUT/eMaktab-Windows-akkauntlar.cmd"
echo "Akkauntlar: $COUNT ta. Faylni Windows kompyuterga (flesh, Telegram 'Saqlangan xabarlar' yoki o'zingizga email) o'tkazib, ikki marta bosing."
echo "Ichida parollar bor: faylni begona odamga bermang."
if [ -z "${EMAKTAB_NO_PAUSE:-}" ] && [ -t 0 ]; then read -r -p "Enter bosing..." _ || true; fi
