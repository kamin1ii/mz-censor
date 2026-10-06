# Builds dist\MZCensor.exe and checks that it actually starts.
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
$appName = "MZCensor"

$running = Get-Process $appName -ErrorAction SilentlyContinue
if ($running) { throw "Close MZ Censor before building so unsaved edits are not lost." }

Write-Host "==> Building" -ForegroundColor Cyan
& $python -m PyInstaller --noconfirm --clean mz-censor.spec
if ($LASTEXITCODE -ne 0) { Write-Error "PyInstaller failed" }

$exe = Join-Path $PSScriptRoot "dist\$appName.exe"
if (-not (Test-Path $exe)) { Write-Error "PyInstaller reported success but produced no exe" }

Write-Host "==> Verifying it launches" -ForegroundColor Cyan
# Offscreen so no window appears. The app has no batch mode, so staying
# alive in its event loop is the pass condition and exiting on its own
# means it crashed, usually a module or DLL excluded that was needed.
$env:QT_QPA_PLATFORM = "offscreen"
$proc = Start-Process -FilePath $exe -ArgumentList "--smoke-test" -PassThru -Wait -WindowStyle Hidden
Remove-Item Env:\QT_QPA_PLATFORM
if ($proc.ExitCode -ne 0) { throw "MZ Censor startup self-test failed" }

# Beyond starting, check no TLS stack crept back in. Nothing in the app
# opens a socket, and OpenSSL is 4.6 MB of the download, so it is worth
# 4.6 MB of nothing. It arrives as a dependency of Qt6Network, which is
# thrown away, and only on a machine that has OpenSSL on its PATH. That is
# why v0.7.0 shipped it from CI while a local build looked fine, and why
# this check is repeated in the workflow rather than trusted here.
Write-Host "==> Checking no OpenSSL crept into the bundle" -ForegroundColor Cyan
& $python -c @"
import sys
from PyInstaller.archive.readers import CArchiveReader
names = [str(k).replace(chr(92), '/').lower() for k in CArchiveReader(sys.argv[1]).toc.keys()]
found = sorted(n for n in names if 'libcrypto' in n or 'libssl' in n)
if found:
    raise SystemExit('the exe is carrying ' + ', '.join(found) + ', which nothing uses')
print('    none, as expected')
"@ $exe
if ($LASTEXITCODE -ne 0) { Write-Error "the exe is larger than it needs to be" }

if (-not $KeepBuildDir) {
    $buildTarget = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "build"))
    $expectedBuild = [IO.Path]::GetFullPath($PSScriptRoot) + [IO.Path]::DirectorySeparatorChar + "build"
    if ($buildTarget -ne $expectedBuild) { throw "Unexpected build cleanup path" }
    if (Test-Path -LiteralPath $buildTarget) { Remove-Item -LiteralPath $buildTarget -Recurse -Force }
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
