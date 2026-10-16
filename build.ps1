$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
}
$installedPyInstaller = & $pythonPath -c 'import importlib.metadata; print(importlib.metadata.version("pyinstaller"))' 2>$null
if ($LASTEXITCODE -ne 0 -or $installedPyInstaller -ne '6.22.3') {
    & $pythonPath -m pip install --disable-pip-version-check 'pyinstaller==6.22.3'
    if ($LASTEXITCODE -ne 0) { throw 'Build dependency installation failed' }
}
$toolVersion = (& $pythonPath -c 'from wuwa_uhd import __version__; print(__version__)').Trim()
if ($LASTEXITCODE -ne 0 -or $toolVersion -notmatch '^\d+\.\d+\.\d+(?:-[a-z0-9.]+)?$') { throw 'Invalid build version' }
$versionOutput = Join-Path $PSScriptRoot ('dist\v' + $toolVersion)
$reportOutput = Join-Path $PSScriptRoot ('build\v' + $toolVersion)
New-Item -ItemType Directory -Force -Path $versionOutput, $reportOutput | Out-Null
& $pythonPath -X utf8 main.py --self-test --report (Join-Path $reportOutput 'source-test-report.json')
if ($LASTEXITCODE -ne 0) { throw 'Acceptance tests failed' }
& $pythonPath -m PyInstaller --noconfirm --onefile --windowed --name WuwaUHDTool --distpath $versionOutput --workpath (Join-Path $reportOutput 'pyinstaller') --icon wuwa_uhd/data/app.ico --add-data 'wuwa_uhd/data;wuwa_uhd/data' main.py
if ($LASTEXITCODE -ne 0) { throw 'Packaging failed' }
$exePath = Join-Path $versionOutput 'WuwaUHDTool.exe'
$selfReport = Join-Path $reportOutput 'frozen-test-report.json'
$uiReport = Join-Path $reportOutput 'frozen-ui-report.json'
$selfProcess = Start-Process -FilePath $exePath -ArgumentList @('--self-test', '--report', ('"' + $selfReport + '"')) -WindowStyle Hidden -Wait -PassThru
if ($selfProcess.ExitCode -ne 0) { throw 'Packaged EXE acceptance tests failed' }
$uiProcess = Start-Process -FilePath $exePath -ArgumentList @('--ui-smoke-test', '--report', ('"' + $uiReport + '"')) -WindowStyle Hidden -Wait -PassThru
if ($uiProcess.ExitCode -ne 0) { throw 'Packaged GUI smoke test failed' }
& $pythonPath -X utf8 scripts/package_release.py --exe $exePath --output $versionOutput
if ($LASTEXITCODE -ne 0) { throw 'Release archive validation failed' }
