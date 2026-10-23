$ErrorActionPreference='Stop'
[System.Net.WebRequest]::DefaultWebProxy=$null
Add-Type -AssemblyName System.Windows.Forms
$root=$env:EMAKTAB_ONDEMAND_ROOT
$data=Join-Path $env:LOCALAPPDATA 'eMaktabManager'
$runtime=Join-Path $data 'desktop.json'
$mutex=New-Object System.Threading.Mutex($false,'Local\eMaktabWebsiteOnDemand')
if(!$mutex.WaitOne(0)) { [System.Windows.Forms.MessageBox]::Show('eMaktab oynasi allaqachon ochiq.','eMaktab'); return }
$backend=$null;$browser=$null;$owned=$null
function Current-Server {
    if(!(Test-Path -LiteralPath $runtime)){return $null}
    try {
        $i=Get-Content -Raw -LiteralPath $runtime|ConvertFrom-Json
        $port=[int]$i.port;if($port -lt 1 -or $port -gt 65535){return $null}
        $base='http://127.0.0.1:'+$port
        $h=Invoke-RestMethod -Uri ($base+'/api/desktop/health') -Headers @{'X-Desktop-Token'=$i.token} -TimeoutSec 2
        if($h.product -eq 'eMaktab Manager' -and $h.instance -eq $i.instance){return @{base=$base;instance=$i.instance;pid=[int]$i.pid;version=$h.version}}
    }catch{}
    return $null
}
function Quit-Server($server) {
    Invoke-RestMethod -Uri ($server.base+'/api/desktop/quit') -Method Post -Body '{}' -ContentType 'application/json' -Headers @{'Origin'=$server.base;'X-Manager'='1'} -TimeoutSec 5|Out-Null
    $end=(Get-Date).AddSeconds(45)
    do {Start-Sleep -Milliseconds 300;$live=Current-Server;if(!$live -or $live.instance -ne $server.instance){return}}while((Get-Date) -lt $end)
    throw 'Old eMaktab server did not close. Close it before opening this file.'
}
try {
    $old=Current-Server
    if($old){Quit-Server $old}
    New-Item -ItemType Directory -Force -Path $data|Out-Null
    $db=Join-Path $data 'manager.sqlite3'
    if(Test-Path -LiteralPath $db){
        & (Join-Path $root 'sqlite3.exe') -batch $db '.timeout 10000' 'UPDATE settings SET enabled=0 WHERE id=1;'
        if($LASTEXITCODE -ne 0){throw 'Could not disable the saved schedule. No server was started.'}
    }
    $env:EMAKTAB_DATA_DIR=$data
    $backend=Start-Process -FilePath (Join-Path $root 'manager.exe') -ArgumentList '--no-browser' -PassThru
    $end=(Get-Date).AddSeconds(90)
    do {Start-Sleep -Milliseconds 300;$owned=Current-Server;if($owned){break};if($backend.HasExited){throw 'Local server exited before startup.'}}while((Get-Date) -lt $end)
    if(!$owned){throw 'Local server startup timed out.'}
    if($owned.version -ne '1.0.1'){throw 'Unexpected server version.'}
    # Only the bundled static stylesheet is updated; account storage is unchanged.
    $process=Get-Process -Id $owned.pid
    $static=Join-Path (Split-Path -Parent $process.Path) 'static'
    $css=Join-Path $static 'style.css'
    if(!(Test-Path -LiteralPath $css)){throw 'The local website stylesheet was not found.'}
    Add-Content -LiteralPath $css -Value "`n.lower>article:first-child{display:none!important}.lower{grid-template-columns:1fr!important}" -Encoding UTF8
    $chrome=Get-ChildItem -LiteralPath (Join-Path $data 'browser-cache') -Filter chrome.exe -Recurse|Select-Object -First 1
    if(!$chrome){throw 'Bundled browser was not found.'}
    $chromeProfile=Join-Path $data 'website-ui-profile'
    $chromeArguments=@(('--app='+$owned.base+'/'),('--user-data-dir="'+$chromeProfile+'"'),'--disable-background-mode','--no-first-run','--no-default-browser-check')
    $browser=Start-Process -FilePath $chrome.FullName -ArgumentList $chromeArguments -PassThru
    if($env:EMAKTAB_ONDEMAND_TEST -eq '1'){
        Start-Sleep -Seconds 4
        if($browser.HasExited){throw 'Website browser exited unexpectedly.'}
        $browser.Refresh()
        if(!$browser.CloseMainWindow()){throw 'The actual browser window could not be closed by the acceptance test.'}
    }
    while(!$browser.HasExited){
        Start-Sleep -Milliseconds 500
        if(!(Current-Server)){$browser.Refresh();$browser.CloseMainWindow()|Out-Null;break}
    }
} catch {
    if($env:EMAKTAB_ONDEMAND_TEST -eq '1'){throw}
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message,'eMaktab website')|Out-Null
} finally {
    if($owned){$live=Current-Server;if($live -and $live.instance -eq $owned.instance){try {Quit-Server $live}catch {}}}
    if($browser -and !$browser.HasExited){$browser.CloseMainWindow()|Out-Null}
    $mutex.ReleaseMutex();$mutex.Dispose()
}
if($env:EMAKTAB_ONDEMAND_TEST -eq '1'){
    if(Current-Server){throw 'Server remained alive after its browser closed.'}
    Write-Output 'ON_DEMAND_BROWSER_CLOSE_STOPS_SERVER_OK'
}
