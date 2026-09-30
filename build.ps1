$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
}
& $pythonPath -m pip install --disable-pip-version-check 'pyinstaller==6.22.3'
if ($LASTEXITCODE -ne 0) { throw 'Build dependency installation failed' }
& $pythonPath main.py --self-test --report test-report.json
if ($LASTEXITCODE -ne 0) { throw 'Acceptance tests failed' }
& $pythonPath -m PyInstaller --noconfirm --onefile --windowed --name WuwaUHDTool --icon wuwa_uhd/data/app.ico --add-data 'wuwa_uhd/data;wuwa_uhd/data' main.py
if ($LASTEXITCODE -ne 0) { throw 'Packaging failed' }
$exePath = Join-Path $PSScriptRoot 'dist\WuwaUHDTool.exe'
$selfReport = Join-Path $PSScriptRoot 'frozen-test-report.json'
$uiReport = Join-Path $PSScriptRoot 'frozen-ui-report.json'
$selfProcess = Start-Process -FilePath $exePath -ArgumentList @('--self-test', '--report', ('"' + $selfReport + '"')) -WindowStyle Hidden -Wait -PassThru
if ($selfProcess.ExitCode -ne 0) { throw 'Packaged EXE acceptance tests failed' }
$uiProcess = Start-Process -FilePath $exePath -ArgumentList @('--ui-smoke-test', '--report', ('"' + $uiReport + '"')) -WindowStyle Hidden -Wait -PassThru
if ($uiProcess.ExitCode -ne 0) { throw 'Packaged GUI smoke test failed' }
& $pythonPath scripts/package_release.py
if ($LASTEXITCODE -ne 0) { throw 'Release archive validation failed' }
