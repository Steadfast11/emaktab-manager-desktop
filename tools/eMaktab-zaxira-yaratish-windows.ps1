# eMaktab Manager (Windows): hozirgi akkauntlardan zaxira yaratadi -> Ish stoli\eMaktab-zaxira.cmd
# Hech narsa o'rnatilmaydi (faqat Windows'dagi PowerShell). Zaxira faylni bossangiz:
#   1) ilova (eMaktab.exe) yo'q bo'lsa, GitHub'dan yuklaydi (50 MB, SHA256 tekshiriladi);
#   2) akkauntlarni ilova o'zining eMaktab-akkauntlar.emk mexanizmi orqali tiklaydi (bor akkauntlarga tegmaydi);
#   3) litsenziyani (kompyuter mos bo'lsa) tiklaydi va ilovani ochadi.
# DIQQAT: zaxirada loginlar va parollar bor. Begonalarga bermang.
$ErrorActionPreference = 'Stop'
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem

    $data = if ($env:EMAKTAB_DATA_DIR) { $env:EMAKTAB_DATA_DIR } else { Join-Path $env:LOCALAPPDATA 'eMaktabManager' }
    $outDir = if ($env:EMAKTAB_OUT_DIR) { $env:EMAKTAB_OUT_DIR } else { [Environment]::GetFolderPath('Desktop') }
    $db = Join-Path $data 'manager.sqlite3'
    $keyFile = Join-Path $data 'vault.key'
    if (-not (Test-Path -LiteralPath $db) -or -not (Test-Path -LiteralPath $keyFile)) {
        throw "Akkauntlar topilmadi: $data (ilova hali ishlatilmagan bo'lishi mumkin)."
    }

    # sqlite3.exe: ilovaning o'zi yuklab qo'ygan nusxasi (alohida o'rnatish kerak emas)
    $sqliteExe = $env:EMAKTAB_SQLITE
    if (-not $sqliteExe) {
        $runtime = Join-Path $env:LOCALAPPDATA 'eMaktabWebsiteRuntime'
        if (Test-Path -LiteralPath $runtime) {
            $found = Get-ChildItem -LiteralPath $runtime -Recurse -Filter 'sqlite3.exe' -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending | Select-Object -First 1
            if ($found) { $sqliteExe = $found.FullName }
        }
    }
    if (-not $sqliteExe) { throw "sqlite3.exe topilmadi. Ilovani bir marta oching va yoping, keyin qayta urinib ko'ring." }

    $work = Join-Path ([IO.Path]::GetTempPath()) ('emk-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $work | Out-Null
    $snapshot = Join-Path $work 'snapshot.sqlite3'

    function Invoke-Sqlite([string]$Database, [string]$Script, [bool]$Json) {
        $psi = New-Object Diagnostics.ProcessStartInfo
        $psi.FileName = $sqliteExe
        $flags = '-batch '
        if ($Json) { $flags += '-json ' }
        $psi.Arguments = $flags + '"' + $Database + '"'
        $psi.WorkingDirectory = $work
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $psi.RedirectStandardInput = $true
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.StandardOutputEncoding = [Text.Encoding]::UTF8
        $proc = [Diagnostics.Process]::Start($psi)
        $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes(".timeout 10000`n$Script`n")
        $proc.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
        $proc.StandardInput.Close()
        $errTask = $proc.StandardError.ReadToEndAsync()
        $text = $proc.StandardOutput.ReadToEnd()
        $proc.WaitForExit()
        return [pscustomobject]@{ Exit = $proc.ExitCode; Out = $text; Err = $errTask.Result }
    }

    # Ilova ochiq bo'lsa ham xavfsiz nusxa: sqlite3 .backup
    $r = Invoke-Sqlite $db ".backup 'snapshot.sqlite3'" $false
    if ($r.Exit -ne 0 -or -not (Test-Path -LiteralPath $snapshot)) { throw "Bazadan nusxa olib bo'lmadi: $($r.Err)" }

    $accounts = @(); $groups = @()
    $a = Invoke-Sqlite $snapshot 'SELECT id,label,hex(secret) AS s,group_id FROM accounts ORDER BY id;' $true
    $g = Invoke-Sqlite $snapshot 'SELECT id,name FROM account_groups ORDER BY id;' $true
    if ($a.Exit -ne 0 -or $g.Exit -ne 0) { throw "Bazani o'qib bo'lmadi: $($a.Err) $($g.Err)" }
    if ($a.Out.Trim().Length -gt 0) { $accounts = @($a.Out | ConvertFrom-Json) }
    if ($g.Out.Trim().Length -gt 0) { $groups = @($g.Out | ConvertFrom-Json) }
    if ($accounts.Count -eq 0) { throw "Zaxiralash uchun akkaunt yo'q." }

    # Ilovaning o'zi hisoblaydigan raqamli belgi (digest) bilan bir xil formula
    $sb = New-Object Text.StringBuilder
    foreach ($r in $accounts) { [void]$sb.Append("A|$($r.id)|$($r.label)|$($r.s)|$($r.group_id)`n") }
    foreach ($r in $groups) { [void]$sb.Append("G|$($r.id)|$($r.name)`n") }
    $sha = [Security.Cryptography.SHA256]::Create()
    $digest = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($sb.ToString())))).Replace('-', '').ToLower()

    # Jurnal va seanslarni olib tashlaymiz (xuddi ilovaning o'zidek), akkauntlarga tegilmaydi
    foreach ($stmt in @('DELETE FROM logs;', 'DELETE FROM browser_sessions;', 'UPDATE settings SET enabled=0;', 'VACUUM;')) {
        [void](Invoke-Sqlite $snapshot $stmt $false)
    }

    function Add-Entry($zip, [string]$name, [byte[]]$bytes) {
        $entry = $zip.CreateEntry($name, [IO.Compression.CompressionLevel]::Optimal)
        $stream = $entry.Open()
        try { $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Dispose() }
    }
    $now = [DateTime]::UtcNow
    $metaJson = '{"savedAt":"' + $now.ToString('o') + '","digest":"' + $digest + '","accounts":' + $accounts.Count + ',"app":"zaxira"}'
    $memory = New-Object IO.MemoryStream
    $zip = New-Object IO.Compression.ZipArchive($memory, [IO.Compression.ZipArchiveMode]::Create, $true)
    Add-Entry $zip 'manager.sqlite3' ([IO.File]::ReadAllBytes($snapshot))
    Add-Entry $zip 'vault.key' ([IO.File]::ReadAllBytes($keyFile))
    Add-Entry $zip 'meta.json' ((New-Object Text.UTF8Encoding($false)).GetBytes($metaJson))
    $zip.Dispose()
    $blob = $memory.ToArray()

    $licensePath = Join-Path $data 'license.key'
    $license = if (Test-Path -LiteralPath $licensePath) { ([IO.File]::ReadAllText($licensePath)).Trim() } else { '' }
    $machine = ''
    try { $machine = [string](Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Cryptography' -ErrorAction Stop).MachineGuid } catch { }
    $info = [ordered]@{ created = (Get-Date -Format 'yyyy-MM-dd HH:mm'); accounts = $accounts.Count; machine = $machine; license = $license }
    $infoJson = ($info | ConvertTo-Json -Compress)

    $b64 = [Convert]::ToBase64String($blob)
    $b64Lines = New-Object Collections.Generic.List[string]
    for ($i = 0; $i -lt $b64.Length; $i += 100) { $b64Lines.Add('#B64 ' + $b64.Substring($i, [Math]::Min(100, $b64.Length - $i))) }

    $header = @'
@echo off
setlocal
set "EMK_SELF=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$t=[IO.File]::ReadAllText($env:EMK_SELF); $i=$t.IndexOf('#PS'+'-START'); iex $t.Substring($i)"
echo.
pause
exit /b
'@

    $restore = @'
#PS-START
$ErrorActionPreference = 'Stop'
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072
    $lines = $t -split "`r?`n"
    $info = ($lines | Where-Object { $_.StartsWith('#INFO ') } | Select-Object -First 1).Substring(6) | ConvertFrom-Json
    $blob = [Convert]::FromBase64String((-join ($lines | Where-Object { $_.StartsWith('#B64 ') } | ForEach-Object { $_.Substring(5).Trim() })))
    $dataDir = if ($env:EMAKTAB_DATA_DIR) { $env:EMAKTAB_DATA_DIR } else { Join-Path $env:LOCALAPPDATA 'eMaktabManager' }
    $appUrl = 'https://github.com/Steadfast11/emaktab-manager-desktop/releases/download/v1.2.2/eMaktab.exe'
    $appSha = '@@APPSHA@@'
    $defaultDir = if ($env:EMAKTAB_APP_DIR) { $env:EMAKTAB_APP_DIR } else { Join-Path $env:LOCALAPPDATA 'eMaktab' }

    Write-Host ("Zaxira: " + $info.accounts + " ta akkaunt (" + $info.created + ").")

    # 1) Ilovani topamiz yoki yuklaymiz
    $searchDirs = @($env:EMAKTAB_APP_DIR, (Split-Path -Parent $env:EMK_SELF), (Join-Path $env:LOCALAPPDATA 'eMaktab'),
        [Environment]::GetFolderPath('Desktop'), (Join-Path $env:USERPROFILE 'Downloads'), [Environment]::GetFolderPath('MyDocuments'))
    $exe = $null
    foreach ($d in $searchDirs) {
        if ($d) { $p = Join-Path $d 'eMaktab.exe'; if (Test-Path -LiteralPath $p) { $exe = $p; break } }
    }
    if (-not $exe) {
        Write-Host "Ilova topilmadi. Yuklanmoqda (50 MB), biroz kuting..."
        New-Item -ItemType Directory -Force -Path $defaultDir | Out-Null
        $tmp = Join-Path $defaultDir 'eMaktab.exe.download'
        try { (New-Object Net.WebClient).DownloadFile($appUrl, $tmp) }
        catch { throw "Ilovani yuklab bo'lmadi. Internetni tekshiring va qayta urinib ko'ring." }
        $got = (Get-FileHash -Algorithm SHA256 -LiteralPath $tmp).Hash.ToLower()
        if ($got -ne $appSha) { Remove-Item -LiteralPath $tmp -Force; throw "Yuklangan fayl buzilgan (SHA256 mos emas). Qayta urinib ko'ring." }
        $exe = Join-Path $defaultDir 'eMaktab.exe'
        Move-Item -LiteralPath $tmp -Destination $exe -Force
        Write-Host "Ilova o'rnatildi: $exe"
    }

    # 2) Akkauntlar paketini ilova yoniga qo'yamiz: ilova ishga tushganda o'zi tiklaydi/birlashtiradi
    $pack = Join-Path (Split-Path -Parent $exe) 'eMaktab-akkauntlar.emk'
    $sha = [Security.Cryptography.SHA256]::Create()
    $same = $false
    if (Test-Path -LiteralPath $pack) {
        $same = ([BitConverter]::ToString($sha.ComputeHash([IO.File]::ReadAllBytes($pack)))) -eq ([BitConverter]::ToString($sha.ComputeHash($blob)))
        if (-not $same) { Move-Item -LiteralPath $pack -Destination ($pack + '.eski-' + (Get-Date -Format 'yyyyMMdd-HHmmss')) }
    }
    if (-not $same) { [IO.File]::WriteAllBytes($pack, $blob) }
    Write-Host "Akkauntlar paketi qo'yildi: $pack"

    # 3) Litsenziya: faqat shu kompyuterga mos bo'lsa va yo'q bo'lsa
    $licensePath = Join-Path $dataDir 'license.key'
    if ($info.license -and -not (Test-Path -LiteralPath $licensePath)) {
        $machine = ''
        try { $machine = [string](Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Cryptography' -ErrorAction Stop).MachineGuid } catch { }
        if ($machine -and $machine -eq $info.machine) {
            New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
            [IO.File]::WriteAllText($licensePath, [string]$info.license, [Text.Encoding]::ASCII)
            Write-Host "Litsenziya tiklandi."
        } else {
            Write-Host "Litsenziya bu kompyuterga mos emas (Windows qayta o'rnatilgan). Ilova yangi litsenziya so'raydi."
        }
    }

    # 4) Ochamiz
    if ($env:EMAKTAB_NO_OPEN) { return }
    if (Get-Process -Name 'eMaktab' -ErrorAction SilentlyContinue) {
        Write-Host "Ilova hozir ochiq: akkauntlar ko'rinishi uchun uni yoping va qayta oching."
    } else {
        Start-Process -FilePath $exe
        Write-Host "Ilova ochilmoqda. Akkauntlar birinchi ochilishda tiklanadi."
    }
} catch {
    Write-Host ("XATO: " + $_.Exception.Message) -ForegroundColor Red
}
'@

    $restore = $restore.Replace('@@APPSHA@@', '7bf7c7273743e320eb21b3e880f221096ac09c46417112bd3f05b6239b23e204')
    $text = ($header.TrimEnd() + "`n" + $restore.TrimEnd() + "`n#INFO " + $infoJson + "`n" + ($b64Lines -join "`n") + "`n") -replace "`r?`n", "`r`n"
    $target = Join-Path $outDir 'eMaktab-zaxira.cmd'
    [IO.File]::WriteAllText($target, $text, [Text.Encoding]::ASCII)

    Write-Host "Zaxira tayyor: $target"
    Write-Host ("Akkauntlar: " + $accounts.Count + " ta, guruhlar: " + $groups.Count + " ta")
    Write-Host "Ichida parollar va litsenziya bor: faylni begona odamga bermang."
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
} catch {
    Write-Host ("XATO: " + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
