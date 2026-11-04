import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--backend',type=Path,required=True)
    p.add_argument('--sqlite',type=Path,required=True)
    p.add_argument('--out',type=Path,default=Path('website-native'))
    a=p.parse_args();a.out=a.out.resolve();a.out.mkdir(parents=True,exist_ok=True)
    source=Path(__file__).resolve().parent
    compiler=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    saver=a.out/'saver.exe'
    subprocess.run([str(compiler),'/nologo','/target:winexe','/platform:x64','/optimize+','/codepage:65001','/out:'+str(saver),str(source/'Saver.cs')],check=True)
    payload=a.out/'payload.zip'
    with zipfile.ZipFile(payload,'w',compression=zipfile.ZIP_STORED) as z:
        z.write(a.backend,'manager.exe');z.write(a.sqlite,'sqlite3.exe');z.write(saver,'saver.exe')
    manifest=a.out/'Payload.cs'
    manifest.write_text('namespace EMaktabWebsite { static class Payload { public const string Hash="'+sha(payload)+'"; public const string ManagerHash="'+sha(a.backend)+'"; public const string SqliteHash="'+sha(a.sqlite)+'"; public const string SaverHash="'+sha(saver)+'"; } }')
    output=a.out/'eMaktab.exe'
    command=[str(compiler),'/nologo','/target:winexe','/platform:x64','/optimize+','/codepage:65001',
             '/out:'+str(output),'/resource:'+str(payload)+',Payload','/win32icon:'+str(source/'icon.ico'),
             '/win32manifest:'+str(source/'app.manifest')]
    command += ['/reference:'+name for name in ['System.Windows.Forms.dll','System.Drawing.dll','System.Web.Extensions.dll','System.IO.Compression.dll','System.IO.Compression.FileSystem.dll']]
    command += [str(source/'Launcher.cs'),str(source/'Portable.cs'),str(manifest)]
    subprocess.run(command,check=True)
    result={'artifact':output.name,'sha256':sha(output),'size_bytes':output.stat().st_size,'wrapper_version':'1.2.0','backend_version':'1.0.1','console_subsystem':False,'payload_entries':['manager.exe','sqlite3.exe','saver.exe'],'portable_accounts_in_exe':True,'contains_customer_data':False,'publisher_signed':False}
    (a.out/'build-result.json').write_text(json.dumps(result,indent=2)+'\n')
    (a.out/'eMaktab.exe.sha256').write_text(result['sha256']+'  eMaktab.exe\n')
    print('Built single GUI executable:',output)

if __name__=='__main__':main()
