"""Run with native Python 3.12 on each target OS. Builds from an explicit allowlist."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ('desktop.py', 'app.py', 'resilience.py', 'simulation.py', 'licensing.py', 'license_config.py')


def browser_payload(stage):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        executable = Path(pw.chromium.executable_path)
    if not executable.is_file():
        raise SystemExit('First run: python -m playwright install chromium --no-shell')
    browser = next(p for p in executable.parents if p.name.startswith('chromium-'))
    archive_path = stage / 'browser-payload.zip'
    with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=5) as archive:
        for path in sorted(browser.rglob('*')):
            name = path.relative_to(browser.parent).as_posix()
            if path.is_symlink():
                member = zipfile.ZipInfo(name)
                member.create_system = 3
                member.external_attr = path.lstat().st_mode << 16
                archive.writestr(member, os.readlink(path))
            elif path.is_file():
                archive.write(path, name)
    with archive_path.open('rb') as f:
        digest = hashlib.file_digest(f, 'sha256').hexdigest()
    (stage / 'browser-payload.json').write_text(json.dumps({'sha256': digest, 'executable': executable.relative_to(browser.parent).as_posix()}))


def create_icons(stage):
    from PIL import Image, ImageDraw, ImageFont
    icon = Image.new('RGBA', (1024, 1024))
    draw = ImageDraw.Draw(icon)
    draw.rounded_rectangle((0, 0, 1023, 1023), radius=220, fill='#102c25')
    draw.text((512, 460), 'eM', font=ImageFont.load_default(size=380), anchor='mm', fill='#c9f29c')
    draw.rounded_rectangle((310, 750, 714, 790), radius=20, fill='#c9f29c')
    icon.save(stage / 'icon.png')
    icon.save(stage / 'icon.icns')
    icon.save(stage / 'icon.ico', sizes=[(16,16),(32,32),(48,48),(128,128),(256,256)])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=ROOT / 'release')
    parser.add_argument('--skip-browser-payload', action='store_true', help='Developer iteration only; not a distributable release')
    args = parser.parse_args()
    out = args.out.resolve(); out.mkdir(parents=True, exist_ok=True)
    stage = out / 'stage'
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()
    for name in SOURCES:
        shutil.copy2(ROOT / name, stage / name)
    shutil.copytree(ROOT / 'static', stage / 'static')
    shutil.copy2(ROOT / 'LICENSE.txt', stage / 'LICENSE.txt')
    create_icons(stage)
    if not args.skip_browser_payload:
        browser_payload(stage)
    notices = ['eMaktab Manager includes third-party software under its respective licenses.\n']
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    licensed = {canonicalize_name(Requirement(line).name) for line in (ROOT / 'requirements-release-lock.txt').read_text().splitlines()
                if line and not line.lstrip().startswith('#') and not line.startswith(' ')}
    for dist in sorted(importlib.metadata.distributions(), key=lambda d:d.metadata.get('Name','')):
        if canonicalize_name(dist.metadata.get('Name','')) not in licensed:
            continue
        # Bundle license text, never distribution metadata containing local installation paths.
        for file in dist.files or []:
            if 'license' in str(file).lower() and file.suffix.lower() in ('', '.txt', '.md', '.rst'):
                p = Path(dist.locate_file(file))
                if p.is_file() and p.stat().st_size < 300000:
                    notices.extend([f'\n--- {dist.metadata.get("Name")} {dist.version}: {file.name} ---\n', p.read_text(errors='replace')])
    (stage / 'THIRD-PARTY-NOTICES.txt').write_text(''.join(notices), encoding='utf-8')
    command = [sys.executable, '-m', 'nuitka', '--mode=app', '--assume-yes-for-downloads',
               '--output-dir=' + str(out / 'native'), '--output-filename=eMaktab-Manager',
               '--include-module=app', '--include-module=resilience', '--include-module=simulation',
               '--include-module=uvicorn.lifespan.on', '--include-module=uvicorn.protocols.http.h11_impl',
               '--include-package-data=tzdata',
               '--include-data-dir=' + str(stage / 'static') + '=static',
               '--include-data-files=' + str(stage / 'LICENSE.txt') + '=LICENSE.txt',
               '--include-data-files=' + str(stage / 'THIRD-PARTY-NOTICES.txt') + '=THIRD-PARTY-NOTICES.txt',
               '--noinclude-pytest-mode=nofollow', '--noinclude-unittest-mode=nofollow',
               '--report=' + str(out / 'compilation-report.xml')]
    if not args.skip_browser_payload:
        command += ['--include-data-files=' + str(stage / name) + '=' + name for name in ('browser-payload.zip','browser-payload.json')]
    if sys.platform == 'darwin':
        command += ['--macos-app-name=eMaktab Manager', '--macos-signed-app-name=local.emaktab.manager',
                    '--macos-app-version=1.0.0', '--macos-app-icon=' + str(stage / 'icon.icns')]
    elif sys.platform == 'win32':
        # MSVC C1002 on Playwright's very large generated API module: use ClangCL.
        command += ['--clang', '--jobs=2', '--lto=no',
                    '--windows-console-mode=attach', '--windows-icon-from-ico=' + str(stage / 'icon.ico'),
                    '--company-name=eMaktab Manager', '--product-name=eMaktab Manager', '--file-version=1.0.0']
    command += [str(stage / 'desktop.py')]
    lines = []
    with subprocess.Popen(command, cwd=stage, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1) as compiler:
        for line in compiler.stdout:
            lines.append(line)
            print(line, end='', flush=True)
        compiled = subprocess.CompletedProcess(command, compiler.wait(), ''.join(lines))
    if compiled.returncode:
        # Finder/iCloud metadata can invalidate otherwise complete ad-hoc signing.
        # Repair only this specific signing failure, never hide compilation errors.
        if sys.platform == 'darwin' and 'resource fork, Finder information, or similar detritus not allowed' in compiled.stdout:
            # Finish signing below in a non-synced temporary directory. Finder
            # can immediately re-add forbidden metadata inside Documents/iCloud.
            pass
        else:
            compiled.check_returncode()
    native = out / 'native'
    if sys.platform == 'darwin':
        source_bundle = next(native.glob('*.app'))
        artifact = Path(tempfile.mkdtemp(prefix='emaktab-release-')) / 'eMaktab Manager.app'
        shutil.copytree(source_bundle, artifact, symlinks=True)
        subprocess.run(['xattr','-cr',str(artifact)],check=True)
        subprocess.run(['codesign','--force','--deep','--sign','-',str(artifact)],check=True)
        executable = next((artifact / 'Contents/MacOS').glob('eMaktab-Manager*'))
        subprocess.run(['codesign','--verify','--deep','--strict',str(artifact)],check=True)
    else:
        executable = native / ('eMaktab-Manager.exe' if os.name == 'nt' else 'eMaktab-Manager')
        artifact = executable
    forbidden = {'vault.key', 'license.key', 'issuer-private.pem', 'manager.sqlite3', 'lab.sqlite3', *SOURCES}
    for file in (artifact.rglob('*') if artifact.is_dir() else []):
        if file.name in forbidden or file.suffix in ('.pyc', '.pyo'):
            raise RuntimeError('Private data or source unexpectedly present in release: ' + file.name)
    # Gate publishing on the actual native executable, not the Python source.
    acceptance = subprocess.run([str(executable), '--self-test'], capture_output=True, text=True, timeout=180)
    print(acceptance.stdout, flush=True)
    print(acceptance.stderr, flush=True)
    acceptance.check_returncode()
    subprocess.run([sys.executable, '-m', 'pytest', str(ROOT / 'tests/test_desktop.py'), '-q'],
                   env=dict(os.environ, EMAKTAB_TEST_EXECUTABLE=str(executable)), check=True, timeout=180)
    label = {'darwin':'macOS','win32':'Windows'}.get(sys.platform, 'Linux') + '-' + platform.machine()
    if sys.platform == 'darwin':
        package = out / f'eMaktab-Manager-{label}.zip'
        subprocess.run(['ditto','-c','-k','--sequesterRsrc','--keepParent',str(artifact),str(package)],check=True)
    else:
        package = out / (f'eMaktab-Manager-{label}.exe' if os.name == 'nt' else f'eMaktab-Manager-{label}.bin')
        shutil.copy2(executable, package)
    with package.open('rb') as f:
        digest = hashlib.file_digest(f, 'sha256').hexdigest()
    (out / (package.name + '.sha256')).write_text(digest + '  ' + package.name + '\n')
    (out / 'build-result.json').write_text(json.dumps({'artifact': package.name, 'native_executable': str(executable), 'platform':sys.platform,'architecture':platform.machine(),'offline_acceptance':'passed','version':'1.0.0','sha256':digest},indent=2))
    print('Verified release:', package)


if __name__ == '__main__':
    main()
