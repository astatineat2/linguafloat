param([string]$AppPython = "", [string]$DocumentPython = "", [switch]$Publish)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $AppPython) { $AppPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe" }
if (-not $DocumentPython) { $DocumentPython = Join-Path $ProjectRoot ".document-runtime\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $AppPython) -or -not (Test-Path -LiteralPath $DocumentPython)) {
    throw "Install the app and document virtual environments first."
}
Push-Location $ProjectRoot
$OriginalPath = $env:PATH
try {
    $env:PATH = (($OriginalPath -split [IO.Path]::PathSeparator) | Where-Object {
        $_ -notmatch '\\native\\poppler\\Library\\bin$'
    }) -join [IO.Path]::PathSeparator
    & $AppPython -m PyInstaller --noconfirm --clean --distpath app-dist --workpath build-documents LinguaFloat.spec
    if ($LASTEXITCODE -ne 0) { throw "Main application build failed." }
    & $AppPython -m PyInstaller --noconfirm --clean --distpath app-dist --workpath build-updater LinguaFloatUpdater.spec
    if ($LASTEXITCODE -ne 0) { throw "Update assistant build failed." }
    Copy-Item -LiteralPath app-dist\LinguaFloatUpdater.exe -Destination app-dist\LinguaFloat\LinguaFloatUpdater.exe -Force
    & $DocumentPython scripts\prepare_document_runtime.py --output app-dist\LinguaFloat\document-runtime
    if ($LASTEXITCODE -ne 0) { throw "Document runtime preparation failed." }
    # Only reviewed public documentation enters a distributable.
    Copy-Item -LiteralPath docs\public -Destination app-dist\LinguaFloat\help -Recurse -Force
    foreach ($PublicFile in @("LICENSE", "THIRD_PARTY_NOTICES.md")) {
        Copy-Item -LiteralPath $PublicFile -Destination app-dist\LinguaFloat -Force
    }
    Copy-Item -LiteralPath LICENSES -Destination app-dist\LinguaFloat -Recurse -Force
    Copy-Item -LiteralPath docs\public\首次使用指南.md -Destination app-dist\LinguaFloat\README.md -Force
    Copy-Item -LiteralPath docs\public\本版更新.md -Destination app-dist\LinguaFloat\CHANGELOG.md -Force
    $ReleaseVersion = (Select-String -LiteralPath src\linguafloat\__init__.py -Pattern '^__version__ = "(.*)"').Matches[0].Groups[1].Value
    [IO.File]::WriteAllText((Join-Path $ProjectRoot 'app-dist\LinguaFloat\VERSION.txt'), "LinguaFloat v$ReleaseVersion`nLaunch: LinguaFloat.exe`n", [Text.UTF8Encoding]::new($false))
    & $AppPython scripts\check_build.py --build-dir build-documents\LinguaFloat --dist-dir app-dist\LinguaFloat
    if ($LASTEXITCODE -ne 0) { throw "Portable build verification failed." }
    if ($Publish) {
        & $AppPython scripts\publish_portable.py --source app-dist\LinguaFloat
        if ($LASTEXITCODE -ne 0) { throw "Portable publication failed." }
    }
}
finally {
    $env:PATH = $OriginalPath
    Pop-Location
}
Write-Host "Verified staging: $ProjectRoot\app-dist\LinguaFloat. Supported launch path: $ProjectRoot\exe\LinguaFloat.exe"
