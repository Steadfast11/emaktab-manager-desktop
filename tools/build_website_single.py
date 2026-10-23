from pathlib import Path
import zipfile,hashlib,base64
r=Path(__file__).resolve().parents[1]
exe=r/'outputs/Windows-1.0.1/eMaktab-Manager-1.0.1-Windows-x64.exe'
sqlite=r/'work/ondemand/sqlite3.exe';script=r/'work/ondemand/start.ps1'
payload=r/'work/ondemand/payload.zip'
with zipfile.ZipFile(payload,'w',compression=zipfile.ZIP_STORED) as z:
    z.write(exe,'manager.exe');z.write(sqlite,'sqlite3.exe');z.write(script,'start.ps1')
with payload.open('rb') as f: sha=hashlib.file_digest(f,'sha256').hexdigest()
loader="""$ErrorActionPreference='Stop'; Add-Type -AssemblyName System.Windows.Forms; try { $cache=Join-Path $env:LOCALAPPDATA 'eMaktabWebsiteRuntime\\HASH_PREFIX'; $ready=Join-Path $cache 'ready'; if(!(Test-Path -LiteralPath $ready)){ New-Item -ItemType Directory -Force -Path $cache|Out-Null; $zip=Join-Path $cache 'payload.zip'; $reader=[IO.File]::OpenText($env:EMAKTAB_WEBSITE_FILE); $output=$null; try { do {$line=$reader.ReadLine();if($null -eq $line){throw 'Payload marker missing'}}while($line -ne ':EMAKTAB_WEBSITE_PAYLOAD'); $output=[IO.File]::Create($zip);while(($line=$reader.ReadLine()) -ne $null){if($line.Length){$bytes=[Convert]::FromBase64String($line);$output.Write($bytes,0,$bytes.Length)}} }finally {if($output){$output.Dispose()};$reader.Dispose()}; if((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLower() -ne 'HASH_FULL'){throw 'File is incomplete. Download it again.'}; Add-Type -AssemblyName System.IO.Compression.FileSystem; [IO.Compression.ZipFile]::ExtractToDirectory($zip,$cache); Remove-Item -LiteralPath $zip; Set-Content -LiteralPath $ready -Value 'HASH_FULL' }; $env:EMAKTAB_ONDEMAND_ROOT=$cache; & ([scriptblock]::Create([IO.File]::ReadAllText((Join-Path $cache 'start.ps1')))) }catch{if($env:EMAKTAB_ONDEMAND_TEST -eq '1'){throw};[System.Windows.Forms.MessageBox]::Show($_.Exception.Message,'eMaktab website')|Out-Null}""".replace('HASH_PREFIX',sha[:16]).replace('HASH_FULL',sha)
header='@echo off\r\nsetlocal\r\nset "EMAKTAB_WEBSITE_FILE=%~f0"\r\npowershell.exe -NoProfile -STA -WindowStyle Hidden -Command "'+loader+'"\r\nexit /b\r\n:EMAKTAB_WEBSITE_PAYLOAD\r\n'
out=r/'outputs/eMaktab-Website-Windows.cmd'
with out.open('wb') as output,payload.open('rb') as source:
    output.write(header.encode('ascii'))
    while chunk:=source.read(3*262144):output.write(base64.b64encode(chunk)+b'\r\n')
# Verify the exact shipped payload, not just the intermediate ZIP.
h=hashlib.sha256()
with out.open('rb') as source:
    for line in source:
        if line.strip()==b':EMAKTAB_WEBSITE_PAYLOAD':break
    for line in source:
        if line.strip():h.update(base64.b64decode(line.strip(),validate=True))
assert h.hexdigest()==sha
print(f'Created and payload-verified: {out} ({out.stat().st_size/1024**2:.1f} MiB)')
