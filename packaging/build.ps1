# Builds dist\QuickEntryPlus.exe (one file, no console) and runs its self-test.
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File packaging\build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"

& $py (Join-Path $root "packaging\make_icon.py")
# the UI Automation wrapper is generated on first use; generate it now so it gets bundled
& $py -c "import comtypes.client; comtypes.client.GetModule('UIAutomationCore.dll')"

& $py -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name QuickEntryPlus `
    --icon (Join-Path $root "assets\icon.ico") `
    --version-file (Join-Path $root "packaging\version_info.txt") `
    --collect-submodules comtypes.gen `
    --collect-data faster_whisper --collect-binaries ctranslate2 `
    --exclude-module tkinter `
    --distpath (Join-Path $root "dist") `
    --workpath (Join-Path $root "build") `
    --specpath (Join-Path $root "build") `
    (Join-Path $root "run.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$exe = Join-Path $root "dist\QuickEntryPlus.exe"
$p = Start-Process $exe -ArgumentList "--selftest" -Wait -PassThru
$log = Join-Path $env:APPDATA "QuickEntryPlus\qeplus.log"
Get-Content $log -Tail 9 | Select-String "selftest"
if ($p.ExitCode -ne 0) { throw "self-test failed (exit $($p.ExitCode)); see $log" }
"{0:N1} MB  {1}" -f ((Get-Item $exe).Length / 1MB), $exe
