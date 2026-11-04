param([Parameter(Mandatory=$true)][string]$Candidate,[Parameter(Mandatory=$true)][string]$Work)
# Windows acceptance test for accounts stored inside eMaktab.exe. Synthetic accounts only.
$ErrorActionPreference='Stop'
$fixture=Join-Path $PSScriptRoot 'portable_fixture.py'
$candidateHash=(Get-FileHash $Candidate -Algorithm SHA256).Hash
New-Item -ItemType Directory -Force $Work|Out-Null

function Py {
  & python $fixture @args
  if($LASTEXITCODE -ne 0){throw ('fixture command failed: '+($args -join ' '))}
}
function Expect($folder,$name,$accounts,$groups) {
  $file=Join-Path $Work ($name+'.expected.json')
  Set-Content -Path $file -Value ($accounts|ConvertTo-Json -Compress) -Encoding utf8
  if($groups){Py expect $folder $file $groups}else{Py expect $folder $file}
}
function New-Case($name) {
  $dir=Join-Path $Work $name
  Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
  New-Item -ItemType Directory -Force $dir|Out-Null
  Copy-Item $Candidate (Join-Path $dir 'eMaktab.exe')
  return $dir
}
function Run-Exe($dir) {
  $report=Join-Path $dir 'result.json'
  Remove-Item $report -ErrorAction SilentlyContinue
  $process=Start-Process -FilePath (Join-Path $dir 'eMaktab.exe') -ArgumentList @('--test-portable',('"'+$report+'"')) -PassThru
  if(!$process.WaitForExit(150000)){Stop-Process -Id $process.Id -Force;throw 'eMaktab.exe test run timed out'}
  if($process.ExitCode -ne 0){if(Test-Path $report){Get-Content $report};throw ('eMaktab.exe failed with '+$process.ExitCode)}
  Wait-Saver
}
function Wait-Saver {
  $deadline=(Get-Date).AddSeconds(120)
  do {Start-Sleep -Seconds 1} while((Get-Process -Name saver -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline)
  if(Get-Process -Name saver -ErrorAction SilentlyContinue){throw 'saver did not finish'}
}
function Show-Log($dir) {
  $log=Join-Path $dir 'data\portable.log'
  if(Test-Path $log){Write-Output ('--- portable.log ('+(Split-Path $dir -Leaf)+')');Get-Content $log}
}

$setA=[ordered]@{'shared@mail.uz'='incoming-pw';'only-in-a'='pwA2';'only-in-a-2'='pwA3'}
$setB=[ordered]@{'shared@mail.uz'='live-pw';'only-in-b'='pwB2'}
$mergedLiveWins=[ordered]@{'shared@mail.uz'='live-pw';'only-in-a'='pwA2';'only-in-a-2'='pwA3';'only-in-b'='pwB2'}
$mergedIncomingWins=[ordered]@{'shared@mail.uz'='incoming-pw';'only-in-a'='pwA2';'only-in-a-2'='pwA3';'only-in-b'='pwB2'}

# 1. Fresh computer: exe carries accounts, nothing is installed yet -> accounts appear.
$srcA=Join-Path $Work 'source-A'; Py make $srcA A
$case=New-Case 'restore'
Py attach (Join-Path $case 'eMaktab.exe') $srcA
Run-Exe $case; Show-Log $case
Expect (Join-Path $case 'data') 'restore' $setA
if((Get-FileHash (Join-Path $srcA 'vault.key')).Hash -ne (Get-FileHash (Join-Path $case 'data\vault.key')).Hash){throw 'restore changed the encryption key'}
Write-Output 'PORTABLE_RESTORE_ON_FRESH_PC_OK'

# 2. Existing installation (old version) -> accounts are absorbed into the exe; wiping the PC data still restores them.
$case=New-Case 'embed'
Py make (Join-Path $case 'data') B
$keyHash=(Get-FileHash (Join-Path $case 'data\vault.key')).Hash
Run-Exe $case; Show-Log $case
Py has-trailer (Join-Path $case 'eMaktab.exe')
Py extract (Join-Path $case 'eMaktab.exe') (Join-Path $case 'embedded')|Out-Null
Expect (Join-Path $case 'embedded') 'embedded' $setB
Remove-Item (Join-Path $case 'data') -Recurse -Force
Remove-Item (Join-Path $case 'runtime') -Recurse -Force
Run-Exe $case; Show-Log $case
Expect (Join-Path $case 'data') 'embed-after-wipe' $setB
if((Get-FileHash (Join-Path $case 'data\vault.key')).Hash -ne $keyHash){throw 'wipe+restore changed the encryption key'}
Write-Output 'PORTABLE_EMBED_WIPE_RESTORE_OK'

# 3. Exe from another computer opened where accounts already exist -> union; local copy wins a conflict when it changed.
$srcA2=Join-Path $Work 'source-A2'; Py make $srcA2 A
$case=New-Case 'merge-live'
Py attach (Join-Path $case 'eMaktab.exe') $srcA2
Py make (Join-Path $case 'data') B
Run-Exe $case; Show-Log $case
Expect (Join-Path $case 'data') 'merge-live' $mergedLiveWins 2
Py extract (Join-Path $case 'eMaktab.exe') (Join-Path $case 'embedded')|Out-Null
Expect (Join-Path $case 'embedded') 'merge-live-embedded' $mergedLiveWins 2
Write-Output 'PORTABLE_MERGE_LOCAL_WINS_OK'

# 4. Local data unchanged since the last sync -> the exe's newer version wins a conflict.
$srcA3=Join-Path $Work 'source-A3'; Py make $srcA3 A
$case=New-Case 'merge-incoming'
Py attach (Join-Path $case 'eMaktab.exe') $srcA3
Py make (Join-Path $case 'data') B
Py sync (Join-Path $case 'data')
Run-Exe $case; Show-Log $case
Expect (Join-Path $case 'data') 'merge-incoming' $mergedIncomingWins 2
Write-Output 'PORTABLE_MERGE_EXE_WINS_OK'

# 5. The distributable build itself must never contain account data.
if((Get-FileHash $Candidate -Algorithm SHA256).Hash -ne $candidateHash){throw 'Candidate exe was modified'}
& python $fixture has-trailer $Candidate
if($LASTEXITCODE -eq 0){throw 'Candidate exe contains a data trailer'}
Write-Output 'PORTABLE_CLEAN_DISTRIBUTION_OK'
exit 0
