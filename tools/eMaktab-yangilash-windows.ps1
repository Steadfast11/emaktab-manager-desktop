# eMaktab Manager (Windows): o'rnatilgan ilovani GitHub'dan yangilaydi, ma'lumotlarni yo'qotmasdan.
#  - ilova ochiq bo'lsa, o'zi yopadi (akkauntlar saqlanadi);
#  - akkauntlar va litsenziyadan zaxira nusxa oladi (eMaktabManager\yangilash-zaxira-...);
#  - eski faylni .eski nomi bilan saqlaydi; ilova ichidagi akkauntlar ombori yangi faylga ko'chiriladi;
#  - yangi faylning SHA256 si tekshiriladi.
$ErrorActionPreference = 'Stop'
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072
    $version = '1.2.3'
    $url = 'https://github.com/Steadfast11/emaktab-manager-desktop/releases/download/v1.2.3/eMaktab.exe'
    $expected = '8c2a158021f6923ca7038d7d10c4179153156e70355d9fc3e8303108349ba7ca'
    $dataDir = if ($env:EMAKTAB_DATA_DIR) { $env:EMAKTAB_DATA_DIR } else { Join-Path $env:LOCALAPPDATA 'eMaktabManager' }
    $defaultDir = if ($env:EMAKTAB_APP_DIR) { $env:EMAKTAB_APP_DIR } else { Join-Path $env:LOCALAPPDATA 'eMaktab' }

    # 1) O'rnatilgan nusxalarni topamiz
    $targets = New-Object Collections.Generic.List[string]
    function Add-Target([string]$path) {
        if ($path -and (Test-Path -LiteralPath $path) -and -not ($targets | Where-Object { $_ -ieq $path })) { $targets.Add($path) }
    }
    $running = @(Get-Process -Name 'eMaktab' -ErrorAction SilentlyContinue)
    foreach ($p in $running) { try { Add-Target $p.Path } catch { } }
    $roots = @($env:EMAKTAB_APP_DIR, (Join-Path $env:LOCALAPPDATA 'eMaktab'), [Environment]::GetFolderPath('Desktop'),
        (Join-Path $env:USERPROFILE 'Downloads'), [Environment]::GetFolderPath('MyDocuments'))
    foreach ($root in $roots) {
        if (-not $root -or -not (Test-Path -LiteralPath $root)) { continue }
        Add-Target (Join-Path $root 'eMaktab.exe')
        foreach ($sub in @(Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue)) { Add-Target (Join-Path $sub.FullName 'eMaktab.exe') }
    }

    function Get-FileVer([string]$path) {
        try {
            $v = (Get-Item -LiteralPath $path).VersionInfo.FileVersion
            if ($v) { return [Version]($v -replace '[^0-9.]', '') }
        } catch { }
        return $null
    }
    $need = @($targets | Where-Object { $v = Get-FileVer $_; -not ($v -and $v -ge [Version]$version) })
    if ($targets.Count -gt 0 -and $need.Count -eq 0) {
        Write-Host "Ilova allaqachon yangi ($version). Yangilash kerak emas."
        return
    }

    # 2) Yangi faylni yuklab, tekshiramiz
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ('emaktab-' + [Guid]::NewGuid().ToString('N') + '.exe')
    Write-Host "Yangi versiya yuklanmoqda (50 MB), biroz kuting..."
    try { (New-Object Net.WebClient).DownloadFile($url, $tmp) }
    catch { throw "Yuklab bo'lmadi. Internetni tekshiring va qayta urinib ko'ring. Hech narsa o'zgarmadi." }
    $got = (Get-FileHash -Algorithm SHA256 -LiteralPath $tmp).Hash.ToLower()
    if ($got -ne $expected) { Remove-Item -LiteralPath $tmp -Force; throw "Yuklangan fayl buzilgan (SHA256 mos emas). Hech narsa o'zgarmadi." }

    # 3) Ilovani yopamiz (akkauntlar yopilganda saqlanadi)
    $wasRunning = $running.Count -gt 0
    if ($wasRunning) {
        Write-Host "Ilova yopilmoqda..."
        foreach ($p in $running) { try { [void]$p.CloseMainWindow() } catch { } }
        $deadline = (Get-Date).AddSeconds(40)
        while ((Get-Process -Name 'eMaktab' -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }
        Get-Process -Name 'eMaktab' -ErrorAction SilentlyContinue | Stop-Process -Force
    }
    # Yopilgach akkauntlarni exe ichiga yozadigan yordamchi (saver) tugashini kutamiz
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Process -Name 'saver' -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }

    # 4) Ma'lumotlardan zaxira nusxa
    if (Test-Path -LiteralPath (Join-Path $dataDir 'manager.sqlite3')) {
        $backup = Join-Path $dataDir ('yangilash-zaxira-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
        New-Item -ItemType Directory -Force -Path $backup | Out-Null
        foreach ($name in @('manager.sqlite3', 'manager.sqlite3-wal', 'manager.sqlite3-shm', 'vault.key', 'license.key')) {
            $f = Join-Path $dataDir $name
            if (Test-Path -LiteralPath $f) { Copy-Item -LiteralPath $f -Destination $backup }
        }
        Write-Host "Ma'lumotlar zaxirasi: $backup"
        Get-ChildItem -LiteralPath $dataDir -Directory -Filter 'yangilash-zaxira-*' | Sort-Object Name -Descending |
            Select-Object -Skip 5 | ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }
    }

    # 5) Eski faylning ichidagi akkauntlar omborini (oxiridagi blok) topamiz: yangi faylga ko'chiramiz
    function Get-Tail([string]$path) {
        $fs = [IO.File]::Open($path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
        try {
            if ($fs.Length -lt 49) { return $null }
            $footer = New-Object byte[] 48
            [void]$fs.Seek(-48, [IO.SeekOrigin]::End)
            [void]$fs.Read($footer, 0, 48)
            if ([Text.Encoding]::ASCII.GetString($footer, 0, 8) -ne 'EMKDATA1') { return $null }
            $size = [BitConverter]::ToInt64($footer, 8)
            if ($size -le 0 -or $size -gt ($fs.Length - 48) -or $size -gt 1GB) { return $null }
            $blob = New-Object byte[] ([int]$size)
            [void]$fs.Seek(-(48 + $size), [IO.SeekOrigin]::End)
            $read = 0
            while ($read -lt $size) { $n = $fs.Read($blob, $read, [int]($size - $read)); if ($n -le 0) { break }; $read += $n }
            if ($read -ne $size) { return $null }
            $digest = [Security.Cryptography.SHA256]::Create().ComputeHash($blob)
            for ($i = 0; $i -lt 32; $i++) { if ($digest[$i] -ne $footer[16 + $i]) { return $null } }
            $tail = New-Object byte[] ($blob.Length + 48)
            [Array]::Copy($blob, 0, $tail, 0, $blob.Length)
            [Array]::Copy($footer, 0, $tail, $blob.Length, 48)
            return , $tail
        } finally { $fs.Dispose() }
    }

    # 6) Almashtiramiz
    if ($targets.Count -eq 0) {
        New-Item -ItemType Directory -Force -Path $defaultDir | Out-Null
        $first = Join-Path $defaultDir 'eMaktab.exe'
        Copy-Item -LiteralPath $tmp -Destination $first -Force
        Write-Host "Ilova o'rnatildi: $first"
    } else {
        foreach ($t in $need) {
            $tail = $null
            try { $tail = Get-Tail $t } catch { Write-Host "Diqqat: $t ichidagi akkauntlar ombori o'qilmadi (ma'lumotlar papkasi va zaxira joyida)." }
            $new = $t + '.yangi'
            Copy-Item -LiteralPath $tmp -Destination $new -Force
            if ($tail) {
                $out = [IO.File]::Open($new, [IO.FileMode]::Append)
                try { $out.Write($tail, 0, $tail.Length) } finally { $out.Dispose() }
            }
            $old = $t + '.eski'
            if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Force }
            Move-Item -LiteralPath $t -Destination $old
            try { Move-Item -LiteralPath $new -Destination $t }
            catch { Move-Item -LiteralPath $old -Destination $t; throw }
            Write-Host ("Yangilandi: $t" + $(if ($tail) { ' (akkauntlar ombori ko''chirildi)' } else { '' }))
        }
        $first = $targets[0]
    }
    Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue

    Write-Host "TAYYOR: eMaktab $version. Akkauntlar va litsenziya joyida."
    if (-not $env:EMAKTAB_NO_OPEN) { Start-Process -FilePath $first }
} catch {
    Write-Host ("XATO: " + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
