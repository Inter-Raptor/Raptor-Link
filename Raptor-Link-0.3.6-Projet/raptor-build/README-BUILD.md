# Rebuilding Raptor Link 0.3.6 Beta

The project archive is self-contained for Windows x64.

## Layout

- `RaptorLink/` — application sources, web interface, icon, Corsair SDK DLL and bundled Python runtime.
- `raptor-build/` — regression tests and NSIS installer scripts.

## Validate

From this folder in PowerShell:

```powershell
& ".\RaptorLink\runtime\python.exe" -m unittest discover -s raptor-build -p "test_*.py"
& ".\RaptorLink\runtime\python.exe" -m py_compile ".\RaptorLink\app.py" ".\RaptorLink\engine.py" ".\RaptorLink\support.py" ".\RaptorLink\icue_worker.py"
node --check ".\RaptorLink\web\ui.js"
node --check ".\RaptorLink\web\extras.js"
```

The JavaScript checks require Node.js. The application itself does not.

## Rebuild the launcher

Install NSIS, then run:

```powershell
& "C:\Program Files (x86)\NSIS\makensis.exe" ".\raptor-build\launcher.nsi"
```

## Rebuild the installer

The GitHub Actions workflow stages `RaptorLink/` as the installer payload. Locally, the simplest method is:

```powershell
Remove-Item ".\raptor-build\payload" -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item ".\RaptorLink" ".\raptor-build\payload" -Recurse
Push-Location ".\raptor-build"
& "C:\Program Files (x86)\NSIS\makensis.exe" "/DAPPDIR=payload" "/DOUTPUT=..\Raptor-Link-Setup-0.3.6.exe" ".\installer.nsi"
Pop-Location
Remove-Item ".\raptor-build\payload" -Recurse -Force
```

User configuration is stored outside the installation directory in `%LOCALAPPDATA%\AuroraWLED`.
