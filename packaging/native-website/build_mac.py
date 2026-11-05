"""Cross-build eMaktab.exe (Windows x64, .NET Framework) on macOS/Linux with mono's Roslyn csc.

    python packaging/native-website/build_mac.py --backend manager.exe --sqlite sqlite3.exe --out website-native
Same payload layout as build.py (which needs the Windows csc.exe).
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--backend', type=Path, required=True)
    p.add_argument('--sqlite', type=Path, required=True)
    p.add_argument('--out', type=Path, default=Path('website-native'))
    a = p.parse_args()
    a.out = a.out.resolve()
    a.out.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parent
    csc = ['csc', '/nologo', '/langversion:5', '/platform:x64', '/optimize+', '/codepage:65001']
    saver = a.out / 'saver.exe'
    subprocess.run(csc + ['/target:winexe', '/out:' + str(saver), str(source / 'Saver.cs')], check=True)
    payload = a.out / 'payload.zip'
    with zipfile.ZipFile(payload, 'w', compression=zipfile.ZIP_STORED) as z:
        for file, name in ((a.backend, 'manager.exe'), (a.sqlite, 'sqlite3.exe'), (saver, 'saver.exe')):
            z.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), Path(file).read_bytes())
    manifest = a.out / 'Payload.cs'
    manifest.write_text('namespace EMaktabWebsite { static class Payload { public const string Hash="' + sha(payload) + '"; public const string ManagerHash="' + sha(a.backend) + '"; public const string SqliteHash="' + sha(a.sqlite) + '"; public const string SaverHash="' + sha(saver) + '"; } }')
    output = a.out / 'eMaktab.exe'
    command = csc + ['/target:winexe', '/out:' + str(output), '/resource:' + str(payload) + ',Payload',
                     '/win32icon:' + str(source / 'icon.ico'), '/win32manifest:' + str(source / 'app.manifest')]
    command += ['/reference:' + name for name in ['System.Windows.Forms.dll', 'System.Drawing.dll', 'System.Web.Extensions.dll', 'System.IO.Compression.dll', 'System.IO.Compression.FileSystem.dll']]
    command += [str(source / 'Launcher.cs'), str(source / 'Portable.cs'), str(manifest)]
    subprocess.run(command, check=True)
    result = {'artifact': output.name, 'sha256': sha(output), 'size_bytes': output.stat().st_size, 'wrapper_version': '1.2.1', 'backend_version': '1.0.1',
              'console_subsystem': False, 'payload_entries': ['manager.exe', 'sqlite3.exe', 'saver.exe'], 'portable_accounts_in_exe': True,
              'contains_customer_data': False, 'publisher_signed': False, 'built_with': 'mono csc (cross-build)'}
    (a.out / 'build-result.json').write_text(json.dumps(result, indent=2) + '\n')
    (a.out / 'eMaktab.exe.sha256').write_text(result['sha256'] + '  eMaktab.exe\n')
    print('Built single GUI executable:', output)


if __name__ == '__main__':
    main()
