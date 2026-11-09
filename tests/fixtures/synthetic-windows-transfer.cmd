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
#INFO {"created":"2026-11-09 12:14","accounts":3}
#B64 UEsDBBQAAAAIANFhaV1dgdFzXgUAAACwAAAPABwAbWFuYWdlci5zcWxpdGUzVVQJAAPacvFq2nLxanV4CwABBPUBAAAEFAAAAO3d
#B64 y27iVhgHcDtMQkJKGKmKUBSN5K4S1NAxAQK0GrVAnHALtxhCkDrI4AM4MTbYBhLUDTNv0F2lqs/QfV+gD9D9SFXVqlWXXVc1kAuQ
#B64 RDOrjjL6/2TLsY+xz3e+Y4sDMjnOpSSDMHVVawkG46WeUjT90VcMQ1HUojmvUrcWzPnJ1DpNvd0i9dl3P9odf1M22xtq7WfHT2sv
#B64 7d9+9Lu9sPrv6g+2N6tfv8MxAAAAAADe1TcrVqfbTQ8/NYSqTKqa2teJVtGJrkuqos+v26J5LsxzDB+OpDhmvnRbqNXUrmJUJJHh
#B64 uRLPZPPxo3D+lElypzuMoZ4ThYmkMhEmneGZdCGVcn1pW3Ieu2lKUkRyoXdk8512Rega6ni9Mn/8imd+y+rL5SXn8+d0a1x7jdTU
#B64 HtEuK4JhkFbb0O9sWJmp/53ibfMdvrlD6raCQ2K1Ojc36defXLUPEc6JdrVYnmuN8cZtM/h4mucOufx0/Ew0xkWTZuELj2uH0Q3B
#B64 HFOM2+j6VMw+dxAupHhmK76f4rZ2mKqs1s6JWLmu03U5e1tktrUk3ym9iuamFlNFpCeJRKldnfrmjFuu1qLV6XLRw/VxmNd5bGhq
#B64 t63Pri3NBD1btn1v4hWhNRer64ulJWfG9VDeZw9a8cyuW4fxJ1bns2f0KzKurE4MQ1Ia+vVycaaC11vfKS1EGR1QvK/lDKk132ps
#B64 4HOWNRMlC7pREW8SetuqO0xLFR9Mc59Ut1xNi9W5vk4PveNQZLWhj+YnMyGMtjxQ/R1m7pKbqqh5dvMqERqTNdewTY9b7bV3OsXX
#B64 ydUX7kvrAwmVhSqRZ8MyuzSpacSYvb4nHb2rP9QEkigTs5VEM5GS/NBO5g7d9qh5ryMcd4Obe8zdF4ikLnRlY8v1fGHJGX32ll52
#B64 2790i3k/XJ6/QdrN2WL/nnLsrInmAgAAAAAAAN6DIWdZ2dh8xa/bqU39UjGau8eSovaY3UZ4JNIJllLCfiMpn7sDA2+BHKVPs9lY
#B64 ONNh2bTR80T77Za63x4Yg4v6caLJRgPNSMjfKldOsiFfulnyDbh2qprLyPH8WSIWC8abRe9ZoZk6YXvRouFm65eDelbXtEYlmZSL
#B64 kfPOXrbnO833TnTpWPPFSLB42RTjg4xYL5ylzk5JJxsIF72+cMSbC794MRr8Xg1Vh9zCbByeSRye6Tii2RhX6+V1EnLHYpFdLwll
#B64 3RdaOl8q6oeJSExR/Vq0HIhcCC3/JW8M9ILKe+Tdk8Cg1Cn4MudHzYDIiwcpeS/UPQiK5b39s8S5spcLu41uPJput0JCPcvzkVSs
#B64 m6n5g8mcnthtqB5d6gajYaOSUKO+Il9mk1KuqFXLJ6lk1QjupufjoGfjYCdxsNNx5FTe0OrkxN/usUXfGa/Fa3LTp2VyBh84Etzh
#B64 Qqd4FGl4m0pZKOZVI17u5nWlryi+TtBTklpkkO96PCTXjhnZTDqSjWW9wuG+290/DmmnZ3LCn2dLob3EZaB+UFfZ/gGfj/ilTulA
#B64 FPS9XpfllI4ktPSwx59UYruewzJXU/Zjg/5MHNN9zGbOFscvlOMvxxtzAQAAAAAAAACPhM2yQU8+L7Lc/OlZWLFsrEw+tZjed/T9
#B64 P+X45/1UFAAAAAAAAAD+F6PxP+34jTInAAAAAAAAAHjE1uglatlp/3j8iw59Up0um4z/f6XMCQAAAAAAAAAerae0ZXPj6sn/sK7q
#B64 0uV06ej5f9rxJ2VOAAAAAAAAAPDY2SybK/f8/t/k+/8/KHMCAAAAAAAAgMdtlbZS68vLy/bRf9+cLsDv/wEAAAAAAAB8+DD+BwAA
#B64 AAAAAPjwjZ7/x/gfAAAAAAAA4MP2H1BLAwQKAAAAAADRYWldX0ecxywAAAAsAAAACQAcAHZhdWx0LmtleVVUCQAD2nLxatpy8Wp1
#B64 eAsAAQT1AQAABBQAAABWaGprTWJDZGFkQ0FhWkQ3RnVfMVoxU3Y4ZXViZzhxMU1WT2JDbndkMTFRPVBLAwQUAAAACADRYWldGuYa
#B64 E34AAACYAAAACQAcAG1ldGEuanNvblVUCQAD2nLxatpy8Wp1eAsAAQT1AQAABBQAAAAljLsOwjAMAP8lM0W245g0W/+BiS0PGzFQ
#B64 qqawIP6dIm476XRv1/NL27S55AhIBsQBxjOcEnLyfIQ/F3dw7XbV/uskFqOi0kSUUJjEs4VYlHMoUFoICHGEYhZZM4p4HaMRh1Ix
#B64 WKP9lWt9POetu+R3WZb9es912NY8d9PVfb5QSwECHgMUAAAACADRYWldXYHRc14FAAAAsAAADwAYAAAAAAAAAAAApIEAAAAAbWFu
#B64 YWdlci5zcWxpdGUzVVQFAAPacvFqdXgLAAEE9QEAAAQUAAAAUEsBAh4DCgAAAAAA0WFpXV9HnMcsAAAALAAAAAkAGAAAAAAAAQAA
#B64 AICBpwUAAHZhdWx0LmtleVVUBQAD2nLxanV4CwABBPUBAAAEFAAAAFBLAQIeAxQAAAAIANFhaV0a5hoTfgAAAJgAAAAJABgAAAAA
#B64 AAEAAACkgRYGAABtZXRhLmpzb25VVAUAA9py8Wp1eAsAAQT1AQAABBQAAABQSwUGAAAAAAMAAwDzAAAA1wYAAAAA
