# Windows single GUI executable

Customer delivery is only `eMaktab.exe`. The Windows GUI launcher embeds the
already compiled 1.0.1 backend (including Chromium) plus SQLite's official CLI.
It does not launch CMD, PowerShell, Python source, or a recipient installer.

The system CLR and Windows Forms are used. Customer target: Windows 11 x64.
First launch extracts verified runtime files into the current user's AppData.
The existing `eMaktabManager` data directory and offline license are preserved.
A normal Windows job object owns the started backend/browser process trees;
closing the UI, cancelling startup, or terminating the launcher releases them.

A named mutex prevents duplicate launchers; another click activates the existing
window. An older health-verified manager is closed through its own quit API.
The saved in-app schedule is disabled before the backend starts. Schedule controls
are hidden in its disposable extracted stylesheet, without modifying account data.

The output has Windows GUI subsystem, a custom icon, and an asInvoker manifest.
It has no publisher certificate. OS trust/security warnings are still possible.
Programmers can inspect managed launcher code; backend compilation and offline
license signatures do not promise absolute reverse-engineering resistance.

Build with Windows Python and the OS .NET Framework x64 compiler:

    python packaging/native-website/build.py --backend backend.exe --sqlite sqlite3.exe --out website-native

The GitHub workflow verifies the exact compiled output using a fresh user-data
store: actual visible browser window, close-to-stop lifecycle, warm reopen,
preserved vault/license/group data, disabled saved schedule, occupied-port
fallback, and automatic repair of a corrupted runtime cache. Test mode does not
activate licenses, run account cycles, or authenticate real eMaktab accounts.
