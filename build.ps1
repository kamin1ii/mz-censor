# Builds dist\AliceCensor.exe and checks that it actually starts.
#
# The check matters more than it sounds. A PyInstaller build can succeed
# and still produce an exe that dies instantly, which is exactly what
# happened here the first time: the entry script used a relative import
# that has no parent package once frozen. A build that is not launched is
# not a build that works.
#
# Usage:  .\build.ps1  [-KeepBuildDir] [-SkipTests]

param(
    [switch]$KeepBuildDir,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "No virtualenv found. Run: python -m venv .venv; .venv\Scripts\pip install -r requirements.txt"
}

Write-Host "==> Checking build dependencies" -ForegroundColor Cyan
& $python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "    installing pyinstaller"
    & $python -m pip install --quiet pyinstaller
    if ($LASTEXITCODE -ne 0) { Write-Error "pyinstaller install failed" }
}

if (-not $SkipTests) {
    Write-Host "==> Running tests" -ForegroundColor Cyan
    & $python -m pytest -q
    if ($LASTEXITCODE -ne 0) { Write-Error "tests failed, not building" }
}

# A previous copy still running holds a lock on the exe, and PyInstaller
# reports that as a bare PermissionError from os.remove that gives no hint
# what is wrong. Far better to say so and clear it.
$appName = "AliceCensor"

$running = Get-Process $appName -ErrorAction SilentlyContinue
if ($running) {
    Write-Host "==> Closing $($running.Count) running AliceCensor process(es) holding the exe" -ForegroundColor Yellow
    $running | Stop-Process -Force
    Start-Sleep -Milliseconds 500
}

Write-Host "==> Building" -ForegroundColor Cyan
& $python -m PyInstaller --noconfirm --clean alice-censor.spec
if ($LASTEXITCODE -ne 0) { Write-Error "PyInstaller failed" }

$exe = Join-Path $PSScriptRoot "dist\$appName.exe"
if (-not (Test-Path $exe)) { Write-Error "PyInstaller reported success but produced no exe" }

Write-Host "==> Verifying it launches" -ForegroundColor Cyan
# Offscreen so no window appears. The app has no batch mode, so staying
# alive in its event loop is the pass condition and exiting on its own
# means it crashed, usually a module or DLL excluded that was needed.
$env:QT_QPA_PLATFORM = "offscreen"
$proc = Start-Process -FilePath $exe -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 12
$alive = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
Remove-Item Env:\QT_QPA_PLATFORM
if (-not $alive) {
    Write-Error "the exe exited on its own, so it does not work. Run it from a console to see why."
}
Stop-Process -Id $proc.Id -Force

# Beyond starting, check the exe still has working https. Nothing in the
# app reaches the network today, but a Qt exclusion once matched Python's
# OpenSSL by name and removed it, and the build looked perfectly fine. This
# is the cheapest guard against that whole class of mistake.
Write-Host "==> Checking https works inside the bundle" -ForegroundColor Cyan
& $python -c @"
import sys
from PyInstaller.archive.readers import CArchiveReader
names = [str(k).replace(chr(92), '/').lower() for k in CArchiveReader(sys.argv[1]).toc.keys()]
need = ['_ssl', 'libcrypto', 'libssl']
missing = [n for n in need if not any(n in b and 'pyside6' not in b for b in names)]
if missing:
    raise SystemExit('the exe is missing ' + ', '.join(missing) + ', so https will not work')
print('    ssl support present')
"@ $exe
if ($LASTEXITCODE -ne 0) { Write-Error "the exe cannot do https" }

if (-not $KeepBuildDir) {
    Remove-Item -Recurse -Force (Join-Path $PSScriptRoot "build") -ErrorAction SilentlyContinue
}

$size = (Get-Item $exe).Length / 1MB
$hash = (Get-FileHash $exe -Algorithm SHA256).Hash
# Published with the release so anyone can check the download arrived
# intact. The exe is unsigned, so this is the only integrity check there
# is. CI computes the same hash on a clean machine and attaches it to the
# release, so the two are worth comparing when a build looks wrong.
Set-Content -Path "$exe.sha256" -Value "$hash *$appName.exe" -Encoding ascii
Write-Host ""
Write-Host ("==> Built {0}  ({1:N1} MB)" -f $exe, $size) -ForegroundColor Green
Write-Host ("    SHA256 {0}" -f $hash) -ForegroundColor Green
