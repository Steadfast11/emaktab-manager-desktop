@echo off
setlocal
set "EMK_SELF=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$t=[IO.File]::ReadAllText($env:EMK_SELF); $i=$t.IndexOf('#PS'+'-START'); iex $t.Substring($i)"
echo.
pause
exit /b
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
        $got = (Get-FileHash -Algorithm SHA256 -LiteralPath $tmp).Hash.ToLower()
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
#INFO {"created":"2026-11-09 12:13","accounts":3}
#B64 UEsDBBQAAAAIALFhaV03roDLXwUAAACwAAAPABwAbWFuYWdlci5zcWxpdGUzVVQJAAOdcvFqnXLxanV4CwABBPUBAAAEFAAAAO3d
#B64 y27iVhgHcDtMQkJKGKmKUBSN5K4S1NAxgRDSatRyMZdACDGGAFLHAtsh5mKDbW5RN8y8QXeVqj5D932BPkD3I1VVq1Zddl3VQEiA
#B64 JJpZdZTR/ydbjn2Mfb7zHVsckEn2LCUbEnWhas2yQXmJpwRJfvQVRREEsWzO68StJXN+MrNOEm+3THz23Y92x9+EzfaG2PjZ8dPG
#B64 S/u3H/1uz63/u/6D7c361+9wDAAAAACAd/XNmtXpdpPDT41ypSFVNLWnSxqvS7ouq4q+uG4Ls0yQYyguGEox1GLpblkQ1I5i8LJI
#B64 cUyBozJs4iTIFqkkU9yjDLUuKVQodRqi0qcclc6lUq4vbSvOrJskZEWU+nq7Yb7T5ssdQx2v84vH5z2LW9Zfrq44nz8nm+Paa5Kg
#B64 diVtwJcNQ2q2DP3OhrW5+t8p3jXf4Zs7pG4rOJSsVuf2Nvn6k+v2kcp1SbterC60xnjjrhl8Is0xMYadjZ8Kx5lw0ix84XHtUbpR
#B64 NscU4zaanoqKMNFgLsVRO4lIitnZoyoNVahLIj+t07Scvi0y21pu3Cm9juamFjNFUlcWJUW4PvXNGXdczWWr0+Uih5vjMKd5rGpq
#B64 p6XPr63MBT1ftntv4pVycyFW1xcrK85T10N5nz8o75lftw4TT6zOZ8/IV9K4srpkGLJS1afL5bkKTre+U1okZXRA8b6WM+TmYqvR
#B64 h5/TtJmoRlk3ePEmobetukc1VfHBNPekyo7r0mJ1bm6SQ+84lIZa1Ufzk7kQRlseqP4etXDJzVTUPLt5lZSrkzXXsEWOW+21dzbF
#B64 0+TqS/el9YGENsoVqTEfltmlJUGTjPnre9LRO/pDTSCLDclsJdFMpNx4aCdzh05r1LzTCMfd4OYec/cFonRR7jSMHdfzpRVn+Nlb
#B64 etlt/9It5v1wdfEGaTdni/17wrG3IZoLAAAAAAAAeA+GjGVta/sVt2kntvWBYlzuZ2VF7VL71eBIqB0oJMV0vt836Gqmq9MDju34
#B64 Cz5WKfT9Z7kg11LpC7XClU7orh4XC0WhqiX0NBNItHm2VmJ9B2KfPo1ltYNo+jKSz5zHvN1wNxR218S8lrjyR3klkPG5i/U4e5BM
#B64 iAflnrvtizciqVJMSbazl6lIMc+V67nBCV/xejO1TkBXPNFqUDiN9l68GA1+r4eqQ2ZpPg7PJA7PbBwH2Vo57alcndXOB34vXaN9
#B64 x2dCXzfYI6/I+Ohis1g6jKnuYs4b7zfDyUyg2WZr6Vi+rnqKh3LX549d+Ur7vZys8KVYy1dV6eaVUNREphhjS5pcOD5nOwIXjp67
#B64 2+duRUz5Mm49p5w1+uFMhOZ6R9xx1l3PpBk2IGYNPVTwePiouhgHOR8HPYmDno0jX7rSgmftbJT2+664o9JpKHAstC7Z+IUQ9/An
#B64 BbcUzwUyl96+4c+1K7GjrBj1HbavZN+g3S6l+od6KFk7GITEtl6LDBShbujFXkDwa43qvvt83zjRtVDj0nfiDrO9vMpns3wywMRz
#B64 6TMjq6QGpUz0Yj/S54VsKdcKJ2K1SCWV7ubd83HM9jGbOVscvxCOvxxvzAUAAAAAAAAAPBI2yxY5+bzIcvOnZ2nNsrU2+dRidt/R
#B64 9/+E45/3U1EAAAAAAAAA+F+Mxv+k4zfCnAAAAAAAAADgEdsgV4hVp/3j8S869KTKbNlk/P8rYU4AAAAAAAAA8Gg9JS3bW9dP/gd1
#B64 VZcHs6Wj5/9Jx5+EOQEAAAAAAADAY2ezbK/d8/t/k+///yDMCQAAAAAAAAAet3XSSmyurq7aR/99c7YAv/8HAAAAAAAA8OHD+B8A
#B64 AAAAAADgwzd6/h/jfwAAAAAAAIAP239QSwMECgAAAAAAsWFpXV6jGfksAAAALAAAAAkAHAB2YXVsdC5rZXlVVAkAA51y8WqdcvFq
#B64 dXgLAAEE9QEAAAQUAAAAMnI4QzdkUHk1azFNcHJxcnFjSnc4MWNkbVUxeWhqbUxnU3hFYWlOTHhmcz1QSwMEFAAAAAgAsWFpXZNl
#B64 Bx58AAAAmAAAAAkAHABtZXRhLmpzb25VVAkAA51y8WqdcvFqdXgLAAEE9QEAAAQUAAAAJYwxDsIwDAD/4pkgp4a4zsYfmNgcJ0EM
#B64 lKoJLIi/E8RtJ53uDU1fJZ86RJhwCs57h3JGjp4i0R7/XGAH+XYt7dd5RayhGnEOpEqMrKoH8zZ74oCJWOZUNeUURCizFJPEaDUc
#B64 TTONl5o9nktvEGnIuo7rXc31TZdWywafL1BLAQIeAxQAAAAIALFhaV03roDLXwUAAACwAAAPABgAAAAAAAAAAACkgQAAAABtYW5h
#B64 Z2VyLnNxbGl0ZTNVVAUAA51y8Wp1eAsAAQT1AQAABBQAAABQSwECHgMKAAAAAACxYWldXqMZ+SwAAAAsAAAACQAYAAAAAAABAAAA
#B64 gIGoBQAAdmF1bHQua2V5VVQFAAOdcvFqdXgLAAEE9QEAAAQUAAAAUEsBAh4DFAAAAAgAsWFpXZNlBx58AAAAmAAAAAkAGAAAAAAA
#B64 AQAAAKSBFwYAAG1ldGEuanNvblVUBQADnXLxanV4CwABBPUBAAAEFAAAAFBLBQYAAAAAAwADAPMAAADWBgAAAAA=
